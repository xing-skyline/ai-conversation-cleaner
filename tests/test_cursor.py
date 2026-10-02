"""Synthetic Cursor 3.23 storage; never opens an installed Cursor profile."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cleaner.demo import IDS
from cleaner.providers import LocalProvider
from cleaner.store import CleanupError
from tests.test_cleaner import sql


EXACT = ('composerData', 'composerDraft', 'composerRootWriterDiagnostic',
         'composerVirtualRowHeights', 'localAgentMailboxCursor')
SCOPED = ('bubbleId', 'checkpointId', 'inlineDiff', 'ofsContent',
          'codeBlockPartialInlineDiffFates')


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ai-cleaner-cursor-tests-')
        self.profile = Path(self.temp.name)
        self.store = LocalProvider('cursor', self.profile, process_provider=lambda: [])
        self.db = self.store.home / 'globalStorage/state.vscdb'
        self.search = self.db.with_name('conversation-search.db')
        self.db.parent.mkdir(parents=True)
        sql(self.db, '''CREATE TABLE composerHeaders (
            composerId TEXT PRIMARY KEY, workspaceId TEXT, createdAt INTEGER,
            lastUpdatedAt INTEGER, isArchived INTEGER, isSubagent INTEGER,
            recency INTEGER, checkpointAt INTEGER, subagentTypeName TEXT, value TEXT)''')
        sql(self.db, 'CREATE TABLE cursorDiskKV(key TEXT UNIQUE, value BLOB)')
        sql(self.db, 'CREATE TABLE ItemTable(key TEXT UNIQUE, value BLOB)')
        for target in IDS[:2]:
            header = {'type': 'head', 'composerId': target, 'name': 'Synthetic chat',
                      'subtitle': 'Private subtitle', 'createdAt': 1000, 'lastUpdatedAt': 2000,
                      'isArchived': False, 'isDraft': False, 'workspaceIdentifier': {'id': 'sample'}}
            sql(self.db, 'INSERT INTO composerHeaders VALUES(?,?,?,?,?,?,?,?,?,?)',
                (target, 'sample', 1000, 2000, 0, 0, 2000, None, '', json.dumps(header)))
            for prefix in EXACT:
                sql(self.db, 'INSERT INTO cursorDiskKV VALUES(?,?)',
                    (prefix + ':' + target, json.dumps(header)))
            for prefix in SCOPED:
                sql(self.db, 'INSERT INTO cursorDiskKV VALUES(?,?)',
                    (prefix + ':' + target + ':item', 'owned data'))
        for key in ('agentKv:blob:shared', 'composer.content.shared'):
            sql(self.db, 'INSERT INTO cursorDiskKV VALUES(?,?)', (key, 'shared ' + IDS[0]))
        for key in ('composer.composerData', 'workbench.backgroundComposer.workspacePersistentData'):
            sql(self.db, 'INSERT INTO ItemTable VALUES(?,?)', (key, json.dumps({
                'allComposers': [{'composerId': target} for target in IDS[:2]],
                'selectedComposerIds': list(IDS[:2]), 'keep': 'setting'})))
        sql(self.db, 'INSERT INTO ItemTable VALUES(?,?)',
            ('cursor/pinnedComposers', json.dumps(list(IDS[:2]))))
        sql(self.db, 'INSERT INTO ItemTable VALUES(?,?)', ('cursor/glass.selectedAgent', IDS[0]))
        sql(self.db, 'INSERT INTO ItemTable VALUES(?,?)', ('cursor/glass.lastRealAgent', IDS[1]))
        sql(self.search, '''CREATE TABLE conversations (
            fts_rowid INTEGER PRIMARY KEY, source TEXT NOT NULL, scope TEXT NOT NULL,
            id TEXT NOT NULL, title TEXT NOT NULL, branches TEXT NOT NULL,
            updated_at INTEGER NOT NULL, is_archived INTEGER NOT NULL,
            root_fingerprint TEXT, cache_fingerprint TEXT, UNIQUE(source,scope,id))''')
        sql(self.search, 'CREATE VIRTUAL TABLE conversation_fts USING fts5(title,body,branches)')
        sql(self.search, 'CREATE TABLE conversation_search_candidates(id TEXT PRIMARY KEY,updated_at INTEGER NOT NULL)')
        sql(self.search, 'CREATE TABLE conversation_search_reconciliation(id INTEGER PRIMARY KEY,cursor TEXT NOT NULL,in_progress INTEGER NOT NULL)')
        sql(self.search, 'INSERT INTO conversation_search_reconciliation VALUES(1,?,0)', ('',))
        sql(self.search, 'CREATE TABLE conversation_search_settings(id INTEGER PRIMARY KEY,effective_conversation_cap INTEGER NOT NULL)')
        for number, (target, source, scope) in enumerate([
            (IDS[0], 'local', ''), (IDS[1], 'local', ''), (IDS[0], 'cloud-cache', 'account'),
            (IDS[2], 'local', '')], 1):
            sql(self.search, 'INSERT INTO conversations VALUES(?,?,?,?,?,?,?,?,?,?)',
                (number, source, scope, target, 'Synthetic', '', 2000, 0,
                 'root' if source == 'local' else None, None))
            sql(self.search, 'INSERT INTO conversation_fts(rowid,title,body,branches) VALUES(?,?,?,?)',
                (number, 'Synthetic', 'body ' + str(number), ''))
            if source == 'local':
                sql(self.search, 'INSERT INTO conversation_search_candidates VALUES(?,?)', (target, 2000))

    def tearDown(self):
        self.temp.cleanup()

    def test_delete_covers_new_keys_and_search_preserving_other_and_cloud_rows(self):
        other_header = sql(self.db, 'SELECT * FROM composerHeaders WHERE composerId=?', (IDS[1],))
        other_kv = sql(self.db, 'SELECT * FROM cursorDiskKV WHERE key LIKE ? ORDER BY key', ('%' + IDS[1] + '%',))
        result = self.store.apply(self.store.preview([IDS[0]]))
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(sql(self.db, 'SELECT key FROM cursorDiskKV WHERE key LIKE ?', ('%' + IDS[0] + '%',)), [])
        self.assertEqual(sql(self.db, 'SELECT * FROM composerHeaders WHERE composerId=?', (IDS[1],)), other_header)
        self.assertEqual(sql(self.db, 'SELECT * FROM cursorDiskKV WHERE key LIKE ? ORDER BY key', ('%' + IDS[1] + '%',)), other_kv)
        header = sql(self.db, 'SELECT isArchived,lastUpdatedAt,recency,value FROM composerHeaders WHERE composerId=?', (IDS[0],))[0]
        value = json.loads(header[3])
        self.assertEqual(header[0], 1)
        self.assertGreater(header[1], 2000)
        self.assertEqual(header[1], header[2])
        self.assertTrue(value['isArchived'])
        self.assertFalse(value['isDraft'])
        self.assertNotIn('name', value)
        self.assertNotIn('subtitle', value)
        self.assertNotIn(IDS[0], {r['id'] for r in self.store.inventory()['rows']})
        self.assertEqual(sql(self.search, 'SELECT source,scope FROM conversations WHERE id=?', (IDS[0],)), [('cloud-cache', 'account')])
        self.assertEqual(sql(self.search, 'SELECT rowid,body FROM conversation_fts ORDER BY rowid'), [(2, 'body 2'), (3, 'body 3'), (4, 'body 4')])
        self.assertEqual(sql(self.search, 'SELECT id FROM conversation_search_candidates WHERE id=?', (IDS[0],)), [])
        self.assertEqual(sql(self.db, "SELECT value FROM cursorDiskKV WHERE key='agentKv:blob:shared'"), [('shared ' + IDS[0],)])
        self.assertEqual(json.loads(sql(self.db, "SELECT value FROM ItemTable WHERE key='cursor/pinnedComposers'")[0][0]), [IDS[1]])
        self.assertEqual(sql(self.db, "SELECT value FROM ItemTable WHERE key='cursor/glass.selectedAgent'"), [])
        self.assertEqual(sql(self.db, "SELECT value FROM ItemTable WHERE key='cursor/glass.lastRealAgent'"), [(IDS[1],)])

    def test_search_only_and_owned_key_only_remnants_are_selectable(self):
        sql(self.db, 'INSERT INTO cursorDiskKV VALUES(?,?)', ('ofsContent:' + IDS[3] + ':file', 'remnant'))
        self.assertEqual({r['id'] for r in self.store.inventory()['rows']}, set(IDS[:4]))
        self.store.apply(self.store.preview([IDS[2], IDS[3]]))
        self.assertEqual({r['id'] for r in self.store.inventory()['rows']}, set(IDS[:2]))
        self.assertEqual(sql(self.search, 'SELECT rowid FROM conversation_fts WHERE rowid=4'), [])

    def test_archived_unnamed_conversation_with_body_is_not_hidden(self):
        raw = json.loads(sql(self.db, 'SELECT value FROM composerHeaders WHERE composerId=?', (IDS[0],))[0][0])
        raw.pop('name'); raw.pop('subtitle'); raw['isArchived'] = True
        sql(self.db, 'UPDATE composerHeaders SET isArchived=1,value=? WHERE composerId=?', (json.dumps(raw), IDS[0]))
        self.assertIn(IDS[0], {r['id'] for r in self.store.inventory()['rows']})

    def test_search_changes_invalidate_preview(self):
        plan = self.store.preview([IDS[0]])
        sql(self.search, "UPDATE conversations SET title='changed' WHERE id=?", (IDS[1],))
        with self.assertRaisesRegex(CleanupError, '变化'):
            self.store.apply(plan)
        self.assertEqual(len(sql(self.db, 'SELECT composerId FROM composerHeaders')), 2)

    def test_unknown_search_schema_blocks_before_any_deletion(self):
        sql(self.search, 'CREATE TABLE future_conversation_parts(conversation_id TEXT,body TEXT)')
        with self.assertRaisesRegex(CleanupError, 'Cursor.*(未适配|结构)'):
            self.store.preview([IDS[0]])
        self.assertEqual(len(sql(self.db, 'SELECT composerId FROM composerHeaders')), 2)

    def test_search_cloud_scope_is_validated(self):
        sql(self.search, "UPDATE conversations SET scope='unexpected' WHERE source='local' AND id=?", (IDS[0],))
        with self.assertRaisesRegex(CleanupError, 'Cursor.*(归属|结构)'):
            self.store.preview([IDS[0]])

    def test_backups_restore_state_and_search_on_failure(self):
        state_before = sql(self.db, 'SELECT * FROM composerHeaders ORDER BY composerId')
        search_before = sql(self.search, 'SELECT * FROM conversations ORDER BY fts_rowid')
        fts_before = sql(self.search, 'SELECT rowid,* FROM conversation_fts ORDER BY rowid')
        original = self.store.mutate_db
        def fail_after_state(path, ids):
            result = original(path, ids)
            if path == self.db:
                raise OSError('injected failure after state write')
            return result
        plan = self.store.preview([IDS[0]], backup={'mode': 'custom', 'directory': str(self.profile/'backup')})
        with patch.object(self.store, 'mutate_db', side_effect=fail_after_state):
            with self.assertRaisesRegex(CleanupError, 'rolled_back'):
                self.store.apply(plan)
        self.assertEqual(sql(self.db, 'SELECT * FROM composerHeaders ORDER BY composerId'), state_before)
        self.assertEqual(sql(self.search, 'SELECT * FROM conversations ORDER BY fts_rowid'), search_before)
        self.assertEqual(sql(self.search, 'SELECT rowid,* FROM conversation_fts ORDER BY rowid'), fts_before)

    def test_fts_residue_cannot_report_success(self):
        original = self.store.mutate_db
        def reinsert_fts(path, ids):
            result = original(path, ids)
            if path == self.search:
                sql(path, 'INSERT OR REPLACE INTO conversation_fts(rowid,title,body,branches) VALUES(1,?,?,?)',
                    ('left behind', 'body', ''))
            return result
        plan = self.store.preview([IDS[0]])
        with patch.object(self.store, 'mutate_db', side_effect=reinsert_fts):
            with self.assertRaisesRegex(CleanupError, 'failed_no_backup'):
                self.store.apply(plan)

    def test_unknown_state_header_schema_blocks_preview(self):
        sql(self.db, 'ALTER TABLE composerHeaders ADD COLUMN remoteHostId TEXT')
        with self.assertRaisesRegex(CleanupError, 'Cursor.*(未适配|结构)'):
            self.store.preview([IDS[0]])

    def test_malformed_new_index_blocks_preview(self):
        sql(self.db, "UPDATE ItemTable SET value='not json' WHERE key='cursor/pinnedComposers'")
        with self.assertRaisesRegex(CleanupError, 'Cursor.*JSON'):
            self.store.preview([IDS[0]])

    def test_uuid_boundaries_and_shared_values_are_preserved(self):
        keys = ['composerDraft:' + IDS[0] + ':unrecognized',
                'ofsContent:' + IDS[0] + '-other:file',
                'unknown:' + IDS[0], 'composerData:empty-state-draft']
        for key in keys:
            sql(self.db, 'INSERT INTO cursorDiskKV VALUES(?,?)', (key, '{}'))
        self.store.apply(self.store.preview([IDS[0]]))
        for key in keys:
            self.assertEqual(sql(self.db, 'SELECT value FROM cursorDiskKV WHERE key=?', (key,)), [('{}',)])

    def test_native_tombstone_survives_rescan_and_remnants_can_be_cleaned_again(self):
        self.store.apply(self.store.preview([IDS[0]]))
        reopened = LocalProvider('cursor', self.profile, process_provider=lambda: [])
        self.assertNotIn(IDS[0], {r['id'] for r in reopened.inventory()['rows']})
        sql(self.db, 'INSERT INTO cursorDiskKV VALUES(?,?)', ('composerDraft:' + IDS[0], '{}'))
        self.assertIn(IDS[0], {r['id'] for r in reopened.inventory()['rows']})
        reopened.apply(reopened.preview([IDS[0]]))
        self.assertNotIn(IDS[0], {r['id'] for r in reopened.inventory()['rows']})

    def test_search_fts_structure_is_checked_before_mutation(self):
        sql(self.search, 'DROP TABLE conversation_fts')
        sql(self.search, 'CREATE TABLE conversation_fts(title TEXT,body TEXT,branches TEXT)')
        with self.assertRaisesRegex(CleanupError, 'Cursor.*结构'):
            self.store.preview([IDS[0]])


if __name__ == '__main__':
    unittest.main()
