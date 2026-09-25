"""Synthetic fixtures for Codex desktop 26.915.4065 catalog auxiliaries."""
import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cleaner.demo import IDS, create_demo
from cleaner.store import CleanupError, Store
from tests.test_cleaner import sql


def add_catalog_auxiliaries(path):
    with contextlib.closing(sqlite3.connect(path)) as con:
        con.executescript('''
            CREATE TABLE inbox_items(id TEXT PRIMARY KEY, title TEXT, description TEXT,
                thread_id TEXT, read_at INTEGER, created_at INTEGER);
            CREATE TABLE automation_runs(thread_id TEXT PRIMARY KEY, automation_id TEXT NOT NULL,
                status TEXT NOT NULL, read_at INTEGER, thread_title TEXT, source_cwd TEXT,
                inbox_title TEXT, inbox_summary TEXT, created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL, archived_user_message TEXT,
                archived_assistant_message TEXT, archived_reason TEXT);
            CREATE TABLE live_visualization_suggestions(account_id TEXT NOT NULL, user_id TEXT NOT NULL,
                host_id TEXT NOT NULL, thread_id TEXT NOT NULL, id TEXT NOT NULL, title TEXT NOT NULL,
                description TEXT NOT NULL, status TEXT NOT NULL, submission TEXT,
                PRIMARY KEY(account_id,user_id,host_id,thread_id)) WITHOUT ROWID;
            CREATE TABLE automations(id TEXT PRIMARY KEY, name TEXT, prompt TEXT);
            INSERT INTO automations VALUES('automation-keep','Keep schedule','Keep prompt');
        ''')
        for target in (IDS[1], IDS[2]):
            con.execute('INSERT INTO inbox_items VALUES(?,?,?,?,?,?)',
                        ('inbox-'+target,'Synthetic inbox','Summary',target,None,10))
            con.execute('INSERT INTO automation_runs(thread_id,automation_id,status,created_at,updated_at) VALUES(?,?,?,?,?)',
                        (target,'automation-keep','ACCEPTED',10,10))
            for host in ('local','remote:test','chatgpt:test'):
                con.execute('INSERT INTO live_visualization_suggestions VALUES(?,?,?,?,?,?,?,?,?)',
                            ('account-test','user-test',host,target,'visual-'+target,'Title','Description','pending',None))
        # An inbox item ID is not a thread ID, and NULL-owned inbox items are unrelated.
        con.execute('INSERT INTO inbox_items(id,thread_id) VALUES(?,?)',(IDS[1],IDS[2]))
        con.execute('INSERT INTO inbox_items(id,thread_id) VALUES(?,NULL)',('unrelated-inbox',))
        con.commit()


class CodexCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cleaner-catalog-tests-')
        self.root=Path(self.temp.name);create_demo(self.root)
        self.db=self.root/'sqlite/codex-dev.db'
        add_catalog_auxiliaries(self.db)
        self.store=Store(self.root,process_provider=lambda:[],rpc_factory=None)

    def tearDown(self):
        self.temp.cleanup()

    def test_selected_auxiliaries_removed_and_unrelated_content_preserved(self):
        remote_before=sql(self.db,"SELECT * FROM live_visualization_suggestions WHERE host_id<>'local' ORDER BY host_id,thread_id")
        definitions=sql(self.db,'SELECT * FROM automations')
        result=self.store.apply(self.store.preview([IDS[1]],backup={'mode':'none'}))
        self.assertEqual(result['remaining'],3)
        self.assertEqual(result['rows_removed']['catalog.inbox_items'],1)
        self.assertEqual(result['rows_removed']['catalog.automation_runs'],1)
        self.assertEqual(result['rows_removed']['catalog.live_visualization_suggestions'],1)
        self.assertEqual(sql(self.db,'SELECT thread_id FROM automation_runs'),[(IDS[2],)])
        self.assertEqual(sql(self.db,'SELECT thread_id FROM inbox_items WHERE id=?',(IDS[1],)),[(IDS[2],)])
        self.assertEqual(sql(self.db,"SELECT count(*) FROM inbox_items WHERE id='unrelated-inbox'"),[(1,)])
        self.assertEqual(sql(self.db,"SELECT * FROM live_visualization_suggestions WHERE host_id<>'local' ORDER BY host_id,thread_id"),remote_before)
        self.assertEqual(sql(self.db,'SELECT * FROM automations'),definitions)
        self.assertEqual(sql(self.db,'PRAGMA integrity_check'),[('ok',)])

    def test_ambiguous_hostless_records_block_before_mutation(self):
        sql(self.db,'INSERT INTO inbox_items(id,thread_id) VALUES(?,?)',('ambiguous',IDS[0]))
        before=sql(self.db,'SELECT * FROM inbox_items ORDER BY id')
        with self.assertRaisesRegex(CleanupError,'归属'):
            self.store.preview([IDS[0]])
        self.assertEqual(sql(self.db,'SELECT * FROM inbox_items ORDER BY id'),before)
        self.assertEqual(len(self.store.inventory()['rows']),4)

    def test_auxiliary_changes_invalidate_preview(self):
        plan=self.store.preview([IDS[1]])
        sql(self.db,'UPDATE inbox_items SET description=? WHERE thread_id=?',('Changed',IDS[1]))
        with self.assertRaisesRegex(CleanupError,'变化'):
            self.store.apply(plan)
        self.assertEqual(len(self.store.inventory()['rows']),4)

    def test_failure_restores_auxiliaries_with_backup(self):
        before={table:sql(self.db,'SELECT * FROM '+table) for table in
                ('inbox_items','automation_runs','live_visualization_suggestions','automations')}
        with tempfile.TemporaryDirectory() as outside,patch.object(self.store,'prune_files',side_effect=OSError('Synthetic failure')):
            with self.assertRaisesRegex(CleanupError,'rolled_back'):
                self.store.apply(self.store.preview([IDS[1]],backup={'mode':'custom','directory':outside}))
        for table,rows in before.items():
            self.assertEqual(sql(self.db,'SELECT * FROM '+table),rows)

    def test_unknown_tables_block_at_preview_even_when_empty(self):
        sql(self.db,'CREATE TABLE future_inbox(thread_id TEXT)')
        with self.assertRaisesRegex(CleanupError,'未适配'):
            self.store.preview([IDS[1]])

    def test_changed_host_scope_is_not_silently_ignored(self):
        sql(self.db,'ALTER TABLE inbox_items ADD COLUMN host_id TEXT')
        with self.assertRaisesRegex(CleanupError,'结构'):
            self.store.preview([IDS[1]])


if __name__=='__main__':
    unittest.main()
