"""Recognized Cursor state and search storage (checked against Cursor 3.23.23).

Only UUID-owned local records are removed. Content-addressed blobs, cloud search
rows and non-session settings are never selected by substring matching.
"""
from __future__ import annotations

import json
import time

from .store import CleanupError, UUID, columns, quote, tables


EXACT_PREFIXES = ('composerData', 'composerDraft', 'composerRootWriterDiagnostic',
                  'composerVirtualRowHeights', 'localAgentMailboxCursor')
SCOPED_PREFIXES = ('bubbleId', 'checkpointId', 'inlineDiff', 'ofsContent',
                   'codeBlockPartialInlineDiffFates')
JSON_INDEX_KEYS = ('composer.composerData', 'workbench.backgroundComposer.workspacePersistentData',
                   'workbench.backgroundComposer.persistentData', 'cursor/pinnedComposers',
                   'glass.localAgentProjectMembership.v1')
SCALAR_INDEX_KEYS = ('cursor/glass.selectedAgent', 'cursor/glass.lastRealAgent')
UI_KEY_PREFIXES = ('glass/cursor.editorPanelVisibility.agent/', 'cursor/glass.editorPanelFullscreen/')
HEADER_COLUMNS = {'composerId', 'workspaceId', 'createdAt', 'lastUpdatedAt', 'isArchived',
                  'isSubagent', 'recency', 'checkpointAt', 'subagentTypeName', 'value'}
SEARCH_COLUMNS = {
    'conversations': {'fts_rowid', 'source', 'scope', 'id', 'title', 'branches',
                      'updated_at', 'is_archived', 'root_fingerprint', 'cache_fingerprint'},
    'conversation_fts': {'title', 'body', 'branches'},
    'conversation_search_candidates': {'id', 'updated_at'},
    'conversation_search_reconciliation': {'id', 'cursor', 'in_progress'},
    'conversation_search_settings': {'id', 'effective_conversation_cap'},
}
FTS_SHADOWS = {'conversation_fts_' + suffix for suffix in ('data', 'idx', 'content', 'docsize', 'config')}


def object_json(value, label):
    try:
        result = json.loads(value)
    except (ValueError, TypeError, UnicodeError) as error:
        raise CleanupError('Cursor ' + label + ' JSON 无法读取。') from error
    if not isinstance(result, dict):
        raise CleanupError('Cursor ' + label + ' 结构未适配。')
    return result


def owned_id(key):
    parts = key.split(':', 2)
    if len(parts) < 2 or not UUID.fullmatch(parts[1]):
        return None
    if (parts[0] in EXACT_PREFIXES and len(parts) == 2
            or parts[0] in SCOPED_PREFIXES and len(parts) == 3):
        return parts[1]
    return None


def validate_state(con):
    names = tables(con)
    for table in ('ItemTable', 'cursorDiskKV'):
        if table in names and columns(con, table) != {'key', 'value'}:
            raise CleanupError('Cursor ' + table + ' 结构未适配。')
    if 'composerHeaders' in names:
        fields = columns(con, 'composerHeaders')
        if not {'composerId', 'value'} <= fields or not fields <= HEADER_COLUMNS:
            raise CleanupError('Cursor composerHeaders 结构未适配。')
        if fields != {'composerId', 'value'} and not {'workspaceId', 'isArchived', 'lastUpdatedAt'} <= fields:
            raise CleanupError('Cursor composerHeaders 结构未适配。')
    for table in names - {'ItemTable', 'cursorDiskKV', 'composerHeaders'}:
        if columns(con, table) & {'composerId', 'conversationId', 'conversation_id', 'sessionId', 'session_id'}:
            raise CleanupError('Cursor 存在未适配的会话关联表：' + table)
    if 'ItemTable' in names:
        for key, value in con.execute('SELECT key,value FROM ItemTable'):
            if key in JSON_INDEX_KEYS:
                index_value(value, set(), lambda item, _: item)


def modern_headers(con):
    return {'workspaceId', 'lastUpdatedAt', 'isArchived'} <= columns(con, 'composerHeaders')


