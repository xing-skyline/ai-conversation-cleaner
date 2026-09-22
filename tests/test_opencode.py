import contextlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cleaner.providers import make_providers
from cleaner.opencode import OpenCodeProvider
from cleaner.store import CleanupError
from tests.test_cleaner import put, sql

SELECTED, KEPT, CHILD = 'ses_selected123', 'ses_kept456', 'ses_child789'
RELATED = ('todo', 'session_share', 'session_message', 'session_input', 'session_context_epoch')


def create_opencode(profile):
    root = profile/'.local/share/opencode'
    root.mkdir(parents=True)
    db = root/'opencode.db'
    with contextlib.closing(sqlite3.connect(db)) as con:
        con.executescript('''
            CREATE TABLE project(id TEXT PRIMARY KEY, worktree TEXT);
            CREATE TABLE account(id TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE session(id TEXT PRIMARY KEY, title TEXT, directory TEXT,
                time_updated INTEGER, time_archived INTEGER, parent_id TEXT,
                project_id TEXT REFERENCES project(id));
            CREATE TABLE message(id TEXT PRIMARY KEY, session_id TEXT REFERENCES session(id) ON DELETE CASCADE,
                time_created INTEGER, data TEXT);
            CREATE TABLE part(id TEXT PRIMARY KEY, message_id TEXT REFERENCES message(id) ON DELETE CASCADE,
                session_id TEXT, time_created INTEGER, data TEXT);
            CREATE TABLE event_sequence(aggregate_id TEXT PRIMARY KEY, seq INTEGER);
            CREATE TABLE event(id TEXT PRIMARY KEY, aggregate_id TEXT REFERENCES event_sequence(aggregate_id) ON DELETE CASCADE,
                seq INTEGER, type TEXT, data TEXT);
            INSERT INTO project VALUES('project_fixture', 'C:/Example');
            INSERT INTO account VALUES('account_fixture', 'synthetic credential; preserve me');
        ''')
        for table in RELATED:
            con.execute(f'CREATE TABLE {table}(session_id TEXT REFERENCES session(id) ON DELETE CASCADE, data TEXT)')
        for target in (SELECTED, KEPT):
            con.execute('INSERT INTO session VALUES(?,?,?,?,?,?,?)',
                        (target, 'OpenCode '+target, 'C:/Example', 1800000000000, 1 if target == KEPT else None, None, 'project_fixture'))
            con.execute('INSERT INTO message VALUES(?,?,?,?)', ('msg_'+target, target, 1, json.dumps({'role':'user'})))
            con.execute('INSERT INTO part VALUES(?,?,?,?,?)',
                        ('prt_'+target, 'msg_'+target, target, 1, json.dumps({'type':'text','text':'Hello '+target})))
            for table in RELATED:
                con.execute(f'INSERT INTO {table} VALUES(?,?)', (target, '{}'))
            con.execute('INSERT INTO event_sequence VALUES(?,?)', (target, 0))
            con.execute('INSERT INTO event VALUES(?,?,?,?,?)', ('evt_'+target, target, 0, 'session.created', '{}'))
        con.execute('INSERT INTO event_sequence VALUES(?,?)', ('project_fixture', 0))
        con.execute('INSERT INTO event VALUES(?,?,?,?,?)', ('evt_project', 'project_fixture', 0, 'project.updated', '{}'))
        con.commit()
    for target in (SELECTED, KEPT):
        put(root/'storage/session_diff'/(target+'.json'), [{'fixture':True}])
    put(root/'auth.json', {'fixture':'leave unchanged'})
    put(root/'repos/project_fixture/source.json', {'project':'leave unchanged'})
    return root, db


class OpenCodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='opencode-cleaner-tests-')
        self.profile = Path(self.temp.name)
        self.root, self.db = create_opencode(self.profile)

    def tearDown(self):
        self.temp.cleanup()

    def provider(self):
        providers = make_providers(profile=self.profile)
        self.assertIn('opencode', providers, 'OpenCode must be available as an application tab')
        store = providers['opencode']
        store.process_provider = lambda: []
        return store

    def test_inventory_detail_and_selected_delete_preserve_unselected_and_accounts(self):
        store = self.provider()
        self.assertEqual({r['id'] for r in store.inventory()['rows']}, {SELECTED, KEPT})
        self.assertTrue(next(r for r in store.inventory()['rows'] if r['id']==KEPT)['archived'])
        self.assertEqual(store.detail(SELECTED)['messages'][0]['text'], 'Hello '+SELECTED)
        result = store.apply(store.preview([SELECTED], backup={'mode':'none'}))
        self.assertEqual(result['remaining'], 1)
        self.assertEqual(result['backups'], [])
        for table in (*RELATED, 'message', 'part'):
            self.assertEqual(sql(self.db, f'SELECT session_id FROM {table}'), [(KEPT,)])
        self.assertEqual(sql(self.db, 'SELECT id FROM session'), [(KEPT,)])
        self.assertEqual({r[0] for r in sql(self.db, 'SELECT aggregate_id FROM event')}, {KEPT, 'project_fixture'})
        self.assertEqual({r[0] for r in sql(self.db, 'SELECT aggregate_id FROM event_sequence')}, {KEPT, 'project_fixture'})
        self.assertEqual(sql(self.db, 'SELECT value FROM account'), [('synthetic credential; preserve me',)])
        self.assertEqual(sql(self.db, 'SELECT id FROM project'), [('project_fixture',)])
        self.assertTrue((self.root/'auth.json').is_file())
        self.assertTrue((self.root/'repos/project_fixture/source.json').is_file())
        self.assertFalse((self.root/'storage/session_diff'/(SELECTED+'.json')).exists())
        self.assertTrue((self.root/'storage/session_diff'/(KEPT+'.json')).exists())

    def test_parent_requires_explicit_child_selection(self):
        sql(self.db, 'INSERT INTO session VALUES(?,?,?,?,?,?,?)', (CHILD,'child','C:/Example',1,None,SELECTED,'project_fixture'))
        store = self.provider()
        with self.assertRaisesRegex(CleanupError, '子会话|派生'):
            store.preview([SELECTED])
        self.assertEqual(store.apply(store.preview([SELECTED,CHILD]))['remaining'], 1)

    def test_unknown_related_table_blocks_before_writes(self):
        sql(self.db, 'CREATE TABLE future_session_payload(session_id TEXT, value TEXT)')
        store = self.provider()
        with self.assertRaisesRegex(CleanupError, '未适配|结构'):
            store.apply(store.preview([SELECTED]))
        self.assertEqual(sql(self.db, 'SELECT count(*) FROM session'), [(2,)])

    def test_default_and_custom_backups_and_rollback(self):
        store = self.provider()
        original = store.snapshot
        failed = False
        def fail_after_deletion():
            nonlocal failed
            snap = original()
            if not failed and SELECTED not in {r['id'] for r in snap['rows']}:
                failed = True
                raise OSError('injected verification failure')
            return snap
        with patch.object(store, 'snapshot', side_effect=fail_after_deletion):
            with self.assertRaisesRegex(CleanupError, 'rolled_back'):
                store.apply(store.preview([SELECTED], backup={'mode':'custom','directory':str(self.profile/'chosen')}))
        self.assertEqual({r['id'] for r in store.inventory()['rows']}, {SELECTED, KEPT})
        self.assertTrue((self.root/'storage/session_diff'/(SELECTED+'.json')).exists())
        result = store.apply(store.preview([SELECTED]))
        self.assertTrue(Path(result['backup_dir'],'result.json').is_file())

    def test_running_and_stale_previews_block(self):
        store = self.provider()
        plan = store.preview([SELECTED])
        store.process_provider = lambda: [{'pid':123,'name':'opencode.exe'}]
        with self.assertRaisesRegex(CleanupError, '退出'):
            store.apply(plan)
        store.process_provider = lambda: []
        sql(self.db, 'UPDATE session SET title=? WHERE id=?', ('changed',KEPT))
        with self.assertRaisesRegex(CleanupError, '变化'):
            store.apply(plan)

    def test_legacy_json_and_orphan_diff_are_removable(self):
        legacy = 'ses_legacy123'
        put(self.root/'storage/session/project_fixture'/(legacy+'.json'),
            {'id':legacy,'title':'Legacy fixture','directory':'C:/Example','time':{'updated':1}})
        put(self.root/'storage/message'/legacy/'msg_legacy.json', {'id':'msg_legacy','sessionID':legacy,'role':'user'})
        put(self.root/'storage/part/msg_legacy/prt_legacy.json',
            {'id':'prt_legacy','sessionID':legacy,'messageID':'msg_legacy','type':'text','text':'Legacy hello'})
        orphan = 'ses_orphan123'
        put(self.root/'storage/session_diff'/(orphan+'.json'), [])
        store = self.provider()
        self.assertEqual(store.detail(legacy)['messages'][0]['text'], 'Legacy hello')
        result = store.apply(store.preview([legacy,orphan], backup={'mode':'none'}))
        self.assertEqual(result['remaining'], 2)
        self.assertFalse((self.root/'storage/part/msg_legacy/prt_legacy.json').exists())

    def test_legacy_ownership_mismatch_blocks(self):
        put(self.root/'storage/message'/SELECTED/'msg_mismatch.json', {'id':'msg_mismatch','sessionID':KEPT})
        store = self.provider()
        with self.assertRaisesRegex(CleanupError, '归属|不一致'):
            store.preview([SELECTED])

    def test_explicit_xdg_and_database_paths_are_respected(self):
        with patch.dict(os.environ, {'XDG_DATA_HOME':str(self.profile/'xdg'),'OPENCODE_DB':str(self.db)}):
            store = OpenCodeProvider(process_provider=lambda: [])
            self.assertEqual(store.home, (self.profile/'xdg/opencode').resolve())
            self.assertEqual(store.database, self.db.resolve())
            self.assertEqual({r['id'] for r in store.snapshot()['rows']}, {SELECTED, KEPT})

    def test_no_backup_failure_reports_irrecoverable_changes(self):
        store = self.provider()
        original = store.snapshot
        def fail_after_deletion():
            snap = original()
            if SELECTED not in {r['id'] for r in snap['rows']}:
                raise OSError('injected no-backup verification failure')
            return snap
        with patch.object(store, 'snapshot', side_effect=fail_after_deletion):
            with self.assertRaisesRegex(CleanupError, 'failed_no_backup'):
                store.apply(store.preview([SELECTED], backup={'mode':'none'}))
        self.assertEqual({r['id'] for r in store.snapshot()['rows']}, {KEPT})


if __name__ == '__main__':
    unittest.main()
