"""Synthetic Antigravity 2.19 stores; no installed profile is changed."""
import contextlib
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cleaner.demo import IDS
from cleaner.providers import LocalProvider
from cleaner import processes
from cleaner.protobuf import fields
from cleaner.store import CleanupError
from tests.test_cleaner import pb, sql


def hub(ids):
    return b''.join(pb(1, pb(1, target.encode()) + pb(2, pb(1, ('Chat ' + target[:8]).encode())))
                    for target in ids)


def hub_ids(path):
    return {value.decode() for number, wire, entry, _ in fields(path.read_bytes())
            if number == 1 and wire == 2
            for number, wire, value, _ in fields(entry) if number == 1 and wire == 2}


def session(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    sql(path, 'CREATE TABLE trajectory_meta(trajectory_id TEXT PRIMARY KEY,cascade_id TEXT)')
    sql(path, 'INSERT INTO trajectory_meta VALUES(?,?)', (IDS[4], target))
    sql(path, 'CREATE TABLE steps(idx INTEGER PRIMARY KEY,step_payload BLOB)')
    sql(path, 'INSERT INTO steps VALUES(0,?)', (b'opaque conversation body',))


class AntigravityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ai-cleaner-antigravity-tests-')
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)
        self.store = LocalProvider('antigravity', self.profile, process_provider=lambda: [])
        self.root, self.ide, _ = self.store.roots
        self.root.mkdir(parents=True)
        self.summary = self.root/'conversation_summaries.db'
        self.hub = self.root/'agyhub_summaries_proto.pb'

    def fixture(self):
        sql(self.summary, '''CREATE TABLE conversation_summaries(
            conversation_id TEXT PRIMARY KEY,title TEXT,preview TEXT,
            last_modified_time TEXT,workspace_uris TEXT)''')
        for target in IDS[:2]:
            sql(self.summary, 'INSERT INTO conversation_summaries VALUES(?,?,?,?,?)',
                (target, 'Chat', 'Preview', '2026-10-01T00:00:00Z', '[]'))
            session(self.root/'conversations'/(target+'.db'), target)
        self.hub.write_bytes(hub(IDS[:2]) + pb(12, b'unknown outer field'))

    def test_delete_body_hub_summary_and_exact_sidecars(self):
        self.fixture()
        selected, keep = IDS[:2]
        db = self.root/'conversations'/(selected+'.db')
        # Empty SQLite sidecars can survive an unclean application shutdown.
        for suffix in ('-wal', '-shm'):
            Path(str(db)+suffix).touch()
        unrelated = db.with_name(selected+'.db.backup')
        unrelated.write_bytes(b'keep historical backup')
        other = self.root/'conversations'/(keep+'.db')
        original = other.read_bytes()
        result = self.store.apply(self.store.preview([selected]))
        self.assertEqual(result['remaining'], 1)
        self.assertFalse(db.exists(), 'Reported success while the conversation body survived')
        self.assertFalse(Path(str(db)+'-wal').exists())
        self.assertFalse(Path(str(db)+'-shm').exists())
        self.assertEqual(hub_ids(self.hub), {keep})
        self.assertEqual(self.hub.read_bytes(), hub([keep])+pb(12, b'unknown outer field'))
        self.assertEqual(sql(self.summary, 'SELECT conversation_id FROM conversation_summaries'), [(keep,)])
        self.assertEqual(other.read_bytes(), original)
        self.assertEqual(unrelated.read_bytes(), b'keep historical backup')

    def test_body_only_orphans_are_found_in_both_roots(self):
        for base, target in zip((self.root, self.ide), IDS[:2]):
            session(base/'conversations'/(target+'.db'), target)
        self.assertEqual({row['id'] for row in self.store.inventory()['rows']}, set(IDS[:2]))
        self.store.apply(self.store.preview(list(IDS[:2])))
        self.assertFalse(list(self.root.glob('conversations/*.db')))
        self.assertFalse(list(self.ide.glob('conversations/*.db')))

    def test_hub_only_remnants_and_plain_titles(self):
        self.ide.mkdir(parents=True)
        path = self.ide/self.hub.name
        path.write_bytes(hub(IDS[:2]))
        rows = self.store.inventory()['rows']
        self.assertEqual({row['id'] for row in rows}, set(IDS[:2]))
        self.assertEqual(next(row['title'] for row in rows if row['id']==IDS[0]), 'Chat '+IDS[0][:8])
        self.store.apply(self.store.preview([IDS[0]]))
        self.assertEqual(hub_ids(path), {IDS[1]})

    def test_orphan_wal_after_interrupted_deletion_is_visible(self):
        folder = self.root/'conversations'
        folder.mkdir()
        wal = folder/(IDS[0]+'.db-wal')
        wal.write_bytes(b'opaque orphan WAL')
        self.assertEqual([row['id'] for row in self.store.inventory()['rows']], [IDS[0]])
        self.store.apply(self.store.preview([IDS[0]]))
        self.assertFalse(wal.exists())

    def test_mismatched_cascade_or_unknown_database_blocks_before_writes(self):
        self.fixture()
        db = self.root/'conversations'/(IDS[0]+'.db')
        original = self.hub.read_bytes()
        sql(db, 'UPDATE trajectory_meta SET cascade_id=?', (IDS[1],))
        with self.assertRaisesRegex(CleanupError, 'ID|归属'):
            self.store.preview([IDS[0]])
        sql(db, 'DROP TABLE trajectory_meta')
        with self.assertRaisesRegex(CleanupError, '结构|格式'):
            self.store.preview([IDS[0]])
        self.assertEqual(self.hub.read_bytes(), original)

    def test_malformed_hub_blocks_before_writes(self):
        self.fixture()
        self.hub.write_bytes(b'\x0a\xff')
        with self.assertRaises(CleanupError):
            self.store.preview([IDS[0]])
        self.assertEqual(sql(self.summary, 'SELECT count(*) FROM conversation_summaries'), [(2,)])

    def test_stale_body_and_hub_are_rejected(self):
        self.fixture()
        plan = self.store.preview([IDS[0]])
        sql(self.root/'conversations'/(IDS[0]+'.db'), 'INSERT INTO steps VALUES(1,?)', (b'new step',))
        with self.assertRaisesRegex(CleanupError, '变化'):
            self.store.apply(plan)
        plan = self.store.preview([IDS[0]])
        self.hub.write_bytes(self.hub.read_bytes()+pb(13, b'new metadata'))
        with self.assertRaisesRegex(CleanupError, '变化'):
            self.store.apply(plan)

    def test_stale_wal_is_rejected(self):
        db = self.wal_fixture()
        plan = self.store.preview([IDS[0]])
        with contextlib.closing(sqlite3.connect(db)) as con:
            con.execute('INSERT INTO steps VALUES(2,?)', (b'another committed step',))
            con.commit()
            with self.assertRaisesRegex(CleanupError, '变化'):
                self.store.apply(plan)

    def wal_fixture(self):
        self.fixture()
        destination = self.root/'conversations'/(IDS[0]+'.db')
        staging = self.profile/'wal-source.db'
        session(staging, IDS[0])
        with contextlib.closing(sqlite3.connect(staging)) as con:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('INSERT INTO steps VALUES(1,?)', (b'committed only in WAL',))
            con.commit()
            for suffix in ('', '-wal', '-shm'):
                shutil.copy2(Path(str(staging)+suffix), Path(str(destination)+suffix))
        return destination

    def test_backup_includes_committed_wal_and_skips_raw_sidecars(self):
        db = self.wal_fixture()
        report = self.store.apply(self.store.preview([IDS[0]], backup={
            'mode':'custom', 'directory':str(self.profile/'backup')}))
        backups = {entry['source']: entry for entry in report['backups']}
        self.assertIn(str(db), backups)
        self.assertTrue(backups[str(db)]['database'])
        self.assertEqual(sql(Path(backups[str(db)]['backup']), 'SELECT count(*) FROM steps'), [(2,)])
        self.assertNotIn(str(db)+'-wal', backups)
        self.assertNotIn(str(db)+'-shm', backups)
        self.assertNotIn(str(db.with_name(IDS[1]+'.db')), backups)
        self.assertFalse(db.exists())

    def test_rollback_recreates_deleted_database_with_wal_content(self):
        db = self.wal_fixture()
        original_hub = self.hub.read_bytes()
        original_snapshot = self.store.snapshot
        plan = self.store.preview([IDS[0]], backup={'mode':'custom', 'directory':str(self.profile/'backup')})
        def fail_after_deletion():
            if not db.exists():
                raise OSError('Injected verification failure')
            return original_snapshot()
        with patch.object(self.store, 'snapshot', side_effect=fail_after_deletion):
            with self.assertRaisesRegex(CleanupError, 'rolled_back'):
                self.store.apply(plan)
        self.assertEqual(sql(db, 'SELECT step_payload FROM steps ORDER BY idx'),
                         [(b'opaque conversation body',), (b'committed only in WAL',)])
        self.assertEqual(sql(db, 'PRAGMA integrity_check'), [('ok',)])
        self.assertEqual(self.hub.read_bytes(), original_hub)
        self.assertEqual({row['id'] for row in self.store.inventory()['rows']}, set(IDS[:2]))

    def test_unremoved_body_is_not_reported_as_success(self):
        self.fixture()
        db = self.root/'conversations'/(IDS[0]+'.db')
        unlink = Path.unlink
        def skip_body(path, *args, **kwargs):
            if path != db:
                return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', skip_body):
            with self.assertRaisesRegex(CleanupError, 'failed_no_backup'):
                self.store.apply(self.store.preview([IDS[0]]))
        self.assertTrue(db.exists())

    def test_unremoved_hub_is_not_reported_as_success(self):
        self.fixture()
        with patch('cleaner.antigravity.prune_hub'):
            with self.assertRaisesRegex(CleanupError, 'failed_no_backup'):
                self.store.apply(self.store.preview([IDS[0]]))
        self.assertIn(IDS[0], hub_ids(self.hub))

    def test_rollback_after_main_file_deleted_but_wal_unlink_fails(self):
        db = self.wal_fixture()
        wal = Path(str(db)+'-wal')
        unlink = Path.unlink
        def fail_wal(path, *args, **kwargs):
            if path == wal:
                raise OSError('Injected WAL removal failure')
            return unlink(path, *args, **kwargs)
        plan = self.store.preview([IDS[0]], backup={'mode':'custom', 'directory':str(self.profile/'backup')})
        with patch.object(Path, 'unlink', fail_wal):
            with self.assertRaisesRegex(CleanupError, 'rolled_back'):
                self.store.apply(plan)
        self.assertEqual(sql(db, 'SELECT count(*) FROM steps'), [(2,)])
        self.assertEqual(sql(db, 'PRAGMA integrity_check'), [('ok',)])
        self.assertEqual(hub_ids(self.hub), set(IDS[:2]))


