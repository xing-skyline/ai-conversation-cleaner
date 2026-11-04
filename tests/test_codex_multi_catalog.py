"""Synthetic regressions for coexisting Codex desktop catalogs."""
import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cleaner.demo import IDS, create_demo
from cleaner.store import CleanupError, Store
from tests.test_cleaner import sql
from tests.test_codex_catalog import add_catalog_auxiliaries


def copy_database(source, destination):
    with contextlib.closing(sqlite3.connect(source)) as src, contextlib.closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)


class CodexMultiCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cleaner-multi-catalog-')
        self.root = Path(self.temp.name)
        create_demo(self.root)
        self.dev = self.root / 'sqlite/codex-dev.db'
        self.stable = self.root / 'sqlite/codex.db'
        add_catalog_auxiliaries(self.dev)
        copy_database(self.dev, self.stable)
        # Each database owns its own local-host identity.
        for table in ('local_thread_catalog_hosts', 'local_thread_catalog',
                      'thread_timeline_ledger', 'local_thread_catalog_scan_entries',
                      'live_visualization_suggestions'):
            sql(self.stable, 'UPDATE ' + table + " SET host_id='desktop' WHERE host_id='local'")
        self.store = Store(self.root, process_provider=lambda: [], rpc_factory=None)

    def tearDown(self):
        self.temp.cleanup()

    def test_inventory_unions_catalogs_and_deduplicates_local_and_cloud_rows(self):
        sql(self.dev, 'INSERT INTO local_thread_catalog VALUES(?,?,?,?,?)',
            ('local', IDS[4], 'Dev-only', 10, ''))
        sql(self.stable, 'INSERT INTO local_thread_catalog VALUES(?,?,?,?,?)',
            ('desktop', IDS[5], 'Stable-only', 20, ''))
        inventory = self.store.inventory()
        self.assertEqual({row['id'] for row in inventory['rows']}, set(IDS))
        self.assertEqual(len(inventory['rows']), 6)
        self.assertEqual(inventory['chatgpt'], 1)
        self.assertEqual(set(inventory['databases'].values()) & {str(self.dev), str(self.stable)},
                         {str(self.dev), str(self.stable)})

    def test_latest_catalog_title_wins_in_either_database(self):
        for path, host, title in ((self.stable, 'desktop', 'Stable newer'), (self.dev, 'local', 'Dev newer')):
            with self.subTest(path=path):
                sql(path, 'UPDATE local_thread_catalog SET display_title=?,source_updated_at=? WHERE host_id=? AND thread_id=?',
                    (title, 9000000000 if path == self.stable else 9000000001, host, IDS[1]))
                row = next(row for row in self.store.inventory()['rows'] if row['id'] == IDS[1])
                self.assertEqual(row['title'], title)

    def test_delete_cleans_both_catalogs_and_preserves_other_hosts_and_tasks(self):
        before = {}
        for path, host in ((self.dev, 'local'), (self.stable, 'desktop')):
            before[path] = {
                'catalog': sql(path, 'SELECT * FROM local_thread_catalog WHERE NOT(host_id=? AND thread_id=?) ORDER BY host_id,thread_id', (host, IDS[1])),
                'remote': sql(path, 'SELECT * FROM live_visualization_suggestions WHERE host_id<>? ORDER BY host_id,thread_id', (host,)),
                'automations': sql(path, 'SELECT * FROM automations'),
                'active': sql(path, 'SELECT * FROM active_chat_turns WHERE thread_id<>?', (IDS[1],)),
            }
        result = self.store.apply(self.store.preview([IDS[1]], backup={'mode': 'none'}))
        self.assertEqual(result['remaining'], 3)
        self.assertEqual(result['chatgpt_before'], 1)
        self.assertEqual(result['chatgpt_after'], 1)
        for path, host in ((self.dev, 'local'), (self.stable, 'desktop')):
            self.assertEqual(sql(path, 'SELECT * FROM local_thread_catalog ORDER BY host_id,thread_id'), before[path]['catalog'])
            self.assertEqual(sql(path, 'SELECT * FROM live_visualization_suggestions WHERE host_id<>? ORDER BY host_id,thread_id', (host,)), before[path]['remote'])
            self.assertEqual(sql(path, 'SELECT * FROM automations'), before[path]['automations'])
            self.assertEqual(sql(path, 'SELECT * FROM active_chat_turns'), before[path]['active'])
            for table in ('inbox_items', 'automation_runs', 'active_chat_turns'):
                self.assertEqual(sql(path, 'SELECT count(*) FROM '+table+' WHERE thread_id=?', (IDS[1],)), [(0,)])
            for table in ('thread_timeline_ledger', 'local_thread_catalog_scan_entries', 'live_visualization_suggestions'):
                self.assertEqual(sql(path, 'SELECT count(*) FROM '+table+' WHERE host_id=? AND thread_id=?', (host, IDS[1])), [(0,)])
            self.assertEqual(sql(path, 'SELECT catalog_revision FROM local_thread_catalog_metadata'), [(2,)])
            self.assertEqual(sql(path, 'PRAGMA integrity_check'), [('ok',)])
        self.assertTrue((self.root/'sessions'/f'rollout-2026-09-22-{IDS[0]}.jsonl').exists())
        self.assertFalse((self.root/'sessions'/f'rollout-2026-09-22-{IDS[1]}.jsonl').exists())

    def test_changes_in_either_catalog_invalidate_preview(self):
        for path in (self.dev, self.stable):
            with self.subTest(path=path):
                plan = self.store.preview([IDS[1]])
                sql(path, 'UPDATE local_thread_catalog_metadata SET catalog_revision=catalog_revision+1')
                with self.assertRaisesRegex(CleanupError, '变化'):
                    self.store.apply(plan)
        self.assertEqual(len(self.store.inventory()['rows']), 4)

    def test_adding_or_removing_a_catalog_invalidates_preview(self):
        self.stable.unlink()
        plan = self.store.preview([IDS[1]])
        copy_database(self.dev, self.stable)
        with self.assertRaisesRegex(CleanupError, '变化'):
            self.store.apply(plan)
        plan = self.store.preview([IDS[1]])
        self.stable.unlink()
        with self.assertRaisesRegex(CleanupError, '变化'):
            self.store.apply(plan)

    def test_unknown_schema_in_either_catalog_blocks_preview(self):
        for path in (self.dev, self.stable):
            with self.subTest(path=path):
                sql(path, 'CREATE TABLE future_catalog_data(thread_id TEXT)')
                with self.assertRaisesRegex(CleanupError, '未适配'):
                    self.store.preview([IDS[1]])
                sql(path, 'DROP TABLE future_catalog_data')

    def test_ambiguous_hostless_rows_in_either_catalog_block_preview(self):
        for path in (self.dev, self.stable):
            with self.subTest(path=path):
                sql(path, 'INSERT INTO inbox_items(id,thread_id) VALUES(?,?)', ('ambiguous', IDS[0]))
                with self.assertRaisesRegex(CleanupError, '归属'):
                    self.store.preview([IDS[0]])
                sql(path, "DELETE FROM inbox_items WHERE id='ambiguous'")

    def test_backup_and_failure_restore_both_catalogs(self):
        before = {path: sql(path, 'SELECT * FROM local_thread_catalog ORDER BY host_id,thread_id')
                  for path in (self.dev, self.stable)}
        active_before = {path: sql(path, 'SELECT * FROM active_chat_turns')
                         for path in (self.dev, self.stable)}
        with tempfile.TemporaryDirectory() as outside, patch.object(self.store, 'prune_files', side_effect=OSError('Synthetic failure')):
            with self.assertRaisesRegex(CleanupError, 'rolled_back'):
                self.store.apply(self.store.preview([IDS[1]], backup={'mode': 'custom', 'directory': outside}))
            self.assertEqual(len(list(Path(outside).rglob('codex.db'))), 1)
            self.assertEqual(len(list(Path(outside).rglob('codex-dev.db'))), 1)
        for path in (self.dev, self.stable):
            self.assertEqual(sql(path, 'SELECT * FROM local_thread_catalog ORDER BY host_id,thread_id'), before[path])
            self.assertEqual(sql(path, 'SELECT * FROM active_chat_turns'), active_before[path])
            self.assertEqual(sql(path, 'SELECT count(*) FROM automation_runs WHERE thread_id=?', (IDS[1],)), [(1,)])
            self.assertEqual(sql(path, 'SELECT catalog_revision FROM local_thread_catalog_metadata'), [(1,)])

    def test_unrecognized_extra_catalog_still_blocks(self):
        copy_database(self.dev, self.root/'sqlite/unknown.db')
        with self.assertRaisesRegex(CleanupError, '多个 catalog'):
            self.store.inventory()

    def test_multiple_state_versions_still_block(self):
        copy_database(self.root/'state_5.sqlite', self.root/'state_6.sqlite')
        with self.assertRaisesRegex(CleanupError, '多个 state'):
            self.store.inventory()


if __name__ == '__main__':
    unittest.main()
