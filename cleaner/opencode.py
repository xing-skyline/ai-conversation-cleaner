"""Offline OpenCode SQLite and recognized legacy JSON storage adapter.

Schema references: v1.18.31 and the session_v2 schema (2026-11-17),
packages/core/src/session/sql.ts. Never remove project/account/config records.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path

from .providers import LocalProvider, read_json, timestamp
from .store import CleanupError, canonical_path, columns, connect, digest, file_stamp, quote, tables

SESSION_ID = re.compile(r'ses_[A-Za-z0-9]{1,128}')
# Explicit order also works for older databases without foreign-key cascades.
OWNED = {
    'blob_ref': 'session_id', 'session_part': 'session_id',
    'session_inbox': 'session_id', 'instruction_entry': 'session_id',
    'instruction_state': 'session_id', 'job': 'session_id',
    'part': 'session_id', 'message': 'session_id', 'todo': 'session_id',
    'session_share': 'session_id', 'session_message': 'session_id',
    'session_input': 'session_id', 'session_context_epoch': 'session_id',
    'event': 'aggregate_id', 'event_sequence': 'aggregate_id', 'session': 'id', 'session_v2': 'id',
}
SESSION_TABLES = ('session', 'session_v2')
# Upstream explicitly retains automation definitions after their creating session
# is deleted. Do not treat this session_id as ownership of the automation.
AUTOMATION_COLUMNS = {'id','project_id','session_id','directory','name','trigger','tz','action','status',
                      'next_due_at','expires_at','run_count','failures','last_run_at','last_message_id',
                      'last_outcome','last_output','retry_at','state','event_position','time_created','time_updated'}


def object_json(value):
    data = json.loads(value) if isinstance(value, str) else value
    if not isinstance(data, dict):
        raise CleanupError('OpenCode 会话记录结构不受支持。')
    return data


def message_text(data):
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        return '\n'.join(filter(None, (message_text(v) for v in data)))
    if isinstance(data, dict):
        for key in ('text', 'content', 'parts'):
            if key in data:
                return message_text(data[key])
    return ''


class OpenCodeProvider(LocalProvider):
    def __init__(self, profile=None, process_provider=None):
        super().__init__('opencode', profile, process_provider)
        self.home = canonical_path(self.home)
        # Explicit fixture profiles must never inherit the user's real data paths.
        if profile is None and os.environ.get('XDG_DATA_HOME'):
            self.home = canonical_path(Path(os.environ['XDG_DATA_HOME'])/'opencode')
        self.roots = [self.home]
        self.database = self.home/'opencode.db'
        if profile is None and os.environ.get('OPENCODE_DB'):
            configured = os.environ['OPENCODE_DB']
            if configured == ':memory:':
                raise CleanupError('OpenCode 内存数据库不支持离线清理。')
            path = Path(configured)
            self.database = canonical_path(path if path.is_absolute() else self.home/path)
            if not self.database.is_relative_to(self.home):
                self.roots.append(self.database.parent)

    def valid_id(self, target):
        return isinstance(target, str) and bool(SESSION_ID.fullmatch(target))

    def validate_schema(self, con):
        names = tables(con)
        sessions = names.intersection(SESSION_TABLES)
        if not sessions or any(not {'id','title','directory','time_updated'} <= columns(con,t) for t in sessions):
            raise CleanupError('OpenCode session/session_v2 表结构不受支持。')
        for name in names:
            cols = columns(con, name)
            foreign = {r[2] for r in con.execute(f'PRAGMA foreign_key_list({quote(name)})')}
            preserved = name == 'automation' and cols == AUTOMATION_COLUMNS and not foreign & OWNED.keys()
            if name not in OWNED and not preserved and (cols & {'session_id','message_id','aggregate_id','child_session_id'} or foreign & OWNED.keys()):
                raise CleanupError('OpenCode 存在未适配的会话关联表：'+name)
            if name in OWNED and OWNED[name] not in cols:
                raise CleanupError('OpenCode 表结构已变化：'+name)
        if 'part' in names and not {'id','message_id','data'} <= columns(con,'part'):
            raise CleanupError('OpenCode part 表结构已变化。')
        if 'message' in names and not {'id','data'} <= columns(con,'message'):
            raise CleanupError('OpenCode message 表结构已变化。')
        if {'part','message'} <= names and con.execute('''
            SELECT 1 FROM part p JOIN message m ON p.message_id=m.id
            WHERE p.session_id<>m.session_id LIMIT 1''').fetchone():
            raise CleanupError('OpenCode 消息与正文的会话归属不一致。')
        if 'session_part' in names:
            if 'session_message' not in names or not {'message_id','ordinal','data'} <= columns(con,'session_part') or not {'id','type','seq','data'} <= columns(con,'session_message'):
                raise CleanupError('OpenCode session_part/session_message 表结构已变化。')
            if con.execute('''SELECT 1 FROM session_part p JOIN session_message m ON p.message_id=m.id
                              WHERE p.session_id<>m.session_id LIMIT 1''').fetchone():
                raise CleanupError('OpenCode 新版消息与正文的会话归属不一致。')
        return names

    def snapshot(self):
        try:
            return self._snapshot()
        except FileNotFoundError as error:
            raise CleanupError('OpenCode 会话文件已移动或删除，请退出 OpenCode 后刷新并重新预览。缺失路径：'
                               + str(error.filename or error)) from error

    def _snapshot(self):
        rows, files, dbs, legacy_messages = {}, {}, set(), {}
        message_owners, notes = {}, []
        def row(target):
            if not self.valid_id(target):
                return None
            return rows.setdefault(target, self.base_row(target) | {'parent_id':None,'stored_session':False})
        def owned_file(target, path):
            if row(target) is not None:
                files.setdefault(target,set()).add(self.safe(path))
        if self.database.is_file():
            db = self.safe(self.database)
            dbs.add(db)
            with contextlib.closing(connect(db)) as con:
                names = self.validate_schema(con)
                for table in SESSION_TABLES:
                    if table not in names:
                        continue
                    for record in con.execute(f'SELECT * FROM {quote(table)} ORDER BY id'):
                        item = dict(record); current = row(item['id'])
                        if current is None:
                            raise CleanupError('OpenCode 出现未识别的会话 ID 格式。')
                        # Migrated databases can retain both generations. Prefer v2
                        # for display, but retain dependencies from both records.
                        current.setdefault('dependencies', []).extend(filter(None,(item.get('parent_id'),item.get('fork_session_id'))))
                        current.update(title=item['title'] or current['title'],cwd=item['directory'] or '',
                                       updated=timestamp(item['time_updated']), archived=bool(item.get('time_archived')),
                                       parent_id=item.get('parent_id'),stored_session=True)
                for table, column in OWNED.items():
                    if table in names and table not in SESSION_TABLES:
                        for (target,) in con.execute(f'SELECT DISTINCT {quote(column)} FROM {quote(table)}'):
                            row(target)  # Also expose recognized orphaned records.
                if 'message' in names:
                    message_owners.update(con.execute('SELECT id,session_id FROM message'))
        storage = self.home/'storage'
        for path in sorted((storage/'session').glob('*/*.json')):
            target = path.stem
            if not self.valid_id(target):
                continue
            data = object_json(read_json(self.safe(path)))
            if data.get('id') != target:
                raise CleanupError('OpenCode 会话文件与记录 ID 不一致。')
            current = row(target)
            if not current['stored_session']:
                current.update(title=data.get('title') or current['title'],cwd=data.get('directory') or '',
                               updated=timestamp(data.get('time',{}).get('updated')),
                               archived=bool(data.get('time',{}).get('archived')),parent_id=data.get('parentID'))
            current['stored_session'] = True
            owned_file(target,path)
        for path in sorted((storage/'message').glob('*/*.json')):
            target = path.parent.name
            if not self.valid_id(target):
                continue
            data = object_json(read_json(self.safe(path)))
            if data.get('id') != path.stem or data.get('sessionID') != target:
                raise CleanupError('OpenCode 旧消息文件归属不一致。')
            if path.stem in message_owners and message_owners[path.stem] != target:
                raise CleanupError('OpenCode 数据库与旧消息文件归属不一致。')
            message_owners[path.stem] = target
            legacy_messages[path.stem] = data | {'_parts':[]}
            owned_file(target,path)
        for path in sorted((storage/'part').glob('*/*.json')):
            data = object_json(read_json(self.safe(path)))
            target, message = data.get('sessionID'), path.parent.name
            if not self.valid_id(target):
                notes.append('存在无法识别归属的旧正文文件，未列入删除。')
                continue
            if data.get('id') != path.stem or data.get('messageID') != message:
                raise CleanupError('OpenCode 旧正文文件归属不一致。')
            if message in message_owners and message_owners[message] != target:
                raise CleanupError('OpenCode 消息与旧正文的会话归属不一致。')
            owned_file(target,path)
            if message in legacy_messages:
                legacy_messages[message]['_parts'].append(data)
        for path in (storage/'session_diff').glob('*.json'):
            if self.valid_id(path.stem):
                owned_file(path.stem,path)
        messages = {}
        for data in legacy_messages.values():
            text = message_text(data['_parts']) or message_text(data)
            if text:
                messages.setdefault(data['sessionID'],[]).append({'role':'用户' if data.get('role')=='user' else '助手','text':text[:6000]})
        for target,current in rows.items():
            paths = files.get(target,set())
            current.update(files=len(paths), bytes=sum(p.stat().st_size for p in paths))
            current['status'] = '残留记录' if not current['stored_session'] else ('已归档' if current['archived'] else '正常')
        stamps = [file_stamp(p) for p in sorted({p for paths in files.values() for p in paths})]
        for db in sorted(dbs):
            stamps.append(file_stamp(db))
            wal = Path(str(db)+'-wal')
            if wal.exists():
                stamps.append(file_stamp(wal))
        if not dbs and not rows:
            notes.append('未找到 OpenCode 会话数据库或旧会话文件。请核对数据目录；自定义数据库可通过 OPENCODE_DB 指定。')
        notes.append('仅清理本机会话、事件与已识别的旧会话文件；保留账号、凭据、项目、自动化定义、日志、共享 blob/工具输出和云端分享。数据库整库备份可能包含账号信息，请妥善保管。')
        return {'rows':sorted(rows.values(),key=lambda r:r['updated'],reverse=True),'files':files,'shared':{},
                'databases':dbs,'messages':messages,'fingerprint':digest([rows,stamps]),'warnings':list(dict.fromkeys(notes))}

    def preview(self, selected, backup=None):
        plan = super().preview(selected,backup)
        ids = set(plan['ids'])
        children = [r['id'] for r in self.snapshot()['rows']
                    if (r.get('parent_id') in ids or ids.intersection(r.get('dependencies',[]))) and r['id'] not in ids]
        if children:
            raise CleanupError('所选 OpenCode 会话包含子会话，请一并选择或保留父会话：'+', '.join(children))
        return plan

    def detail(self, target):
        result = super().detail(target)
        if not self.database.is_file():
            return result
        messages = []
        with contextlib.closing(connect(self.safe(self.database))) as con:
            names = self.validate_schema(con)
            if 'message' in names:
                for record in reversed(con.execute('SELECT id,data FROM message WHERE session_id=? ORDER BY id DESC LIMIT 20',(target,)).fetchall()):
                    data = object_json(record['data'])
                    parts = [object_json(p[0]) for p in con.execute('SELECT data FROM part WHERE session_id=? AND message_id=? ORDER BY id',(target,record['id']))] if 'part' in names else []
                    text = message_text(parts) or message_text(data)
                    if text:
                        messages.append({'role':'用户' if data.get('role')=='user' else '助手','text':text[:6000]})
            if 'session_message' in names and {'type','data','seq'} <= columns(con,'session_message') and (not messages or 'session_v2' in names):
                messages = []
                fields = 'id,type,data' if 'session_part' in names else 'type,data'
                for record in reversed(con.execute(f'SELECT {fields} FROM session_message WHERE session_id=? ORDER BY seq DESC LIMIT 20',(target,)).fetchall()):
                    parts = [object_json(p[0]) for p in con.execute('SELECT data FROM session_part WHERE session_id=? AND message_id=? ORDER BY ordinal',(target,record['id']))] if 'session_part' in names else []
                    text = message_text(parts) or message_text(object_json(record['data']))
                    if text:
                        messages.append({'role':'用户' if record['type']=='user' else '助手','text':text[:6000]})
        result['messages'] = messages or result['messages'][-20:]
        return result

    def mutate_db(self, path, ids):
        marks = ','.join('?' for _ in ids)
        with contextlib.closing(connect(self.safe(path),readonly=False)) as con:
            con.execute('PRAGMA foreign_keys=ON')
            con.execute('BEGIN IMMEDIATE')
            try:
                names = self.validate_schema(con)
                for table,column in OWNED.items():
                    if table in names:
                        con.execute(f'DELETE FROM {quote(table)} WHERE {quote(column)} IN ({marks})',ids)
                if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or con.execute('PRAGMA foreign_key_check').fetchone():
                    raise CleanupError('OpenCode 数据库删除后完整性检查失败。')
                con.commit()
            except Exception:
                con.rollback()
                raise