class AntigravityProcessTests(unittest.TestCase):
    @patch('sys.platform', 'darwin')
    def test_mac_generic_server_checks_owning_application(self):
        listing = '''10 /Applications/Antigravity.app/Contents/Resources/bin/language_server
11 /Applications/Other.app/Contents/Resources/bin/language_server
12 /Applications/Antigravity IDE.app/Contents/Resources/language_server_macos_arm
'''
        with patch('cleaner.processes.subprocess.run', return_value=subprocess.CompletedProcess([], 0, listing, '')):
            result = processes.cli_processes('antigravity', {'antigravity.exe'})
        self.assertEqual({row['pid'] for row in result}, {10, 12})

    @patch('sys.platform', 'win32')
    def test_windows_generic_server_checks_owning_application(self):
        listing = '''[{"pid":10,"name":"language_server.exe","path":"C:/Programs/Antigravity/resources/bin/language_server.exe"},
                      {"pid":11,"name":"language_server.exe","path":"C:/Programs/Other/bin/language_server.exe"}]'''
        with patch('cleaner.processes.app_processes', return_value=[]), \
             patch('cleaner.processes.subprocess.run', return_value=subprocess.CompletedProcess([], 0, listing, '')):
            result = processes.cli_processes('antigravity', {'antigravity.exe'})
        self.assertEqual({row['pid'] for row in result}, {10})

    @patch('sys.platform', 'win32')
    def test_unreadable_windows_server_path_blocks_deletion(self):
        listing = '[{"pid":10,"name":"language_server.exe","path":null}]'
        with patch('cleaner.processes.app_processes', return_value=[]), \
             patch('cleaner.processes.subprocess.run', return_value=subprocess.CompletedProcess([], 0, listing, '')):
            with self.assertRaisesRegex(RuntimeError, '进程'):
                processes.cli_processes('antigravity', {'antigravity.exe'})


if __name__ == '__main__':
    unittest.main()
