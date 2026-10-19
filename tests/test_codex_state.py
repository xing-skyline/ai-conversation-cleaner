"""Synthetic fixtures matching the installed Codex state auxiliary schemas."""
import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cleaner.demo import IDS, create_demo
from cleaner.store import CleanupError, Store
from tests.test_cleaner import sql


STATE_AUXILIARIES = ('thread_read_receipts', 'guardian_review_feedback')


def add_state_auxiliaries(path):
    with contextlib.closing(sqlite3.connect(path)) as con:
        con.executescript('''
            CREATE TABLE thread_read_receipts (
                thread_id TEXT PRIMARY KEY NOT NULL,
                first_unread_turn TEXT,
                revision TEXT NOT NULL,
                last_published_turn TEXT
            );
            CREATE TABLE guardian_review_feedback (
                id TEXT PRIMARY KEY NOT NULL,
                thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                record BLOB NOT NULL
            );
        ''')
        for target in IDS[:3]:
            con.execute('INSERT INTO thread_read_receipts VALUES(?,?,?,?)',
                        (target, None, 'revision-1', IDS[0]))
            con.execute('INSERT INTO guardian_review_feedback VALUES(?,?,?)',
                        ('review-'+target, target, b'synthetic feedback'))
        con.execute('INSERT INTO guardian_review_feedback VALUES(?,?,?)',
                    ('second-review', IDS[0], b'second feedback'))
        # A feedback record ID and its opaque payload do not establish ownership.
        con.execute('INSERT INTO guardian_review_feedback VALUES(?,?,?)',
                    (IDS[0], IDS[1], IDS[0].encode()))
        con.commit()


class CodexStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cleaner-state-tests-')
        self.root = Path(self.temp.name)
        create_demo(self.root)
        self.db = self.root/'state_5.sqlite'
        add_state_auxiliaries(self.db)
        self.store = Store(self.root, process_provider=lambda: [], rpc_factory=None)

    def tearDown(self):
        self.temp.cleanup()

    def test_selected_auxiliaries_removed_and_other_threads_preserved(self):
        kept = {table: sql(self.db, 'SELECT * FROM '+table+' WHERE thread_id<>?', (IDS[0],))
                for table in STATE_AUXILIARIES}
        result = self.store.apply(self.store.preview([IDS[0]], backup={'mode': 'none'}))
        self.assertEqual(result['remaining'], 3)
        self.assertEqual(result['rows_removed']['state.thread_read_receipts'], 1)
        self.assertEqual(result['rows_removed']['state.guardian_review_feedback'], 2)
        for table, rows in kept.items():
            self.assertEqual(sql(self.db, 'SELECT * FROM '+table), rows)
        self.assertEqual(sql(self.db, 'PRAGMA foreign_key_check'), [])
        self.assertEqual(sql(self.db, 'PRAGMA integrity_check'), [('ok',)])

    def test_empty_auxiliaries_do_not_block_cleanup(self):
        for table in STATE_AUXILIARIES:
            sql(self.db, 'DELETE FROM '+table)
        result = self.store.apply(self.store.preview([IDS[0]]))
        for table in STATE_AUXILIARIES:
            self.assertEqual(result['rows_removed']['state.'+table], 0)

    def test_older_state_without_auxiliaries_remains_supported(self):
        for table in STATE_AUXILIARIES:
            sql(self.db, 'DROP TABLE '+table)
        result = self.store.apply(self.store.preview([IDS[0]]))
        self.assertEqual(result['deleted'], 1)

    def test_failure_restores_auxiliaries_with_backup(self):
        before = {table: sql(self.db, 'SELECT * FROM '+table) for table in STATE_AUXILIARIES}
        with tempfile.TemporaryDirectory() as outside, patch.object(
            self.store, 'prune_files', side_effect=OSError('Synthetic failure')
        ):
            with self.assertRaisesRegex(CleanupError, 'rolled_back'):
                self.store.apply(self.store.preview(
                    [IDS[0]], backup={'mode': 'custom', 'directory': outside}))
        for table, rows in before.items():
            self.assertEqual(sql(self.db, 'SELECT * FROM '+table), rows)
        self.assertEqual(len(self.store.inventory()['rows']), 4)
        self.assertEqual(sql(self.db, 'PRAGMA foreign_key_check'), [])

    def test_missing_ownership_column_blocks_preview_without_writes(self):
        for table in STATE_AUXILIARIES:
            with self.subTest(table=table):
                sql(self.db, 'ALTER TABLE '+table+' RENAME COLUMN thread_id TO owner_id')
                before = self.db.read_bytes()
                try:
                    with self.assertRaisesRegex(CleanupError, 'state\\.'+table):
                        self.store.preview([IDS[0]])
                    self.assertEqual(self.db.read_bytes(), before)
                finally:
                    sql(self.db, 'ALTER TABLE '+table+' RENAME COLUMN owner_id TO thread_id')

    def test_unknown_related_table_still_blocks_preview_without_writes(self):
        sql(self.db, 'CREATE TABLE future_feedback(thread_id TEXT)')
        before = self.db.read_bytes()
        with self.assertRaisesRegex(CleanupError, 'state\\.future_feedback'):
            self.store.preview([IDS[0]])
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
