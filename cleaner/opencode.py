"""Offline OpenCode SQLite and recognized legacy JSON storage adapter.

Schema reference: anomalyco/opencode v1.18.31, packages/core/src/session/sql.ts
and packages/core/src/event/sql.ts. Never remove project/account/config records.
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
    'part': 'session_id', 'message': 'session_id', 'todo': 'session_id',
    'session_share': 'session_id', 'session_message': 'session_id',
    'session_input': 'session_id', 'session_context_epoch': 'session_id',
    'event': 'aggregate_id', 'event_sequence': 'aggregate_id', 'session': 'id',
}


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
        if 'session' not in names or not {'id','title','directory','time_updated'} <= columns(con,'session'):
            raise CleanupError('OpenCode session 表结构不受支持。')
        for name in names:
            cols = columns(con, name)
            foreign = {r[2] for r in con.execute(f'PRAGMA foreign_key_list({quote(name)})')}
            if name not in OWNED and (cols & {'session_id','message_id','aggregate_id'} or foreign & OWNED.keys()):
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
        return names

    def snapshot(self):
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
                for record in con.execute('SELECT * FROM session ORDER BY id'):
                    item = dict(record); current = row(item['id'])
                    if current is None:
                        raise CleanupError('OpenCode 出现未识别的会话 ID 格式。')
                    current.update(title=item['title'] or current['title'],cwd=item['directory'] or '',
                                   updated=timestamp(item['time_updated']), archived=bool(item.get('time_archived')),
                                   parent_id=item.get('parent_id'),stored_session=True)
                for table, column in OWNED.items():
                    if table in names and table != 'session':
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
        notes.append('仅清理本机会话、事件与已识别的旧会话文件；保留账号、凭据、项目、日志、共享工具输出和云端分享。数据库整库备份可能包含账号信息，请妥善保管。')
        return {'rows':sorted(rows.values(),key=lambda r:r['updated'],reverse=True),'files':files,'shared':{},
                'databases':dbs,'messages':messages,'fingerprint':digest([rows,stamps]),'warnings':list(dict.fromkeys(notes))}

    def preview(self, selected, backup=None):
        plan = super().preview(selected,backup)
        ids = set(plan['ids'])
        children = [r['id'] for r in self.snapshot()['rows'] if r.get('parent_id') in ids and r['id'] not in ids]
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
            if not messages and 'session_message' in names and {'type','data','seq'} <= columns(con,'session_message'):
                for record in reversed(con.execute('SELECT type,data FROM session_message WHERE session_id=? ORDER BY seq DESC LIMIT 20',(target,)).fetchall()):
                    text = message_text(object_json(record['data']))
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
