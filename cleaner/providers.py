"""App-specific, local-only storage adapters. All deletion targets are derived
from recognized session IDs, never from user-supplied arbitrary paths or SQL.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import shutil
import sqlite3
import threading
from pathlib import Path
from urllib.parse import unquote

from .processes import cli_processes, dsh_processes
from .protobuf import summary_map
from .backups import normalize_backup, check_pending, Operation
from .store import CleanupError, Store, UUID, atomic_json, connect, digest, file_stamp, quote, tables

LABELS = {"codex":"Codex", "claude":"Claude Code", "grok":"Grok Build", "cursor":"Cursor", "antigravity":"Antigravity", "deepseek":"DeepSeek Harness"}
PROCESSES = {"claude":{"claude.exe", "claude-code.exe"}, "grok":{"grok.exe", "grok-build.exe"},
             "cursor":{"cursor.exe", "cursor-agent.exe"},
             "antigravity":{"antigravity.exe", "agy.exe", "language_server_windows_x64.exe"}}


def read_json(path, fallback=None):
    if not path.is_file():
        return {} if fallback is None else fallback
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as error:
        raise CleanupError("JSON 文件格式异常：" + str(path)) from error


def json_lines(path):
    if path.is_file():
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        yield value
                except ValueError:
                    continue


def text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def timestamp(value):
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else value
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def prune_json(value, ids):
    """Prune recognized identity fields and exact ID map entries, never substrings."""
    identity = ("sessionId", "session_id", "composerId", "conversationId", "conversation_id")
    if isinstance(value, dict):
        result = {}
        for k,v in value.items():
            if k in ids or (isinstance(v, dict) and any(v.get(field) in ids for field in identity)):
                continue
            result[k] = prune_json(v, ids)
        return result
    if isinstance(value, list):
        return [prune_json(v, ids) for v in value
                if not (isinstance(v, str) and v in ids)
                and not (isinstance(v, dict) and any(v.get(field) in ids for field in identity))]
    return value


class LocalProvider:
    def __init__(self, app, profile=None, process_provider=None):
        self.app = app
        self.label = LABELS[app]
        self.profile = (profile or Path.home()).resolve()
        roaming = self.profile / "AppData/Roaming"
        self.roots = {
            "claude": [self.profile/".claude"],
            "grok": [self.profile/".grok"],
            "cursor": [roaming/"Cursor/User", self.profile/".cursor/projects"],
            "antigravity": [self.profile/".gemini/antigravity", self.profile/".gemini/antigravity-ide", roaming/"Antigravity/User"],
            "deepseek": [self.profile/".dsh"],
        }[app]
        self.home = self.roots[0]
        self.backup_root = self.profile / "AppData/Local/AIConversationCleaner/backups" / app
        self.process_provider = process_provider or (dsh_processes if app=='deepseek' else lambda: cli_processes(app,PROCESSES[app]))
        self.lock = threading.Lock()

    exclusive = Store.exclusive

    def valid_id(self, target):
        if not isinstance(target,str):return False
        if self.app=='deepseek' and target.startswith('session-'):target=target[8:]
        return bool(UUID.fullmatch(target))

    def safe(self, path):
        resolved = path.resolve()
        if not any(resolved.is_relative_to(r) and resolved != r for r in self.roots):
            raise CleanupError("文件路径超出已识别的会话目录：" + str(path))
        return resolved

    def assert_offline(self):
        running = self.process_provider()
        if running:
            raise CleanupError(f"请先退出 {self.label}，再执行删除。检测到：" + ", ".join(f"{p['name']} (PID {p['pid']})" for p in running[:8]))

    def base_row(self, target, title="", cwd="", updated=0, archived=False):
        return {"id":target,"title":title or "未命名会话 · "+target[:8],"cwd":cwd,"updated":updated,
                "archived":bool(archived),"preview":"","files":0,"bytes":0,"status":"正常"}

    def snapshot(self):
        rows, files, dbs, shared, messages, notes = {}, {}, set(), {}, {}, []
        def row(target, **kwargs):
            if not self.valid_id(target):
                return None
            r = rows.setdefault(target,self.base_row(target))
            r.update({k:v for k,v in kwargs.items() if v not in (None, "")})
            return r
        def file(target, path):
            if row(target) is not None and path.is_file():
                files.setdefault(target, set()).add(self.safe(path))
        def directory(target, folder):
            if folder.is_dir():
                for p in folder.rglob("*"):
                    if p.is_file(): file(target,p)
        if self.app == "claude":
            root=self.roots[0]
            history=root/"history.jsonl"
            if history.exists():
                shared[history] = "jsonl:sessionId"
                for item in json_lines(history):
                    target=item.get("sessionId")
                    current=row(target)
                    if current:
                        prompt = str(item.get("display") or "").strip()
                        if prompt and not prompt.startswith('/') and prompt.lower() not in {'exit','quit','clear'} and current['title'].startswith('未命名会话'):
                            current.update(title=prompt[:180],preview=prompt[:500])
                        if timestamp(item.get("timestamp"))>=current["updated"]:
                            current.update(cwd=item.get("project") or "",updated=timestamp(item.get("timestamp")))
            for p in (root/"projects").glob("*/*.jsonl"):
                target=p.stem
                if row(target) is None:continue
                file(target,p);directory(target,p.with_suffix(""))
                preview=[]
                for item in json_lines(p):
                    if item.get("sessionId") not in (None,target):
                        raise CleanupError("Claude 会话 ID 与文件名不一致："+str(p))
                    if item.get("cwd"):row(target,cwd=item["cwd"])
                    if item.get("type") in {"user","assistant"}:
                        content=text_content(item.get("message",{}).get("content",[]))
                        if content:preview.append({"role":"用户" if item["type"]=="user" else "助手","text":content[:6000]})
                messages[target]=preview[-20:]
                if preview:row(target,title=rows[target]["title"] if not rows[target]["title"].startswith("未命名会话") else preview[0]["text"][:120],preview=preview[0]["text"][:500])
                row(target,updated=max(rows[target]["updated"],p.stat().st_mtime))
            for p in (root/"projects").glob("*/sessions-index.json"):
                shared[p]="json"
                for item in read_json(p).get("entries",[]):
                    row(item.get("sessionId"),title=item.get("summary") or item.get("firstPrompt"),cwd=item.get("projectPath"),updated=timestamp(item.get("modified")))
            for target in list(rows):
                for project in (root/"projects").iterdir() if (root/"projects").exists() else []:
                    if project.is_dir():
                        for p in project.glob(target+".*"):file(target,p)
                for folder in ("sessions","session-env","file-history","todos"):
                    directory(target,root/folder/target)
                    if (root/folder).is_dir():
                        for p in (root/folder).glob(target+"*"):
                            if p.is_file() and (p.stem==target or p.name.startswith(target+"-")):file(target,p)
        elif self.app == "grok":
            root=self.roots[0];sessions=root/"sessions"
            for p in sessions.rglob("summary.json"):
                target=p.parent.name
                if row(target) is None:continue
                info=read_json(p);row(target,title=info.get("generated_title") or info.get("session_summary"),cwd=info.get("info",{}).get("cwd") or unquote(p.parent.parent.name),updated=timestamp(info.get("updated_at")))
                directory(target,p.parent)
                msgs=[]
                for item in json_lines(p.parent/"chat_history.jsonl"):
                    if item.get("type") in {"user","assistant"}:
                        content=text_content(item.get("content"))
                        if content:msgs.append({"role":"用户" if item["type"]=="user" else "助手","text":content[:6000]})
                messages[target]=msgs[-20:]
            # Include empty session directories and orphan indexed sessions.
            for p in sessions.glob("*/*"):
                if p.is_dir() and UUID.fullmatch(p.name):
                    row(p.name,cwd=unquote(p.parent.name),updated=rows.get(p.name,{}).get("updated") or p.stat().st_mtime);directory(p.name,p)
            search=sessions/"session_search.sqlite"
            if search.exists():
                dbs.add(self.safe(search))
                with contextlib.closing(connect(search)) as con:
                    if "session_docs" not in tables(con):raise CleanupError("Grok 搜索索引结构已变化。")
                    for item in con.execute("SELECT session_id,title,cwd,updated_at FROM session_docs"):
                        row(item[0],title=item[1],cwd=item[2],updated=item[3])
            for p in sessions.glob("*/prompt_history.jsonl"):shared[p]="jsonl:session_id"
            active=root/"active_sessions.json"
            if active.exists():shared[active]="json"
        elif self.app == "cursor":
            root,projects=self.roots
            paths=[root/"globalStorage/state.vscdb",*list((root/"workspaceStorage").glob("*/state.vscdb"))]
            for p in paths:
                if not p.is_file():continue
                dbs.add(self.safe(p))
                with contextlib.closing(connect(p)) as con:
                    names=tables(con)
                    if "composerHeaders" in names:
                        for item in con.execute("SELECT composerId,value FROM composerHeaders"):
                            info=json.loads(item[1]);row(item[0],title=info.get("name"),cwd=info.get("workspaceIdentifier",{}).get("id"),updated=timestamp(info.get("lastUpdatedAt") or info.get("createdAt")),archived=info.get("isArchived",False))
                    if "cursorDiskKV" in names:
                        for key,value in con.execute("SELECT key,value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"):
                            target=key.split(":",1)[1]
                            if row(target) is None:continue
                            info=json.loads(value);row(target,title=info.get("name"),updated=timestamp(info.get("lastUpdatedAt") or info.get("createdAt")),archived=info.get("isArchived",False))
                            msgs=[]
                            for h in info.get("fullConversationHeadersOnly",[]):
                                if h.get("grouping",{}).get("textPreview"):
                                    msgs.append({"role":"用户" if h.get("type")==1 else "助手","text":h["grouping"]["textPreview"]})
                            messages[target]=msgs[-20:]
                            if msgs:row(target,preview=msgs[0]["text"][:500])
                    if "ItemTable" in names:
                        for key,value in con.execute("SELECT key,value FROM ItemTable WHERE key IN ('composer.composerData','workbench.backgroundComposer.workspacePersistentData')"):
                            try:info=json.loads(value)
                            except ValueError:raise CleanupError("Cursor 会话索引 JSON 无法读取。")
                            for item in info.get("allComposers",[]):
                                row(item.get("composerId"),title=item.get("name"),updated=timestamp(item.get("lastUpdatedAt") or item.get("createdAt")),archived=item.get("isArchived",False))
            for p in projects.glob("*/agent-transcripts/**/*"):
                if not p.is_file():continue
                target=next((part for part in reversed(p.relative_to(projects).parts) if UUID.fullmatch(part)),None)
                target=target or (p.stem if UUID.fullmatch(p.stem) else None)
                if target:file(target,p)
            notes.append("Cursor 共享内容缓存和工作区文件保留；删除任务条目、独立消息与会话转录。")
        elif self.app == "deepseek":
            root=self.roots[0]
            def projection(target, info):
                if row(target) is None:return
                identity=info.get('identity',{});values=info.get('rows',{})
                title=values.get('title',{}).get('val')
                first=(values.get('titleInput',{}).get('val') or {}).get('first')
                prompt=first.get('text','') if isinstance(first,dict) else first
                metadata=values.get('sessionListMetadata',{}).get('val') or {}
                row(target,title=title or (prompt[:160] if prompt else None),cwd=identity.get('cwd'),
                    updated=timestamp(metadata.get('lastPromptAt') or identity.get('createdAt')),preview=(prompt or '')[:500])
                if prompt:messages[target]=[{'role':'用户（首条摘要）','text':prompt[:6000]}]
            cache=root/'storages/session_projcache.json'
            if cache.exists():
                info=read_json(cache)
                if info.get('unit')!={'name':'session_projcache','version':3} or not isinstance(info.get('tables',{}).get('sessions'),dict):
                    raise CleanupError('DeepSeek Harness 共享摘要格式不受支持。')
                shared[cache]='dsh-cache'
                for target,value in info['tables']['sessions'].items():projection(target,value)
            for p in (root/'storages/session_projcache/sessions').glob('*.json'):
                if not self.valid_id(p.stem):continue
                info=read_json(p)
                if info.get('version') not in {5,7} or not isinstance(info.get('record'),dict):
                    raise CleanupError('DeepSeek Harness 会话摘要格式不受支持：'+str(p))
                projection(p.stem,info['record']);file(p.stem,p)
            for folder in (root/'sessions').glob('*/*'):
                if folder.is_dir() and self.valid_id(folder.name):
                    row(folder.name);directory(folder.name,folder)
            workspace=root/'storages/workspace.json'
            if workspace.exists():
                info=read_json(workspace)
                if info.get('unit')!={'name':'workspace','version':2} or not isinstance(info.get('tables',{}).get('workspaces'),dict):
                    raise CleanupError('DeepSeek Harness 工作区索引格式不受支持。')
                if info.get('global',{}).get('pendingMutation'):raise CleanupError('DeepSeek Harness 工作区存在未完成写入，请先在原应用恢复。')
                shared[workspace]='dsh-workspace'
                for entry in info['tables']['workspaces'].values():
                    for target in entry.get('sessionIds',[]):row(target,cwd=entry.get('path'))
                for target in info.get('global',{}).get('archivedSessionIds',[]):row(target,archived=True)
            notes.append('DeepSeek Harness 显示本地索引标题与首条提示摘要；压缩日志整体删除，不解压正文。历史备份与 .deleted-sessions 保留。')
        else:
            root,ide,user=self.roots
            summary=root/"conversation_summaries.db"
            if summary.exists():
                dbs.add(self.safe(summary))
                with contextlib.closing(connect(summary)) as con:
                    if "conversation_summaries" not in tables(con):raise CleanupError("Antigravity 摘要库结构已变化。")
                    for r in con.execute("SELECT conversation_id,title,preview,last_modified_time,workspace_uris FROM conversation_summaries"):
                        row(r[0],title=r[1],preview=r[2],updated=timestamp(r[3]),cwd=r[4])
            for p in [user/"globalStorage/state.vscdb",*list((user/"workspaceStorage").glob("*/state.vscdb"))]:
                if not p.is_file():continue
                dbs.add(self.safe(p))
                with contextlib.closing(connect(p)) as con:
                    if "ItemTable" not in tables(con):raise CleanupError("Antigravity 本地索引表不存在。")
                    entry=con.execute("SELECT value FROM ItemTable WHERE key='antigravityUnifiedStateSync.trajectorySummaries'").fetchone()
                    if entry:
                        items,_=summary_map(entry[0])
                        for target,title in items.items():row(target,title=title)
            for base in (root,ide):
                for p in (base/"conversations").glob("*.pb"):
                    if row(p.stem) is None:continue
                    file(p.stem,p);row(p.stem,updated=max(rows[p.stem]["updated"],p.stat().st_mtime))
                for p in (base/"annotations").glob("*.pbtxt"):
                    if UUID.fullmatch(p.stem):file(p.stem,p)
            for target in list(rows):
                for base in (root,ide):
                    for folder in ("brain","browser_recordings"):
                        directory(target,base/folder/target)
                    brain=base/"brain"/target/"task.md"
                    if brain.exists() and rows[target]["title"].startswith("未命名会话"):
                        first=brain.read_text(encoding="utf-8",errors="replace").splitlines()
                        if first:row(target,title=first[0].lstrip("# ")[:140])
            notes.append("Antigravity 会话正文为二进制格式；优先显示本地摘要，缺少摘要时显示任务 ID。")
        for target,r in rows.items():
            ps=files.get(target,set());r["files"]=len(ps);r["bytes"]=sum(p.stat().st_size for p in ps)
            if not r["updated"] and ps:r["updated"]=max(p.stat().st_mtime for p in ps)
            r["status"]="已归档" if r["archived"] else ("正常" if ps or self.app=="cursor" else "残留记录")
        stamps=[file_stamp(p) for p in sorted(set(shared)|{p for ps in files.values() for p in ps})]
        db_stamps=[]
        for p in sorted(dbs):
            db_stamps.append(file_stamp(p))
            wal=Path(str(p)+"-wal")
            if wal.exists():db_stamps.append(file_stamp(wal))
        return {"rows":sorted(rows.values(),key=lambda r:r["updated"],reverse=True),"files":files,"shared":shared,
                "databases":dbs,"messages":messages,"fingerprint":digest([rows,stamps,db_stamps]),"warnings":notes}

    def inventory(self):
        snap=self.snapshot()
        return {"rows":snap["rows"],"fingerprint":snap["fingerprint"],"home":"；".join(str(p) for p in self.roots),
                "chatgpt":0,"processes":self.process_provider(),"cli":None,"backup_root":str(self.backup_root),"warnings":snap["warnings"]}

    def preview(self, selected, backup=None):
        backup=normalize_backup(self,backup)
        if not isinstance(selected,list) or not 0<len(selected)<=500 or any(not self.valid_id(i) for i in selected):
            raise CleanupError("请选择 1–500 个有效会话。")
        snap=self.snapshot();by_id={r["id"]:r for r in snap["rows"]};ids=set(selected)
        if not ids<=by_id.keys():raise CleanupError("选中的会话已变化，请刷新。")
        if self.app=='grok':
            for target,paths in snap['files'].items():
                if target not in ids and any(any(i in p.parts for i in ids) for p in paths):
                    raise CleanupError('所选 Grok 会话包含子会话，请一并选择：'+target)
        return {"ids":sorted(ids),"rows":[by_id[i] for i in sorted(ids)],"fingerprint":snap["fingerprint"],
                "file_count":sum(by_id[i]["files"] for i in ids),"bytes":sum(by_id[i]["bytes"] for i in ids),"home":str(self.home),"app":self.app,"backup":backup}

    def detail(self,target):
        snap=self.snapshot();r=next((r for r in snap["rows"] if r["id"]==target),None)
        if not r:raise CleanupError("会话不存在。")
        return {"row":r,"messages":snap["messages"].get(target,[]),"files":[str(p) for p in sorted(snap["files"].get(target,set()))]}

    def mutate_db(self,path,ids):
        marks=','.join('?' for _ in ids)
        with contextlib.closing(connect(path,readonly=False)) as con:
            con.execute('BEGIN IMMEDIATE')
            try:
                names=tables(con)
                if self.app=='grok':
                    # Existing FTS triggers remove the matching full-text rows.
                    con.execute(f'DELETE FROM session_docs WHERE session_id IN ({marks})',ids)
                    if 'session_docs_fts' in names:
                        con.execute("INSERT INTO session_docs_fts(session_docs_fts) VALUES('rebuild')")
                elif self.app=='cursor':
                    if 'composerHeaders' in names:con.execute(f'DELETE FROM composerHeaders WHERE composerId IN ({marks})',ids)
                    if 'cursorDiskKV' in names:
                        for target in ids:
                            con.execute("DELETE FROM cursorDiskKV WHERE key=? OR key LIKE ? OR key LIKE ? OR key LIKE ?",('composerData:'+target,'bubbleId:'+target+':%','checkpointId:'+target+':%','inlineDiff:'+target+':%'))
                            con.execute('DELETE FROM cursorDiskKV WHERE key=?',('composerVirtualRowHeights:'+target,))
                    if 'ItemTable' in names:
                        for key,value in list(con.execute("SELECT key,value FROM ItemTable WHERE key IN ('composer.composerData','workbench.backgroundComposer.workspacePersistentData','workbench.backgroundComposer.persistentData')")):
                            con.execute('UPDATE ItemTable SET value=? WHERE key=?',(json.dumps(prune_json(json.loads(value),set(ids)),ensure_ascii=False),key))
                elif self.app=='antigravity':
                    if 'conversation_summaries' in names:con.execute(f'DELETE FROM conversation_summaries WHERE conversation_id IN ({marks})',ids)
                    if 'ItemTable' in names:
                        item=con.execute("SELECT value FROM ItemTable WHERE key='antigravityUnifiedStateSync.trajectorySummaries'").fetchone()
                        if item:
                            _,value=summary_map(item[0],set(ids));con.execute("UPDATE ItemTable SET value=? WHERE key='antigravityUnifiedStateSync.trajectorySummaries'",(value,))
                if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise CleanupError('数据库检查失败：'+str(path))
                con.commit()
            except Exception:
                con.rollback();raise

    def apply(self,plan):
        with self.lock,self.exclusive():
            self.assert_offline()
            check_pending(self)
            if plan.get('app')!=self.app or plan.get('home')!=str(self.home):raise CleanupError('删除计划对应的应用已变化。')
            self.preview(plan['ids'],backup=plan.get('backup'))
            before=self.snapshot();ids=set(plan['ids']);expected={r['id'] for r in before['rows']}-ids
            if before['fingerprint']!=plan['fingerprint']:raise CleanupError('会话在预览后发生变化，请重新预览。')
            if not ids<={r['id'] for r in before['rows']}:raise CleanupError('删除目标不存在。')
            operation=Operation(self,plan);run=operation.run
            mapping=[]
            targets=set(before['shared'])|{p for i in ids for p in before['files'].get(i,set())}
            for n,p in enumerate(sorted(targets|before['databases']) if run is not None else []):
                p=self.safe(p);dest=run/'data'/f'{n:05d}'/p.name;dest.parent.mkdir(parents=True)
                if p in before['databases']:
                    with contextlib.closing(connect(p)) as a,contextlib.closing(sqlite3.connect(dest)) as b:
                        if a.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise CleanupError('源数据库检查失败。')
                        a.backup(b)
                        if b.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise CleanupError('备份数据库检查失败。')
                else:shutil.copy2(p,dest)
                mapping.append({'source':str(p),'backup':str(dest),'database':p in before['databases']})
            report={'status':'prepared','app':self.app,'plan':plan,'backups':mapping,**operation.fields()}
            operation.save(report)
            self.assert_offline()
            if self.snapshot()['fingerprint']!=plan['fingerprint']:raise CleanupError('准备过程中数据已变化，请重新预览。')
            report['status']='executing';operation.save(report)
            try:
                for p in sorted(before['databases']):self.mutate_db(p,sorted(ids))
                for p,kind in before['shared'].items():
                    if kind=='json':atomic_json(p,prune_json(read_json(p),ids))
                    elif kind=='dsh-cache':
                        info=read_json(p)
                        info['tables']['sessions']={i:v for i,v in info['tables']['sessions'].items() if i not in ids}
                        atomic_json(p,info)
                    elif kind=='dsh-workspace':
                        info=read_json(p)
                        info['global']['archivedSessionIds']=[i for i in info['global'].get('archivedSessionIds',[]) if i not in ids]
                        for entry in info['tables']['workspaces'].values():entry['sessionIds']=[i for i in entry.get('sessionIds',[]) if i not in ids]
                        atomic_json(p,info)
                    else:
                        key=kind.split(':')[1];kept=[]
                        for line in p.read_text(encoding='utf-8-sig').splitlines(keepends=True):
                            try:item=json.loads(line)
                            except ValueError:kept.append(line);continue
                            if item.get(key) not in ids:kept.append(line)
                        tmp=p.with_suffix(p.suffix+'.cleaner.tmp');tmp.write_text(''.join(kept),encoding='utf-8');tmp.replace(p)
                for p in sorted({p for i in ids for p in before['files'].get(i,set())}):self.safe(p).unlink()
                # Remove only now-empty, selected session directories. No project roots.
                for i in ids:
                    folders={p.parent for p in before['files'].get(i,set())}
                    for root in self.roots:
                        if self.app in {'grok','deepseek'}:folders.update((root/'sessions').glob('*/'+i))
                    for folder in sorted(folders,key=lambda p:len(p.parts),reverse=True):
                        while folder not in self.roots and any(part==i for part in folder.parts):
                            try:self.safe(folder).rmdir()
                            except OSError:break
                            folder=folder.parent
                after=self.snapshot()
                if {r['id'] for r in after['rows']}!=expected:raise CleanupError('删除后会话集合与计划不一致。')
                report.update(status='complete',deleted=len(ids),remaining=len(expected),chatgpt_before=0,chatgpt_after=0,integrity={p.name:'ok' for p in before['databases']})
                operation.save(report);return report
            except Exception as error:
                if run is None:
                    report.update(status='failed_no_backup',error=str(error));operation.save(report)
                    raise CleanupError(f"操作未完成，状态 failed_no_backup。本次未备份，已发生的删除无法自动恢复；请刷新核对。操作记录：{operation.journal}\n原因：{error}") from error
                # Offline rollback restores all backed-up storage, including files.
                try:
                    self.assert_offline()
                    for entry in mapping:
                        p=Path(entry['source']);backup=Path(entry['backup']);p.parent.mkdir(parents=True,exist_ok=True)
                        if entry['database']:
                            with contextlib.closing(connect(backup)) as a,contextlib.closing(connect(p,readonly=False)) as b:a.backup(b)
                        else:shutil.copy2(backup,p)
                    report.update(status='rolled_back',error=str(error))
                except Exception as rollback_error:
                    report.update(status='recovery_required',error=str(error),rollback_error=str(rollback_error))
                operation.save(report)
                raise CleanupError(f"操作失败，状态 {report['status']}。备份：{run}\n{error}") from error


def make_providers(codex_home=None,profile=None):
    profile=profile or Path.home()
    result={}
    for app in LABELS:
        try:
            result[app]=Store(codex_home or profile/'.codex') if app=='codex' else LocalProvider(app,profile)
        except CleanupError:
            continue
    return result
