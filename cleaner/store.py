"""Read-only union inventory and offline, optionally backed-up Codex deletion.
Uses exact IDs, catalog revisions, non-local preservation and official RPC.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
from pathlib import Path, PureWindowsPath

from .processes import codex_processes
from .rpc import CodexRpc, RpcError, find_codex
from .backups import normalize_backup, check_pending, Operation

UUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", re.I)
FILE_ROOTS = {"sessions", "archived_sessions"}
RULES = {
    "catalog": {"local_thread_catalog": ("thread_id",), "thread_timeline_ledger": ("thread_id",),
                "local_thread_catalog_scan_entries": ("thread_id",), "inbox_items": ("thread_id",),
                "automation_runs": ("thread_id",), "live_visualization_suggestions": ("thread_id",)},
    "state": {"threads": ("id",), "thread_dynamic_tools": ("thread_id",),
              "thread_attachments": ("thread_id",), "thread_spawn_edges": ("parent_thread_id", "child_thread_id")},
    "history": {"thread_turns": ("thread_id",), "thread_items": ("thread_id",),
                "thread_history_projection_state": ("thread_id",), "thread_realtime_items": ("thread_id",)},
    "goals": {"thread_goals": ("thread_id",), "thread_goal_continuation_deferrals": ("thread_id",)},
    "memories": {"stage1_outputs": ("thread_id",)},
    "queue": {"queued_items": ("thread_id",), "queued_thread_revisions": ("thread_id",)},
}
PATTERNS = {"catalog": ["sqlite/*.db", "sqlite/*.sqlite"], "state": ["state_*.sqlite"],
            "history": ["thread_history_*.sqlite"], "goals": ["goals_*.sqlite"],
            "memories": ["memories_*.sqlite"], "queue": ["queue_*.sqlite"]}
REQUIRED = {"catalog": "local_thread_catalog", "state": "threads", "history": "thread_items",
            "goals": "thread_goals", "memories": "stage1_outputs", "queue": "queued_items"}
CATALOG_UNSCOPED = {"inbox_items", "automation_runs"}
CATALOG_AUXILIARIES = CATALOG_UNSCOPED | {"live_visualization_suggestions"}
CATALOG_AUX_REQUIRED = {
    "inbox_items": {"id", "thread_id"},
    "automation_runs": {"thread_id", "automation_id"},
    "live_visualization_suggestions": {"account_id", "user_id", "host_id", "thread_id", "id"},
}


class CleanupError(RuntimeError):
    pass


def windows_path_text(value):
    """Normalize only filesystem extended paths, never device namespaces."""
    if value.startswith('\\\\.\\'):
        raise CleanupError('不支持 Windows 设备路径。')
    if not value.startswith('\\\\?\\'):
        return value
    if value[:8].upper() == '\\\\?\\UNC\\':
        normal = '\\\\' + value[8:]
    elif re.match(r'^[a-zA-Z]:\\', value[4:]):
        normal = value[4:]
    else:
        raise CleanupError('不支持此 Windows 扩展路径命名空间。')
    # A verbatim trailing space/dot can name a different file from its DOS form.
    if any(p not in {'.', '..'} and p.endswith((' ', '.')) for p in PureWindowsPath(normal).parts[1:]):
        raise CleanupError('不支持末尾包含空格或句点的 Windows 扩展路径。')
    return normal


def canonical_path(path):
    path = Path(path).expanduser()
    if os.name == 'nt':
        windows_path_text(str(path))  # Reject device paths before resolution.
    resolved = path.resolve()  # Resolve junctions/symlinks before containment checks.
    return Path(windows_path_text(str(resolved))) if os.name == 'nt' else resolved


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      default=lambda x: {"bytes": x.hex()} if isinstance(x, bytes) else str(x))


def digest(value):
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(dumps(value), encoding="utf-8")
    temporary.replace(path)


def connect(path, readonly=True):
    con = sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
                          uri=True, timeout=8)
    con.row_factory = sqlite3.Row
    return con


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def tables(con):
    return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({quote(table)})")}


def validate_catalog_ownership(con, ids, local_host, alias="main"):
    """Legacy tables have no host field; refuse ambiguous cross-host thread IDs."""
    prefix = quote(alias) + "."
    names = {r[0] for r in con.execute(f"SELECT name FROM {prefix}sqlite_master WHERE type='table'")}
    marks = ",".join("?" for _ in ids)
    for table in sorted(CATALOG_UNSCOPED & names):
        ambiguous = con.execute(
            f"SELECT 1 FROM {prefix}{quote(table)} AS a WHERE a.thread_id IN ({marks}) "
            f"AND EXISTS (SELECT 1 FROM {prefix}local_thread_catalog AS c "
            "WHERE c.thread_id=a.thread_id AND c.host_id<>?) LIMIT 1", [*ids, local_host]
        ).fetchone()
        if ambiguous:
            raise CleanupError(f"catalog.{table} 存在同 ID 跨主机记录，无法确认归属；停止删除。")


def file_stamp(path):
    s = path.stat()
    return [str(path), s.st_size, s.st_mtime_ns]


class Store:
    def __init__(self, home: Path, process_provider=codex_processes, rpc_factory=CodexRpc):
        self.home = canonical_path(home)
        if not self.home.is_dir():
            raise CleanupError(f"Codex 数据目录不存在：{self.home}")
        self.process_provider = process_provider
        self.rpc_factory = rpc_factory
        self.lock = threading.Lock()
        self.backup_root = self.home / "backups" / "codex-thread-cleaner"

    def databases(self):
        result = {}
        for role, patterns in PATTERNS.items():
            matches = []
            for pattern in patterns:
                for path in self.home.glob(pattern):
                    if role != 'catalog' and not re.fullmatch(re.escape(pattern).replace(r'\*', r'[0-9]+'), path.name):
                        continue  # Historical backup names are never live databases.
                    path = self.inside(path)
                    with contextlib.closing(connect(path)) as con:
                        if REQUIRED[role] in tables(con):
                            matches.append(path)
            matches = sorted(set(matches))
            if len(matches) > 1:
                raise CleanupError(f"发现多个 {role} 数据库，停止写入：{matches}")
            if matches:
                result[role] = matches[0]
        if not result:
            raise CleanupError("没有找到受支持的 Codex 本地数据库。")
        return result

    def inside(self, path):
        resolved = canonical_path(path)
        if not resolved.is_relative_to(self.home) or resolved == self.home:
            raise CleanupError(f"路径超出 Codex 数据目录：{path}")
        return resolved

    def session_path(self, path):
        resolved = self.inside(path)
        if resolved.relative_to(self.home).parts[0] not in FILE_ROOTS or resolved.suffix != ".jsonl":
            raise CleanupError(f"会话文件路径不受支持：{path}")
        return resolved

    def file_inventory(self):
        files, warnings = {}, []
        for folder in FILE_ROOTS:
            for raw in sorted((self.home / folder).rglob("*.jsonl")):
                path = self.session_path(raw)
                try:
                    with path.open(encoding="utf-8-sig") as stream:
                        first = json.loads(stream.readline(2 * 1024 * 1024))
                    meta = first.get("payload", {}) if first.get("type") == "session_meta" else {}
                except (ValueError, UnicodeError):
                    meta = {}
                target = meta.get("id")
                if not target:
                    match = UUID.search(path.name)
                    target = match.group(0) if match else None
                if not target or not UUID.fullmatch(target):
                    warnings.append(f"无法识别归属的文件未列入删除：{path.name}")
                    continue
                files.setdefault(target, []).append({"path": str(path), "stamp": file_stamp(path),
                                                     "meta": {k: meta.get(k) for k in
                                                              ("cwd", "forked_from_id", "parent_thread_id")}})
        return files, warnings

    def snapshot(self):
        dbs = self.databases()
        rows, raw_data, cloud, local_host = {}, {}, 0, None
        for role, path in dbs.items():
            with contextlib.closing(connect(path)) as con:
                raw_data[role] = {}
                schema = list(con.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"))
                raw_data[role]["schema"] = [list(r) for r in schema]
                if role == "catalog":
                    for table in sorted(CATALOG_AUXILIARIES & tables(con)):
                        order = ",".join(quote(c) for c in sorted(columns(con, table)))
                        raw_data[role][table] = [list(r) for r in con.execute(
                            f"SELECT * FROM {quote(table)} ORDER BY {order}")]
                    hosts = [dict(r) for r in con.execute("SELECT * FROM local_thread_catalog_hosts")]
                    local = [h["host_id"] for h in hosts if h["host_kind"] == "local"]
                    if len(local) != 1:
                        raise CleanupError("目录库中本地主机不是唯一值。")
                    local_host = local[0]
                    host_kinds = {h["host_id"]: h["host_kind"] for h in hosts}
                    all_rows = [dict(r) for r in con.execute("SELECT * FROM local_thread_catalog ORDER BY host_id,thread_id")]
                    raw_data[role]["rows"] = all_rows
                    raw_data[role]["revision"] = list(con.execute("SELECT catalog_revision FROM local_thread_catalog_metadata WHERE id=1"))[0][0]
                    for item in all_rows:
                        if item["host_id"] != local_host:
                            cloud += int(host_kinds.get(item["host_id"]) == "chatgpt")
                            continue
                        rows[item["thread_id"]] = {"id": item["thread_id"], "title": item["display_title"],
                                                  "cwd": item.get("cwd") or "", "updated": item["source_updated_at"],
                                                  "catalog": True, "state": False, "archived": False, "preview": ""}
                if role == "state":
                    state = [dict(r) for r in con.execute("SELECT * FROM threads ORDER BY id")]
                    raw_data[role]["rows"] = state
                    for item in state:
                        target = item["id"]
                        base = rows.setdefault(target, {"id": target, "catalog": False})
                        display_title = base.get("title") or item.get("name") or item.get("title") or "未命名任务"
                        base.update(title=display_title if len(display_title) <= 160 else display_title[:157] + "…",
                                    cwd=item.get("cwd") or "", updated=item.get("updated_at") or 0,
                                    state=True, archived=bool(item.get("archived")),
                                    preview=item.get("preview") or item.get("first_user_message") or "",
                                    rollout=item.get("rollout_path"))
        files, warnings = self.file_inventory()
        for target, entries in files.items():
            base = rows.setdefault(target, {"id": target, "title": "仅有会话文件 · " + target[:8],
                "cwd": entries[0]["meta"].get("cwd") or "", "updated": entries[0]["stamp"][2] / 1e9,
                "catalog": False, "state": False, "archived": False, "preview": ""})
            base["files"] = len(entries)
        for base in rows.values():
            base.setdefault("files", 0)
            base["bytes"] = sum(f["stamp"][1] for f in files.get(base["id"], []))
            base["status"] = "已归档" if base["archived"] else ("正常" if base["files"] else "缺少会话文件")
            if not base["state"]:
                base["status"] = "残留记录" if base["catalog"] else "仅有文件"
        extra = []
        for name in ("session_index.jsonl", ".codex-global-state.json"):
            path = self.home / name
            if path.exists():
                extra.append(file_stamp(self.inside(path)))
        fingerprint = digest({"db": raw_data, "files": files, "extra": extra})
        return {"rows": sorted(rows.values(), key=lambda r: r["updated"], reverse=True),
                "fingerprint": fingerprint, "chatgpt": cloud, "files": files,
                "local_host": local_host, "databases": {k: str(v) for k,v in dbs.items()},
                "warnings": warnings}

    def inventory(self):
        snap = self.snapshot()
        return {k: v for k,v in snap.items() if k not in {"files", "local_host"}} | {
            "home": str(self.home), "processes": self.process_provider(), "cli": find_codex(),
            "backup_root": str(self.backup_root)}

    def preview(self, selected, backup=None):
        backup=normalize_backup(self,backup)
        if not isinstance(selected, list) or not selected or len(selected) > 500:
            raise CleanupError("请选择 1–500 个本地任务。")
        if any(not isinstance(i, str) or not UUID.fullmatch(i) for i in selected):
            raise CleanupError("任务 ID 格式错误。")
        ids = set(selected)
        snap = self.snapshot()
        by_id = {r["id"]: r for r in snap["rows"]}
        if not ids <= by_id.keys():
            raise CleanupError("选中的任务已经变化，请刷新列表。")
        self.validate_rules(snap["databases"])
        if "catalog" in snap["databases"]:
            with contextlib.closing(connect(Path(snap["databases"]["catalog"]))) as con:
                validate_catalog_ownership(con, sorted(ids), snap["local_host"])
        # A kept fork may need its parent's source rollout. Require the user to
        # include dependent forks instead of silently breaking their histories.
        dependent = []
        for child, entries in snap["files"].items():
            if child not in ids and any(e["meta"].get("forked_from_id") in ids for e in entries):
                dependent.append(by_id[child]["title"] + " (" + child + ")")
        if dependent:
            raise CleanupError("以下派生任务可能引用所选任务的历史；请一并选择或保留父任务：\n" + "\n".join(dependent))
        for target in ids:
            rollout = by_id[target].get("rollout")
            if rollout and Path(rollout).exists():
                path = self.session_path(Path(rollout))
                own = {e["path"] for e in snap["files"].get(target, [])}
                if str(path) not in own:
                    raise CleanupError("主记录与会话文件归属不一致，停止删除：" + target)
        return {"ids": sorted(ids), "rows": [by_id[i] for i in sorted(ids)],
                "fingerprint": snap["fingerprint"], "chatgpt": snap["chatgpt"],
                "file_count": sum(by_id[i]["files"] for i in ids),
                "bytes": sum(by_id[i]["bytes"] for i in ids), "home": str(self.home), "backup":backup}

    def assert_offline(self):
        running = self.process_provider()
        if running:
            names = ", ".join(f"{r['name']} (PID {r['pid']})" for r in running[:8])
            raise CleanupError("请先退出 Codex 桌面端和 Codex CLI，再执行删除。检测到：" + names)

    @contextlib.contextmanager
    def exclusive(self):
        self.backup_root.mkdir(parents=True, exist_ok=True)
        with (self.backup_root / "operation.lock").open("a+b") as handle:
            # Windows byte locks may extend past EOF. Do not read a byte that
            # another process may already have locked.
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    raise CleanupError("另一个清理器正在操作此目录。") from error
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    raise CleanupError("另一个清理器正在操作此目录。") from error
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def backup(self, snap, run, ids):
        mapping = []
        for role, source in snap["databases"].items():
            source = self.inside(Path(source))
            destination = run / "databases" / source.relative_to(self.home)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.closing(connect(source)) as a, contextlib.closing(sqlite3.connect(destination)) as b:
                if a.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise CleanupError(f"数据库完整性检查失败：{source.name}")
                a.backup(b)
                if b.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise CleanupError("备份完整性检查失败。")
            mapping.append({"source": str(source), "backup": str(destination), "kind": "database"})
        paths = {Path(e["path"]) for i in ids for e in snap["files"].get(i, [])}
        paths |= {self.home / name for name in ("session_index.jsonl", ".codex-global-state.json")
                  if (self.home / name).exists()}
        for source in sorted(paths):
            source = self.inside(source)
            destination = run / "files" / source.relative_to(self.home)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            with source.open('rb') as original, destination.open('rb') as copied:
                matches = hashlib.file_digest(original, 'sha256').digest() == hashlib.file_digest(copied, 'sha256').digest()
            if not matches:
                raise CleanupError("文件备份校验失败：" + str(source))
            mapping.append({"source": str(source), "backup": str(destination), "kind": "file"})
        return mapping

    def validate_rules(self, dbs):
        for role, path in dbs.items():
            with contextlib.closing(connect(Path(path))) as con:
                for table in tables(con):
                    cols = columns(con, table)
                    if cols & {"thread_id", "parent_thread_id", "child_thread_id"} and table not in RULES[role]:
                        raise CleanupError(f"发现未适配的任务关联表 {role}.{table}；停止删除。")
                    if table in RULES[role] and not set(RULES[role][table]) <= cols:
                        raise CleanupError(f"任务表结构已变化：{role}.{table}")
                    if role == "catalog" and table in RULES[role]:
                        if ("host_id" in cols) != (table not in CATALOG_UNSCOPED):
                            raise CleanupError(f"任务表主机范围结构已变化：{role}.{table}")
                        if table in CATALOG_AUX_REQUIRED and (
                            not CATALOG_AUX_REQUIRED[table] <= cols
                            or cols & {"parent_thread_id", "child_thread_id"}
                        ):
                            raise CleanupError(f"任务表结构已变化：{role}.{table}")

    def delete_remnants(self, snap, ids):
        dbs = {k: Path(v) for k,v in snap["databases"].items()}
        self.validate_rules(dbs)
        roles = list(dbs)
        with contextlib.closing(connect(dbs[roles[0]], readonly=False)) as con:
            aliases = {roles[0]: "main"}
            for index, role in enumerate(roles[1:], 1):
                alias = f"db{index}"
                con.execute(f"ATTACH DATABASE ? AS {alias}", (str(dbs[role]),))
                aliases[role] = alias
            con.execute("BEGIN IMMEDIATE")
            try:
                catalog_alias = aliases.get("catalog")
                cloud_before = []
                if catalog_alias:
                    validate_catalog_ownership(con, ids, snap["local_host"], catalog_alias)
                    cloud_before = [list(r) for r in con.execute(f"SELECT * FROM {catalog_alias}.local_thread_catalog WHERE host_id<>? ORDER BY host_id,thread_id", (snap["local_host"],))]
                marks = ",".join("?" for _ in ids)
                counts = {}
                for role, alias in aliases.items():
                    names = {r[0] for r in con.execute(f"SELECT name FROM {alias}.sqlite_master WHERE type='table'")}
                    for table, cols in RULES[role].items():
                        if table not in names:
                            continue
                        condition = " OR ".join(f"{quote(c)} IN ({marks})" for c in cols)
                        parameters = list(ids) * len(cols)
                        if role == "catalog" and table not in CATALOG_UNSCOPED:
                            condition = f"host_id=? AND ({condition})"
                            parameters.insert(0, snap["local_host"])
                        count = con.execute(f"DELETE FROM {alias}.{quote(table)} WHERE {condition}", parameters).rowcount
                        counts[f"{role}.{table}"] = count
                    if role == "catalog":
                        con.execute(f"UPDATE {alias}.local_thread_catalog_metadata SET catalog_revision=catalog_revision+1 WHERE id=1")
                    if con.execute(f"PRAGMA {alias}.integrity_check").fetchone()[0] != "ok":
                        raise CleanupError(f"删除事务完整性检查失败：{role}")
                if catalog_alias:
                    cloud_after = [list(r) for r in con.execute(f"SELECT * FROM {catalog_alias}.local_thread_catalog WHERE host_id<>? ORDER BY host_id,thread_id", (snap["local_host"],))]
                    if cloud_before != cloud_after:
                        raise CleanupError("非本地主机数据发生变化，回滚。")
                con.commit()
                return counts
            except Exception:
                con.rollback()
                raise

    def prune_files(self, before, ids):
        for target in ids:
            for entry in before["files"].get(target, []):
                path = self.session_path(Path(entry["path"]))
                if path.exists():
                    path.unlink()
        index = self.home / "session_index.jsonl"
        if index.exists():
            original = index.read_text(encoding="utf-8")
            kept = []
            for line in original.splitlines(keepends=True):
                try:
                    record = json.loads(line)
                except ValueError:
                    kept.append(line)
                    continue
                if record.get("id") not in ids:
                    kept.append(line)
            temporary = index.with_suffix(".cleaner.tmp")
            temporary.write_text("".join(kept), encoding="utf-8")
            temporary.replace(index)
        global_path = self.home / ".codex-global-state.json"
        if global_path.exists():
            data = json.loads(global_path.read_text(encoding="utf-8"))
            def prune(value):
                if isinstance(value, dict):
                    return {k: prune(v) for k,v in value.items() if k not in ids}
                if isinstance(value, list):
                    return [prune(v) for v in value if not (isinstance(v, str) and v in ids)]
                return value
            for key in ["thread-titles", "pinned-thread-ids", "thread-writable-roots", "thread-project-assignments",
                        "sidebar-project-thread-orders", "queued-follow-ups", "electron-thread-read-state-v1"]:
                if key in data:
                    data[key] = prune(data[key])
            atomic_json(global_path, data)

    def apply(self, plan):
        with self.lock, self.exclusive():
            self.assert_offline()
            if plan.get("home") != str(self.home):
                raise CleanupError("删除计划不属于当前数据目录。")
            fresh = self.preview(plan["ids"],backup=plan.get('backup'))
            if fresh["fingerprint"] != plan["fingerprint"]:
                raise CleanupError("预览后任务记录发生了变化。请刷新并重新预览。")
            check_pending(self)
            before = self.snapshot()
            self.validate_rules(before["databases"])
            ids = set(plan["ids"])
            operation=Operation(self,plan);run=operation.run
            mapping = self.backup(before, run, ids) if run is not None else []
            record = {"status": "prepared", "plan": plan, "backups": mapping, "official": {}, **operation.fields()}
            operation.save(record)
            self.assert_offline()
            if self.snapshot()["fingerprint"] != plan["fingerprint"]:
                raise CleanupError("准备过程中任务发生变化，请重新预览。")
            record["status"] = "executing"
            operation.save(record)
            try:
                # RPC may delete normal tasks directly; corrupt/missing rollouts
                # can fail and are handled by exact-ID cleanup below.
                if self.rpc_factory is not None:
                    try:
                        with self.rpc_factory(self.home) as rpc:
                            for target in sorted(ids):
                                try:
                                    rpc.call("thread/delete", {"threadId": target})
                                    record["official"][target] = "deleted"
                                except RpcError as error:
                                    record["official"][target] = str(error)
                    except (RpcError, OSError) as error:
                        record["official"]["unavailable"] = str(error)
                self.assert_offline()
                # The official API may initialize optional databases. Rediscover
                # schemas before clearing residual records.
                after_api = self.snapshot()
                record["rows_removed"] = self.delete_remnants(after_api, sorted(ids))
                for target in ids:
                    paths = {e['path']: e for e in before['files'].get(target, [])}
                    paths.update({e['path']: e for e in after_api['files'].get(target, [])})
                    before['files'][target] = list(paths.values())
                self.prune_files(before, ids)
                after = self.snapshot()
                remaining = {r["id"] for r in after["rows"]}
                expected = {r["id"] for r in before["rows"]} - ids
                if remaining != expected or after["chatgpt"] != before["chatgpt"]:
                    raise CleanupError("删除后保留任务集合或 ChatGPT 数量与计划不一致。")
                integrity = {}
                for role, path in after["databases"].items():
                    with contextlib.closing(connect(Path(path))) as con:
                        integrity[role] = con.execute("PRAGMA integrity_check").fetchone()[0]
                if set(integrity.values()) != {"ok"}:
                    raise CleanupError("删除后数据库检查失败。")
                record.update(status="complete", deleted=len(ids), remaining=len(remaining),
                              chatgpt_before=before["chatgpt"], chatgpt_after=after["chatgpt"], integrity=integrity)
                operation.save(record)
                return record
            except Exception as error:
                if run is None:
                    record.update(status='failed_no_backup',error=str(error));operation.save(record)
                    raise CleanupError(f"操作未完成，状态 failed_no_backup。本次未备份，已发生的删除无法自动恢复；请刷新核对。操作记录：{operation.journal}\n原因：{error}") from error
                record.update(status="recovery_required", error=str(error))
                try:
                    self.assert_offline()
                    for entry in mapping:
                        source, backup = Path(entry['source']), Path(entry['backup'])
                        source.parent.mkdir(parents=True, exist_ok=True)
                        if entry['kind'] == 'database':
                            with contextlib.closing(connect(backup)) as a, contextlib.closing(connect(source, readonly=False)) as b:
                                a.backup(b)
                        else:
                            shutil.copy2(backup, source)
                    record['status'] = 'rolled_back'
                except Exception as rollback_error:
                    record['rollback_error'] = str(rollback_error)
                operation.save(record)
                raise CleanupError(f"操作未完成，状态 {record['status']}，备份和逐项结果：{run}\n原因：{error}") from error

    def detail(self, target):
        snap = self.snapshot()
        row = next((r for r in snap["rows"] if r["id"] == target), None)
        if row is None:
            raise CleanupError("任务不存在。")
        messages = []
        path = snap["databases"].get("history")
        if path:
            with contextlib.closing(connect(Path(path))) as con:
                records = con.execute("SELECT item_json FROM thread_items WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 100", (target,)).fetchall()
                for record in reversed(records):
                    try:
                        item = json.loads(record[0])
                    except ValueError:
                        continue
                    if item.get("type") not in {"userMessage", "agentMessage"}:
                        continue
                    content = item.get("text") or "\n".join(c.get("text", "") for c in item.get("content", []) if isinstance(c, dict))
                    messages.append({"role": "用户" if item["type"] == "userMessage" else "助手", "text": content[:6000]})
        return {"row": row, "messages": messages[-20:], "files": [e["path"] for e in snap["files"].get(target, [])]}