def is_tombstone(con, target, info):
    if not (modern_headers(con) and info.get('isArchived') is True
            and info.get('isDraft') is False and 'name' not in info
            and 'subtitle' not in info and 'draftTarget' not in info):
        return False
    return 'cursorDiskKV' not in tables(con) or con.execute(
        'SELECT 1 FROM cursorDiskKV WHERE key=?', ('composerData:' + target,)).fetchone() is None


def state_headers(con):
    if 'composerHeaders' not in tables(con):
        return
    for target, value in con.execute('SELECT composerId,value FROM composerHeaders'):
        info = object_json(value, '会话头')
        if info.get('composerId', target) != target:
            raise CleanupError('Cursor 会话头 ID 与主键不一致。')
        if not is_tombstone(con, target, info):
            yield target, info


def state_remnants(con):
    if 'cursorDiskKV' in tables(con):
        for key, in con.execute('SELECT key FROM cursorDiskKV'):
            target = owned_id(key)
            if target:
                yield target


def validate_search(con):
    names = tables(con)
    if not SEARCH_COLUMNS.keys() <= names:
        raise CleanupError('Cursor 搜索索引结构未适配：缺少必要的表。')
    unknown = names - SEARCH_COLUMNS.keys() - FTS_SHADOWS - {'sqlite_stat1', 'sqlite_stat4'}
    if unknown:
        raise CleanupError('Cursor 搜索索引存在未适配的表：' + ', '.join(sorted(unknown)))
    for table, fields in SEARCH_COLUMNS.items():
        if columns(con, table) != fields:
            raise CleanupError('Cursor 搜索索引结构未适配：' + table)
    ddl = con.execute("SELECT sql FROM sqlite_master WHERE name='conversation_fts'").fetchone()[0].lower()
    if 'using fts5' not in ddl or 'content=' in ddl.replace(' ', '') or 'contentless' in ddl:
        raise CleanupError('Cursor 全文索引结构未适配。')
    if con.execute("""SELECT 1 FROM conversations WHERE source NOT IN ('local','cloud-cache')
            OR (source='local' AND scope<>'') OR (source='cloud-cache' AND scope='') LIMIT 1""").fetchone():
        raise CleanupError('Cursor 搜索索引归属结构未适配。')
    if con.execute('''SELECT 1 FROM conversation_fts f LEFT JOIN conversations c
            ON c.fts_rowid=f.rowid WHERE c.fts_rowid IS NULL LIMIT 1''').fetchone():
        raise CleanupError('Cursor 全文索引存在无归属残留，请先在 Cursor 中重建索引。')


def search_rows(con):
    validate_search(con)
    for target, title, updated, archived in con.execute(
            "SELECT id,title,updated_at,is_archived FROM conversations WHERE source='local' AND scope=''"):
        yield target, title, updated, archived
    # Interrupted index reconciliation may leave candidates without a body.
    for target, updated in con.execute('''SELECT id,updated_at FROM conversation_search_candidates
            WHERE id NOT IN (SELECT id FROM conversations WHERE source='local' AND scope='')'''):
        yield target, None, updated, False


def index_value(value, ids, prune):
    try:
        original = json.loads(value)
    except (ValueError, TypeError, UnicodeError) as error:
        raise CleanupError('Cursor 会话索引 JSON 无法读取。') from error
    if not isinstance(original, (dict, list)):
        raise CleanupError('Cursor 会话索引结构未适配。')
    cleaned = prune(original, ids)
    # Legacy single-composer selection predates selectedComposerIds arrays.
    if isinstance(cleaned, dict):
        for key in ('selectedComposerId', 'lastFocusedComposerId'):
            if isinstance(cleaned.get(key), str) and cleaned[key] in ids:
                cleaned.pop(key)
    return original, cleaned


