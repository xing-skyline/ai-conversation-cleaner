import base64
import contextlib
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from cleaner.demo import IDS, create_demo
from cleaner.providers import LocalProvider
from cleaner.protobuf import fields, summary_map
from cleaner.server import AppServer
from cleaner.store import Store, CleanupError


def sql(path, statement, args=()):
    with contextlib.closing(sqlite3.connect(path)) as con:
        result=con.execute(statement,args).fetchall();con.commit();return result


def put(path,value,jsonl=False):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(("\n".join(json.dumps(r,ensure_ascii=False) for r in value)+"\n") if jsonl else json.dumps(value,ensure_ascii=False),encoding='utf-8')


def varint(n):
    result=b''
    while n>127:result+=bytes([(n&127)|128]);n>>=7
    return result+bytes([n])


def pb(n,body):
    return varint((n<<3)|2)+varint(len(body))+body


def summary(ids):
    return base64.b64encode(b''.join(pb(1,pb(1,i.encode())+pb(2,pb(1,base64.b64encode(pb(1,('会话 '+i[:8]).encode()))))) for i in ids)).decode()


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ai-cleaner-tests-');self.root=Path(self.temp.name);create_demo(self.root)
        self.store=Store(self.root,process_provider=lambda:[],rpc_factory=None)
    def tearDown(self):self.temp.cleanup()
    def test_read_union_and_detail(self):
        self.assertEqual(len(self.store.inventory()['rows']),4)
        self.assertEqual(len(self.store.detail(IDS[0])['messages']),2)
        self.assertTrue(self.store.inventory()['rows'][2]['archived'])
    def test_catalog_title_wins_over_prompt(self):
        sql(self.root/'state_5.sqlite','UPDATE threads SET title=? WHERE id=?',('a very long original user prompt',IDS[0]))
        self.assertEqual(next(r for r in self.store.inventory()['rows'] if r['id']==IDS[0])['title'],'示例：编写一个计算器')
    def test_delete_nonempty_empty_archived_and_ghost(self):
        for target in [IDS[0],IDS[1],IDS[2],IDS[3]]:
            report=self.store.apply(self.store.preview([target]));self.assertEqual(report['deleted'],1)
            self.assertEqual(report['chatgpt_after'],1)
            self.assertTrue(Path(report['backup_dir'],'result.json').is_file())
        self.assertEqual(self.store.inventory()['rows'],[])
        self.assertEqual(sql(self.root/'sqlite/codex-dev.db','SELECT count(*) FROM local_thread_catalog')[0][0],2)
        self.assertEqual(sql(self.root/'thread_history_1.sqlite','SELECT count(*) FROM thread_items')[0][0],0)
        self.assertEqual(list((self.root/'sessions').glob('*.jsonl')),[])
        self.assertEqual(list((self.root/'archived_sessions').glob('*.jsonl')),[])
        self.assertEqual(json.loads((self.root/'.codex-global-state.json').read_text())['unrelated_setting'],'keep me')
    def test_stale_preview_blocks(self):
        plan=self.store.preview([IDS[0]]);sql(self.root/'state_5.sqlite','UPDATE threads SET title=? WHERE id=?',('changed',IDS[1]))
        with self.assertRaisesRegex(CleanupError,'变化'):self.store.apply(plan)
        self.assertEqual(len(self.store.inventory()['rows']),4)
    def test_running_process_blocks(self):
        plan=self.store.preview([IDS[0]]);self.store.process_provider=lambda:[{'pid':123,'name':'codex.exe'}]
        with self.assertRaisesRegex(CleanupError,'退出'):self.store.apply(plan)
        self.assertEqual(len(self.store.inventory()['rows']),4)
    def test_unknown_schema_blocks(self):
        sql(self.root/'state_5.sqlite','CREATE TABLE future_messages(thread_id TEXT)')
        with self.assertRaisesRegex(CleanupError,'未适配'):self.store.apply(self.store.preview([IDS[0]]))
    def test_outside_rollout_rejected(self):
        with tempfile.NamedTemporaryFile(suffix='.jsonl') as outside:
            sql(self.root/'state_5.sqlite','UPDATE threads SET rollout_path=? WHERE id=?',(outside.name,IDS[0]))
            with self.assertRaisesRegex(CleanupError,'超出'):self.store.preview([IDS[0]])
    def test_failure_restores_databases_and_files(self):
        original=self.store.snapshot()['fingerprint']
        with patch.object(self.store,'prune_files',side_effect=OSError('injected failure')):
            with self.assertRaisesRegex(CleanupError,'rolled_back'):self.store.apply(self.store.preview([IDS[0]]))
        self.assertEqual(len(self.store.inventory()['rows']),4)
        self.assertEqual(len(self.store.detail(IDS[0])['messages']),2)
    def test_fork_dependency(self):
        p=self.root/'sessions'/f'rollout-{IDS[4]}.jsonl'
        put(p,[{'type':'session_meta','payload':{'id':IDS[4],'forked_from_id':IDS[0]}}],True)
        with self.assertRaisesRegex(CleanupError,'派生'):self.store.preview([IDS[0]])
        self.store.apply(self.store.preview([IDS[0],IDS[4]]))


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ai-adapter-tests-');self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def provider(self,app):return LocalProvider(app,self.root,process_provider=lambda:[])
    def test_deepseek_files_both_caches_and_workspace(self):
        root=self.root/'.dsh';selected='session-'+IDS[0];keep=IDS[1]
        for i in [selected,keep]:
            put(root/'sessions'/'project'/i/'session.v3.jsonl.zstd',{'opaque':'compressed fixture'})
            put(root/'storages/session_projcache/sessions'/(i+'.json'),{'version':7 if i==selected else 5,'record':{'identity':{'cwd':'D:\\work','createdAt':1000},'rows':{'title':{'val':'DSH '+i},'titleInput':{'val':{'first':{'seq':1,'text':'hello'}}}}}})
        put(root/'storages/session_projcache.json',{'unit':{'name':'session_projcache','version':3},'global':None,'tables':{'sessions':{selected:{'identity':{},'rows':{}},keep:{'identity':{},'rows':{}}}}})
        # Workspace ID may equal the selected session ID; the workspace itself must survive.
        put(root/'storages/workspace.json',{'unit':{'name':'workspace','version':2},'global':{'initialized':True,'workspaceIds':[selected],'archivedSessionIds':[selected]},'tables':{'workspaces':{selected:{'path':'D:\\work','sessionIds':[selected,keep]}}}})
        put(root/'settings.yaml',{'do':'not touch'});put(root/'.deleted-sessions'/selected/'old.json',{'backup':True})
        store=self.provider('deepseek');self.assertEqual(len(store.inventory()['rows']),2)
        self.assertTrue(next(r for r in store.inventory()['rows'] if r['id']==selected)['archived'])
        self.assertEqual(store.detail(selected)['messages'][0]['text'],'hello')
        result=store.apply(store.preview([selected]));self.assertEqual(result['remaining'],1)
        self.assertFalse((root/'sessions/project'/selected).exists())
        self.assertFalse((root/'storages/session_projcache/sessions'/(selected+'.json')).exists())
        self.assertEqual(set(json.loads((root/'storages/session_projcache.json').read_text())['tables']['sessions']),{keep})
        workspace=json.loads((root/'storages/workspace.json').read_text())
        self.assertEqual(workspace['global']['workspaceIds'],[selected])
        self.assertEqual(workspace['global']['archivedSessionIds'],[])
        self.assertEqual(workspace['tables']['workspaces'][selected]['sessionIds'],[keep])
        self.assertTrue((root/'.deleted-sessions'/selected/'old.json').exists())
        self.assertTrue((root/'settings.yaml').exists())
    def test_deepseek_empty_folder_and_unknown_cache(self):
        root=self.root/'.dsh';folder=root/'sessions/project'/IDS[0];folder.mkdir(parents=True)
        store=self.provider('deepseek');store.apply(store.preview([IDS[0]]))
        self.assertFalse(folder.exists());self.assertEqual(store.inventory()['rows'],[])
        put(root/'storages/session_projcache/sessions'/(IDS[1]+'.json'),{'version':999,'record':{}})
        with self.assertRaisesRegex(CleanupError,'格式'):store.inventory()
    def test_claude_content_history_and_sidecar(self):
        root=self.root/'.claude';project=root/'projects'/'test'
        for i in IDS[:2]:
            put(project/(i+'.jsonl'),[{'sessionId':i,'type':'user','message':{'content':'hello'}}],True)
            put(project/i/'subagents'/'agent-child.jsonl',[{'type':'assistant','message':{'content':'result'}}],True)
        put(root/'history.jsonl',[{'sessionId':i,'display':'Title '+i,'timestamp':10,'project':'demo'} for i in IDS[:2]],True)
        put(project/'sessions-index.json',{'entries':[{'sessionId':i,'summary':'Session'} for i in IDS[:2]]})
        put(root/'settings.json',{'keep':'unchanged'})
        store=self.provider('claude');self.assertEqual(len(store.detail(IDS[0])['messages']),1)
        store.apply(store.preview([IDS[0]]))
        self.assertEqual([r['id'] for r in store.inventory()['rows']],[IDS[1]])
        self.assertTrue((project/(IDS[1]+'.jsonl')).exists())
        self.assertEqual(json.loads((root/'settings.json').read_text()),{'keep':'unchanged'})
    def test_grok_directory_prompt_and_search(self):
        root=self.root/'.grok/sessions';root.mkdir(parents=True)
        db=root/'session_search.sqlite'
        sql(db,'CREATE TABLE session_docs(session_id TEXT PRIMARY KEY,title TEXT,cwd TEXT,updated_at INTEGER,content TEXT)')
        for i in IDS[:2]:
            p=root/'D%3A%5Cwork'/i
            put(p/'summary.json',{'info':{'id':i,'cwd':'D:\\work'},'generated_title':'Grok '+i,'updated_at':100})
            put(p/'chat_history.jsonl',[{'type':'user','content':'hello'}],True)
            sql(db,'INSERT INTO session_docs VALUES(?,?,?,?,?)',(i,'Grok','D:\\work',100,'hello'))
        put(root/'D%3A%5Cwork/prompt_history.jsonl',[{'session_id':i,'prompt':'hello'} for i in IDS[:2]],True)
        store=self.provider('grok');report=store.apply(store.preview([IDS[0]]))
        self.assertEqual(report['remaining'],1);self.assertFalse((root/'D%3A%5Cwork'/IDS[0]).exists())
        self.assertEqual(sql(db,'SELECT session_id FROM session_docs'),[(IDS[1],)])
    def test_cursor_database_exact_keys_and_legacy_index(self):
        appdata='Library/Application Support' if sys.platform=='darwin' else 'AppData/Roaming'
        root=self.root/appdata/'Cursor/User';db=root/'globalStorage/state.vscdb';db.parent.mkdir(parents=True)
        sql(db,'CREATE TABLE composerHeaders(composerId TEXT PRIMARY KEY,value TEXT)');sql(db,'CREATE TABLE cursorDiskKV(key TEXT PRIMARY KEY,value TEXT)');sql(db,'CREATE TABLE ItemTable(key TEXT PRIMARY KEY,value TEXT)')
        for i in IDS[:2]:
            v=json.dumps({'composerId':i,'name':'Cursor '+i,'createdAt':1000000000000})
            sql(db,'INSERT INTO composerHeaders VALUES(?,?)',(i,v))
            for key in ['composerData:'+i,'bubbleId:'+i+':message','checkpointId:'+i+':file']:
                sql(db,'INSERT INTO cursorDiskKV VALUES(?,?)',(key,v))
        sql(db,'INSERT INTO cursorDiskKV VALUES(?,?)',('agentKv:blob:shared','SHARED DATA'))
        sql(db,'INSERT INTO ItemTable VALUES(?,?)',('composer.composerData',json.dumps({'allComposers':[{'composerId':i} for i in IDS[:2]]})))
        store=self.provider('cursor');store.apply(store.preview([IDS[0]]))
        self.assertEqual([r['id'] for r in store.inventory()['rows']],[IDS[1]])
        self.assertEqual(sql(db,"SELECT value FROM cursorDiskKV WHERE key='agentKv:blob:shared'"),[('SHARED DATA',)])
        self.assertEqual(sql(db,"SELECT count(*) FROM cursorDiskKV WHERE key LIKE ?",('%'+IDS[0]+'%',)),[(0,)])
    def test_antigravity_binary_summary_and_artifacts(self):
        appdata='Library/Application Support' if sys.platform=='darwin' else 'AppData/Roaming'
        base=self.root/'.gemini/antigravity';ide=self.root/'.gemini/antigravity-ide';user=self.root/appdata/'Antigravity/User'
        p=user/'globalStorage/state.vscdb';p.parent.mkdir(parents=True);sql(p,'CREATE TABLE ItemTable(key TEXT PRIMARY KEY,value TEXT)')
        sql(p,'INSERT INTO ItemTable VALUES(?,?)',('antigravityUnifiedStateSync.trajectorySummaries',summary(IDS[:2])))
        for i in IDS[:2]:
            f=ide/'conversations'/(i+'.pb');f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b'opaque binary')
            put(ide/'brain'/i/'task.md',{'task':'example'})
        store=self.provider('antigravity');store.apply(store.preview([IDS[0]]))
        self.assertEqual([r['id'] for r in store.inventory()['rows']],[IDS[1]])
        encoded=sql(p,'SELECT value FROM ItemTable')[0][0];self.assertEqual(set(summary_map(encoded)[0]),{IDS[1]})
    def test_proto_preserves_unknown_wire_fields(self):
        original=base64.b64decode(summary(IDS[:2]));unknown=pb(12,b'opaque field')
        _,edited=summary_map(base64.b64encode(original+unknown).decode(),{IDS[0]})
        self.assertTrue(base64.b64decode(edited).endswith(unknown))
        with self.assertRaises(CleanupError):fields(b'\x0a\xff')
    def test_non_codex_running_guard_and_stale(self):
        p=self.root/'.claude/history.jsonl';put(p,[{'sessionId':IDS[0],'display':'test'}],True)
        store=self.provider('claude');plan=store.preview([IDS[0]])
        store.process_provider=lambda:[{'pid':1,'name':'claude.exe'}]
        with self.assertRaisesRegex(CleanupError,'退出'):store.apply(plan)
        store.process_provider=lambda:[];put(p,[{'sessionId':IDS[0],'display':'updated'}],True)
        with self.assertRaisesRegex(CleanupError,'变化'):store.apply(plan)
    def test_claude_exit_does_not_replace_title(self):
        p=self.root/'.claude/history.jsonl'
        put(p,[{'sessionId':IDS[0],'display':'实际任务','timestamp':1},{'sessionId':IDS[0],'display':'exit','timestamp':2}],True)
        self.assertEqual(self.provider('claude').inventory()['rows'][0]['title'],'实际任务')
    def test_adapter_failure_rolls_back(self):
        p=self.root/'.claude/history.jsonl';put(p,[{'sessionId':IDS[0],'display':'test'}],True)
        store=self.provider('claude');plan=store.preview([IDS[0]]);original=p.read_bytes()
        actual=store.snapshot;count=0
        def fail_after_write():
            nonlocal count
            count+=1
            if count==4:raise OSError('verification failure')
            return actual()
        with patch.object(store,'snapshot',side_effect=fail_after_write):
            with self.assertRaisesRegex(CleanupError,'rolled_back'):store.apply(plan)
        self.assertEqual(p.read_bytes(),original)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name);create_demo(root)
        self.server=AppServer(Store(root,process_provider=lambda:[],rpc_factory=None),demo=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.server.shutdown();self.thread.join();self.server.server_close();self.temp.cleanup()
    def request(self,path,body=None,token=True,origin=None):
        headers={'Content-Type':'application/json'}
        if token:headers['X-Cleaner-Token']=self.server.token
        if origin:headers['Origin']=origin
        req=urllib.request.Request(self.server.origin+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req) as response:return json.load(response)
    def test_auth_and_origin_rejection(self):
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/inventory',token=False)
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/preview',{'ids':[IDS[0]]},origin='https://evil.example')
        self.assertEqual(len(self.request('/api/inventory')['rows']),4)
    def test_preview_delete_once(self):
        plan=self.request('/api/preview',{'ids':[IDS[0]]})
        result=self.request('/api/delete',{'plan_token':plan['plan_token']})
        self.assertEqual(result['deleted'],1)
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/delete',{'plan_token':plan['plan_token']})
    def test_ids_require_real_membership(self):
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/preview',{'ids':['../../file']})
    def test_backup_policy_is_bound_to_preview(self):
        plan=self.request('/api/preview',{'ids':[IDS[0]],'backup':{'mode':'none'}})
        self.assertEqual(plan['backup']['mode'],'none')
        report=self.request('/api/delete',{'plan_token':plan['plan_token'],'confirm':'删除','backup':{'mode':'default'}})
        self.assertIsNone(report['backup_dir'])
    def test_native_folder_picker_endpoint_and_cancel(self):
        with patch('cleaner.server.choose_backup_directory',return_value='D:\\My Backups',create=True):
            self.assertEqual(self.request('/api/choose-backup-directory',{})['directory'],'D:\\My Backups')
        with patch('cleaner.server.choose_backup_directory',return_value=None,create=True):
            self.assertIsNone(self.request('/api/choose-backup-directory',{})['directory'])
    def test_closing_browser_stops_server_without_quit_button(self):
        event={'client_id':'test-tab','event':'heartbeat','sequence':1}
        with self.assertRaises(urllib.error.HTTPError):
            self.request('/api/browser',event,token=False)
        self.request('/api/browser',event)
        self.request('/api/browser',event | {'event':'close','sequence':2})
        self.thread.join(timeout=6)
        self.assertFalse(self.thread.is_alive(),'Closing the last browser page left the server running.')
    def test_quit_does_not_require_a_live_selected_provider(self):
        self.request('/api/quit',{'app':'unavailable'})
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())
    def test_quit_waits_until_a_deletion_has_finished(self):
        plan=self.request('/api/preview',{'ids':[IDS[0]],'backup':{'mode':'none'}})
        started=threading.Event();release=threading.Event();results=[]
        apply=self.server.store.apply
        def delayed_apply(value):
            started.set()
            if not release.wait(timeout=8):raise RuntimeError('Test operation timed out.')
            return apply(value)
        def delete():
            try:results.append(self.request('/api/delete',{'plan_token':plan['plan_token']}))
            except Exception as error:results.append(error)
        with patch.object(self.server.store,'apply',side_effect=delayed_apply):
            worker=threading.Thread(target=delete,daemon=True);worker.start()
            try:
                self.assertTrue(started.wait(timeout=3))
                self.assertTrue(self.request('/api/quit',{})['pending'])
                self.thread.join(timeout=.7)
                self.assertTrue(self.thread.is_alive(),'Exit interrupted an active deletion.')
                with self.assertRaises(urllib.error.HTTPError):self.request('/api/preview',{'ids':[IDS[1]]})
            finally:
                release.set();worker.join(timeout=5)
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(len(results),1)
        self.assertIsInstance(results[0],dict)
        self.assertEqual(results[0]['deleted'],1)
        self.assertEqual(len(self.server.store.inventory()['rows']),3)


if __name__=='__main__':unittest.main()
