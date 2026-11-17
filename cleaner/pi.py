"""Pi JSONL and Oh My Pi local transcripts, artifacts and SQLite indexes.

References: earendil-works/pi session-format.md; can1357/oh-my-pi
session-paths.ts, session-storage.ts and session-db-purge.ts (2026-11-17).
Custom-file registries authorize exact transcripts, never arbitrary directories.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
from collections import deque
from pathlib import Path

from .providers import LocalProvider, PROCESSES, read_json, text_content, timestamp
from .processes import cli_processes
from .store import CleanupError, canonical_path, columns, connect, digest, file_stamp, quote, tables

SESSION_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
HISTORY_TABLES = {'history', 'session_titles', 'session_recaps'}
STATS_TABLES = {'messages', 'user_messages', 'tool_calls', 'file_offsets', 'session_rollup',
                'session_dirty', 'spotlight_sessions', 'spotlight_results', 'spotlight_calls',
                'spotlight_requests', 'spotlight_needs'}


def read_session(path):
    """Stream large journals; retain only the header, name and last 20 messages."""
    header, title, first, messages = None, '', '', deque(maxlen=20)
    with path.open(encoding='utf-8-sig') as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError as error:
                raise CleanupError(f'会话 JSONL 格式异常：{path}（第 {number} 行）') from error
            if not isinstance(item, dict):
                raise CleanupError('会话记录不是 JSON 对象：'+str(path))
            kind = item.get('type')
            if header is None:
                if number == 1 and kind == 'title':  # OMP's 256-byte physical title slot
                    title = item.get('title') or ''
                    continue
                if kind != 'session' or item.get('version', 1) not in {1, 2, 3}:
                    raise CleanupError('会话头或版本不受支持：'+str(path))
                header = item
                title = title or item.get('title') or ''
                continue
            if kind == 'session':
                raise CleanupError('同一文件存在多个会话头：'+str(path))
            if kind in {'session_info', 'title_change'}:
                title = item.get('name') if kind == 'session_info' else item.get('title')
                title = title or ''
            message = item.get('message', {}) if kind == 'message' else item if kind in {'user','assistant'} else {}
            if isinstance(message, dict) and message.get('role', kind) in {'user','assistant'}:
                text = text_content(message.get('content'))
                if text:
                    if not first and message.get('role', kind) == 'user':
                        first = text[:500]
                    messages.append({'role':'用户' if message.get('role',kind)=='user' else '助手','text':text[:6000]})
    if header is None or not isinstance(title,str) or not isinstance(header.get('cwd',''),str):
        raise CleanupError('会话头缺失或字段格式异常：'+str(path))
    return header, title, first, list(messages)


class PiProvider(LocalProvider):
    def __init__(self, app='pi', profile=None, process_provider=None):
        super().__init__(app, profile, process_provider)
        if profile is None:
            configured = os.environ.get('PI_CODING_AGENT_DIR')
            if configured:
                self.home = canonical_path(configured)
            elif app == 'omp' and os.environ.get('PI_CONFIG_DIR'):
                self.home = canonical_path(self.profile/os.environ['PI_CONFIG_DIR']/'agent')
        self.home = canonical_path(self.home)
        self.sessions = [self.home/'sessions']
        if app == 'pi':
            settings = read_json(self.home/'settings.json')
            custom = (os.environ.get('PI_CODING_AGENT_SESSION_DIR') if profile is None else None) or settings.get('sessionDir')
            if custom:
                if not isinstance(custom,str) or not Path(custom).expanduser().is_absolute():
                    raise CleanupError('Pi 自定义会话目录必须为绝对路径。')
                self.sessions.append(canonical_path(custom))
        self.sessions = list(dict.fromkeys(canonical_path(p) for p in self.sessions))
        self.roots = list(dict.fromkeys([self.home, *self.sessions]))
        self.registered = set()
        self.artifacts = set()
        if process_provider is None:
            # Both tools can reopen the same custom JSONL; check both writers.
            self.process_provider = lambda: cli_processes('pi',PROCESSES['pi']) + cli_processes('omp',PROCESSES['omp'])

    def valid_id(self, target):
        return isinstance(target,str) and bool(SESSION_ID.fullmatch(target))

    def safe(self, path):
        resolved = canonical_path(path)
        if resolved in self.registered or any(resolved == root or resolved.is_relative_to(root) for root in self.artifacts):
            return resolved
        return super().safe(path)

    def validate_db(self, con, stats=False):
        names = tables(con)
        owned = STATS_TABLES if stats else HISTORY_TABLES
        column = 'session_file' if stats else 'session_id'
        for name in names:
            cols = columns(con,name)
            foreign = {r[2] for r in con.execute(f'PRAGMA foreign_key_list({quote(name)})')}
            if name in owned and column not in cols:
                raise CleanupError('Oh My Pi 索引表结构已变化：'+name)
            if name not in owned and (cols & {'session_id','session_file'} or foreign & owned):
                raise CleanupError('Oh My Pi 存在未适配的会话索引表：'+name)
        if 'history_fts' in names:
            ddl = con.execute("SELECT sql FROM sqlite_master WHERE name='history_fts'").fetchone()[0]
            if 'history' not in names or not {'id','prompt'} <= columns(con,'history') or not re.search(r"content\s*=\s*['\"]history['\"]",ddl,re.I):
                raise CleanupError('Oh My Pi 提示搜索索引结构不受支持。')
        return names

    def snapshot(self):
        rows, files, messages, dbs, notes = {}, {}, {}, set(), []
        candidates, pointers = set(), {}
        # A custom registry permits only the named file, after header validation.
        self.registered, self.artifacts = set(), set()
        for root in self.sessions:
            candidates.update(root.glob('*.jsonl'))
            candidates.update(root.glob('*/*.jsonl'))
        if self.app == 'omp':
            for directory, terminal in [('custom-session-files',False),('terminal-sessions',True)]:
                for pointer in sorted((self.home/directory).glob('*')):
                    if not pointer.is_file():
                        continue
                    lines = self.safe(pointer).read_text(encoding='utf-8-sig').splitlines()
                    value = lines[1] if terminal and len(lines)>1 else lines[0] if not terminal and lines else ''
                    path = Path(value).expanduser()
                    if terminal and not path.is_absolute() and lines:
                        path = Path(lines[0])/path
                    if not value or not path.is_absolute() or path.suffix != '.jsonl':
                        notes.append('存在无法识别的自定义会话指针，已保留。')
                        continue
                    path = canonical_path(path)
                    if path.is_file():
                        self.registered.add(path)
                        candidates.add(path)
                        pointers.setdefault(path,set()).add(self.safe(pointer))
        for path in sorted(candidates):
            path = self.safe(path)
            if not path.is_file():
                continue
            header, title, first, recent = read_session(path)
            target = header.get('id')
            if not self.valid_id(target):
                raise CleanupError('无法识别会话 ID：'+str(path))
            if target in rows:
                if path in files[target]:
                    continue
                raise CleanupError('多个会话文件使用同一 ID，请先核对重复副本：'+target)
            owned = {path, *pointers.get(path,set())}
            if self.app == 'omp':
                raw_artifacts = path.with_suffix('')
                artifact = canonical_path(raw_artifacts)
                # Do not follow a junction/symlink into another project's files.
                if artifact != raw_artifacts:
                    raise CleanupError('Oh My Pi 附属目录是重定向路径，停止删除：'+str(raw_artifacts))
                self.artifacts.add(artifact)
                if artifact.is_dir():
                    for child in artifact.rglob('*'):
                        resolved = canonical_path(child)
                        if not resolved.is_relative_to(artifact):
                            raise CleanupError('Oh My Pi 附属文件超出所属会话目录。')
                        if child.is_file():
                            owned.add(resolved)
            rows[target] = self.base_row(target,title or first[:160],header.get('cwd',''),max(timestamp(header.get('timestamp')),path.stat().st_mtime))
            rows[target]['preview'] = first
            rows[target]['transcript'] = str(path)
            files[target] = owned
            messages[target] = recent
        if self.app == 'omp':
            for filename in ('history.db','stats.db'):
                db = self.home/filename
                if not db.is_file():
                    continue
                dbs.add(self.safe(db))
                with contextlib.closing(connect(db)) as con:
                    names = self.validate_db(con,stats=filename=='stats.db')
                    if filename == 'history.db':
                        for table in HISTORY_TABLES & names:
                            for record in con.execute(f'SELECT * FROM {quote(table)}'):
                                data = dict(record); target = data['session_id']
                                if not self.valid_id(target):
                                    continue
                                current = rows.setdefault(target,self.base_row(target))
                                if table == 'session_titles' and data.get('title'):
                                    current['title'] = data['title']
                                if not current.get('transcript'):
                                    current.update(status='残留记录',updated=max(current['updated'],timestamp(data.get('updated_at') or data.get('created_at'))))
        for target,current in rows.items():
            owned = files.get(target,set())
            current.update(files=len(owned),bytes=sum(p.stat().st_size for p in owned))
        stamps = [file_stamp(p) for p in sorted({p for group in files.values() for p in group})]
        for db in sorted(dbs):
            stamps.append(file_stamp(db))
            wal = Path(str(db)+'-wal')
            if wal.exists():
                stamps.append(file_stamp(wal))
        notes.append('Pi 与 Oh My Pi 可共用自定义会话文件；删除共享文件会同时影响两个应用，请先退出两者。')
        if self.app == 'omp':
            notes.append('清理本地 JSONL、会话附属文件、已识别的提示历史/标题/摘要和统计索引；保留账号、配置、Skills、共享 blobs、归档、备份及远端存储。')
        else:
            notes.append('清理默认目录及已配置会话目录中的 JSONL；保留账号、配置、Skills、项目文件和备份。')
        return {'rows':sorted(rows.values(),key=lambda r:r['updated'],reverse=True),'files':files,'shared':{},
                'databases':dbs,'session_databases':set(),'messages':messages,'warnings':notes,'fingerprint':digest([rows,stamps])}

    def preview(self, selected, backup=None):
        plan = super().preview(selected,backup)
        snap = self.snapshot()
        if plan['backup']['mode'] == 'custom':
            destination = canonical_path(plan['backup']['directory'])
            if any(destination == p or destination.is_relative_to(p) for p in self.artifacts):
                raise CleanupError('自定义备份目录不能位于会话附属目录内。')
        # Prevent a selected artifact tree from silently deleting another listed
        # transcript; explicit selection is required for nested conversations.
        selected_files = {p for i in plan['ids'] for p in snap['files'].get(i,set())}
        for row in snap['rows']:
            if row['id'] not in plan['ids'] and row.get('transcript') and Path(row['transcript']) in selected_files:
                raise CleanupError('所选会话包含另一会话，请一并选择：'+row['id'])
        return plan

    def mutate_db(self, path, ids):
        snap = self.snapshot()
        transcripts = [r['transcript'] for r in snap['rows'] if r['id'] in ids and r.get('transcript')]
        marks = ','.join('?' for _ in ids)
        with contextlib.closing(connect(self.safe(path),readonly=False)) as con:
            con.execute('PRAGMA foreign_keys=ON')
            con.execute('BEGIN IMMEDIATE')
            try:
                stats = path.name == 'stats.db'
                names = self.validate_db(con,stats=stats)
                if stats:
                    # Match exact paths and descendants of this transcript's
                    # artifact directory, with path normalization for Windows.
                    for table in STATS_TABLES & names:
                        for (value,) in con.execute(f'SELECT DISTINCT session_file FROM {quote(table)}').fetchall():
                            if not isinstance(value,str) or not Path(value).is_absolute():
                                continue
                            candidate = canonical_path(value)
                            if any(candidate == Path(p) or candidate.is_relative_to(Path(p).with_suffix('')) for p in transcripts):
                                con.execute(f'DELETE FROM {quote(table)} WHERE session_file=?',(value,))
                else:
                    for table in HISTORY_TABLES & names:
                        con.execute(f'DELETE FROM {quote(table)} WHERE session_id IN ({marks})',ids)
                    if 'history_fts' in names:
                        con.execute("INSERT INTO history_fts(history_fts) VALUES('rebuild')")
                if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or con.execute('PRAGMA foreign_key_check').fetchone():
                    raise CleanupError('Oh My Pi 索引完整性检查失败。')
                con.commit()
            except Exception:
                con.rollback()
                raise