def delete_state(con, ids, prune):
    validate_state(con)
    names = tables(con)
    ids = set(ids)
    if 'composerHeaders' in names:
        fields = columns(con, 'composerHeaders')
        modern = modern_headers(con)
        for target in ids:
            entry = con.execute('SELECT value FROM composerHeaders WHERE composerId=?', (target,)).fetchone()
            if entry is None:
                continue
            info = object_json(entry[0], '会话头')
            if modern and info.get('createdFromBackgroundAgent') is None and info.get('isEphemeral') is not True:
                # Match native tombstoneDeletedComposer; leave the deletion marker
                # for canonical-header reconciliation, but remove display text.
                updated = max(int(time.time() * 1000), int(info.get('lastUpdatedAt') or 0) + 1)
                for key in ('name', 'subtitle', 'draftTarget'):
                    info.pop(key, None)
                info.update(isArchived=True, isDraft=False, lastUpdatedAt=updated)
                values = {'value': json.dumps(info, ensure_ascii=False), 'isArchived': 1, 'lastUpdatedAt': updated}
                if 'recency' in fields:
                    values['recency'] = updated
                con.execute('UPDATE composerHeaders SET ' + ','.join(quote(k) + '=?' for k in values)
                            + ' WHERE composerId=?', (*values.values(), target))
            else:
                con.execute('DELETE FROM composerHeaders WHERE composerId=?', (target,))
    if 'cursorDiskKV' in names:
        # GLOB is case-sensitive; UUID validation excludes wildcard metacharacters.
        for target in ids:
            con.executemany('DELETE FROM cursorDiskKV WHERE key=?',
                            [(prefix + ':' + target,) for prefix in EXACT_PREFIXES])
            con.executemany('DELETE FROM cursorDiskKV WHERE key GLOB ?',
                            [(prefix + ':' + target + ':*',) for prefix in SCOPED_PREFIXES])
    if 'ItemTable' in names:
        for key, value in list(con.execute('SELECT key,value FROM ItemTable')):
            if key in JSON_INDEX_KEYS:
                original, cleaned = index_value(value, ids, prune)
                if cleaned != original:
                    con.execute('UPDATE ItemTable SET value=? WHERE key=?', (json.dumps(cleaned, ensure_ascii=False), key))
            elif (key in SCALAR_INDEX_KEYS and value in ids
                  or any(key == prefix + target for prefix in UI_KEY_PREFIXES for target in ids)):
                con.execute('DELETE FROM ItemTable WHERE key=?', (key,))
        if modern_headers(con):
            # Same sentinel that Cursor bumps when the canonical header table changes.
            con.execute('INSERT OR REPLACE INTO ItemTable(key,value) VALUES(?,?)',
                        ('composer.composerHeaders.version', str(time.time_ns())))


def delete_search(con, ids):
    validate_search(con)
    marks = ','.join('?' for _ in ids)
    predicate = f"source='local' AND scope='' AND id IN ({marks})"
    rowids = [row[0] for row in con.execute('SELECT fts_rowid FROM conversations WHERE ' + predicate, ids)]
    con.executemany('DELETE FROM conversation_fts WHERE rowid=?', [(i,) for i in rowids])
    con.execute('DELETE FROM conversations WHERE ' + predicate, ids)
    con.execute(f'DELETE FROM conversation_search_candidates WHERE id IN ({marks})', ids)
    con.execute("INSERT INTO conversation_fts(conversation_fts) VALUES('integrity-check')")
    return rowids


def verify_deleted(con, ids, prune, search=False, fts_rowids=()):
    """Check actual owned records, independently of the visible conversation list."""
    ids = set(ids)
    if search:
        validate_search(con)
        if any(target in ids for target, *_ in search_rows(con)):
            raise CleanupError('Cursor 删除后仍有搜索索引残留。')
        for rowid in fts_rowids:
            if con.execute('SELECT 1 FROM conversation_fts WHERE rowid=?', (rowid,)).fetchone():
                raise CleanupError('Cursor 删除后仍有全文索引残留。')
        return
    validate_state(con)
    if any(target in ids for target, _ in state_headers(con)) or ids.intersection(state_remnants(con)):
        raise CleanupError('Cursor 删除后仍有会话数据残留。')
    if 'ItemTable' in tables(con):
        for key, value in con.execute('SELECT key,value FROM ItemTable'):
            if key in JSON_INDEX_KEYS:
                original, cleaned = index_value(value, ids, prune)
                if original != cleaned:
                    raise CleanupError('Cursor 删除后仍有工作区索引残留。')
            elif (key in SCALAR_INDEX_KEYS and value in ids
                  or any(key == prefix + target for prefix in UI_KEY_PREFIXES for target in ids)):
                raise CleanupError('Cursor 删除后仍有选中会话引用残留。')
