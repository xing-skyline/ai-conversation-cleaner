import contextlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cleaner.pi import PiProvider, STATS_TABLES
from cleaner.providers import make_providers
from cleaner.store import CleanupError
from tests.test_cleaner import put, sql

SELECTED = '11111111-1111-4111-8111-111111111111'
KEPT = '22222222-2222-4222-8222-222222222222'


def transcript(path, target, *, omp=False, empty=False):
    entries = [{'type':'session','version':3,'id':target,'cwd':'C:/Example','timestamp':'2026-11-16T01:00:00Z'}]
    if omp:
        entries.insert(0, {'type':'title','title':'OMP current title'})
    if not empty:
        entries += [{'type':'message','id':'first','message':{'role':'user','content':'Hello '+target}},
                    {'type':'message','id':'reply','message':{'role':'assistant','content':[{'type':'text','text':'Reply'}]}},
                    {'type':'title_change' if omp else 'session_info','title':'Renamed OMP','name':'Renamed Pi'}]
    put(path,entries,jsonl=True)
    return path


def create_pi(profile, app):
    root=profile/('.omp/agent' if app=='omp' else '.pi/agent')
    paths={target:transcript(root/'sessions/--example--'/('2026-11-16_'+target+'.jsonl'),target,omp=app=='omp')
           for target in (SELECTED,KEPT)}
    put(root/'auth.json',{'credential':'synthetic; preserve'})
    put(root/'settings.json',{})
    put(root/'skills/example/config.json',{'keep':True})
    if app=='omp':
        for target,path in paths.items():
            put(path.with_suffix('')/'output/data.json',{'owned':target})
            pointer=root/'custom-session-files'/target
            pointer.parent.mkdir(parents=True,exist_ok=True)
            pointer.write_text(str(path),encoding='utf-8')
            crumb=root/'terminal-sessions'/target
            crumb.parent.mkdir(parents=True,exist_ok=True)
            crumb.write_text('C:/Example\n'+str(path)+'\nfresh\n',encoding='utf-8')
        put(root/'blobs/shared',{'keep':True})
        with contextlib.closing(sqlite3.connect(root/'history.db')) as con:
            con.executescript('''CREATE TABLE history(id INTEGER PRIMARY KEY,prompt TEXT,session_id TEXT);
                CREATE VIRTUAL TABLE history_fts USING fts5(prompt,content='history',content_rowid='id');
                CREATE TRIGGER history_ai AFTER INSERT ON history BEGIN INSERT INTO history_fts(rowid,prompt) VALUES(new.id,new.prompt); END;
                CREATE TRIGGER history_ad AFTER DELETE ON history BEGIN INSERT INTO history_fts(history_fts,rowid,prompt) VALUES('delete',old.id,old.prompt); END;
                CREATE TABLE session_titles(session_id TEXT PRIMARY KEY,title TEXT,updated_at INTEGER);
                CREATE TABLE session_recaps(session_id TEXT,recap TEXT,created_at INTEGER);
            ''')
            for i,target in enumerate((SELECTED,KEPT)):
                con.execute('INSERT INTO history VALUES(?,?,?)',(i+1,'selectedmarker' if target==SELECTED else 'keptmarker',target))
                con.execute('INSERT INTO session_titles VALUES(?,?,?)',(target,'Title '+target,1))
                con.execute('INSERT INTO session_recaps VALUES(?,?,?)',(target,'recap',1))
            con.execute('INSERT INTO history VALUES(3,?,NULL)',('unowned',))
            con.commit()
        with contextlib.closing(sqlite3.connect(root/'stats.db')) as con:
            for table in STATS_TABLES:
                con.execute(f'CREATE TABLE {table}(session_file TEXT,value TEXT)')
                con.executemany(f'INSERT INTO {table} VALUES(?,?)',[(str(p),'keep or delete by exact path') for p in paths.values()])
                con.execute(f'INSERT INTO {table} VALUES(?,?)',(str(paths[SELECTED].with_suffix('')/'subagent/nested.jsonl'),'nested'))
            con.commit()
    return root,paths


class PiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='pi-cleaner-tests-')
        self.profile=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_both_tabs_and_selected_deletion_preserve_other_sessions_and_config(self):
        for app in ('pi','omp'):
            with self.subTest(app=app):
                root,paths=create_pi(self.profile,app)
                store=make_providers(profile=self.profile)[app]
                store.process_provider=lambda:[]
                self.assertEqual(len(store.inventory()['rows']),2)
                self.assertEqual(store.detail(SELECTED)['messages'][0]['text'],'Hello '+SELECTED)
                other=paths[KEPT].read_bytes()
                result=store.apply(store.preview([SELECTED]))
                self.assertEqual(result['remaining'],1)
                self.assertEqual(result['backups'],[])
                self.assertFalse(paths[SELECTED].exists())
                self.assertEqual(paths[KEPT].read_bytes(),other)
                for path in ('auth.json','settings.json','skills/example/config.json'):
                    self.assertTrue((root/path).is_file())
                if app=='omp':
                    self.assertFalse((paths[SELECTED].with_suffix('')/'output/data.json').exists())
                    self.assertFalse((root/'custom-session-files'/SELECTED).exists())
                    self.assertFalse((root/'terminal-sessions'/SELECTED).exists())
                    self.assertTrue((root/'blobs/shared').is_file())
                    self.assertEqual(sql(root/'history.db',"SELECT rowid FROM history_fts WHERE history_fts MATCH 'selectedmarker'"),[])
                    self.assertEqual(sql(root/'history.db',"SELECT rowid FROM history_fts WHERE history_fts MATCH 'keptmarker'"),[(2,)])
                    self.assertEqual(sql(root/'history.db','SELECT session_id FROM session_titles'),[(KEPT,)])
                    self.assertEqual(sql(root/'history.db','SELECT session_id FROM session_recaps'),[(KEPT,)])
                    self.assertEqual(sql(root/'history.db','SELECT session_id FROM history ORDER BY id'),[(KEPT,),(None,)])
                    for table in STATS_TABLES:
                        self.assertEqual(sql(root/'stats.db',f'SELECT session_file FROM {table}'),[(str(paths[KEPT]),)])

    def test_environment_custom_directory_and_fixture_isolation(self):
        root,paths=create_pi(self.profile,'pi')
        custom=self.profile/'external'
        transcript(custom/'custom.jsonl','custom-safe-id')
        with patch.dict(os.environ,{'PI_CODING_AGENT_DIR':str(root),'PI_CODING_AGENT_SESSION_DIR':str(custom)}):
            live=PiProvider(process_provider=lambda:[])
            self.assertEqual(len(live.snapshot()['rows']),3)
            isolated=PiProvider(profile=self.profile/'isolated',process_provider=lambda:[])
            self.assertEqual(isolated.snapshot()['rows'],[])
            live.apply(live.preview(['custom-safe-id']))
            self.assertFalse((custom/'custom.jsonl').exists())
            self.assertTrue(paths[SELECTED].exists())

    def test_settings_directory_and_empty_session(self):
        root=self.profile/'.pi/agent'
        custom=self.profile/'custom'
        put(root/'settings.json',{'sessionDir':str(custom)})
        transcript(custom/'empty.jsonl','empty',empty=True)
        store=PiProvider(profile=self.profile,process_provider=lambda:[])
        self.assertEqual(store.detail('empty')['messages'],[])
        self.assertEqual(store.apply(store.preview(['empty']))['remaining'],0)

    def test_omp_exact_external_registry_does_not_scan_siblings(self):
        root=self.profile/'.omp/agent'
        path=transcript(self.profile/'external/selected.jsonl',SELECTED,omp=True)
        other=transcript(path.with_name('other.jsonl'),KEPT,omp=True)
        pointer=root/'custom-session-files/pointer'
        pointer.parent.mkdir(parents=True)
        pointer.write_text(str(path),encoding='utf-8')
        put(path.with_suffix('')/'tool.txt',{'output':1})
        store=PiProvider('omp',self.profile,lambda:[])
        self.assertEqual([r['id'] for r in store.snapshot()['rows']],[SELECTED])
        with self.assertRaises(CleanupError):store.safe(other)
        with self.assertRaisesRegex(CleanupError,'备份'):
            store.preview([SELECTED],backup={'mode':'custom','directory':str(path.with_suffix('')/'backup')})
        self.assertEqual(store.apply(store.preview([SELECTED]))['remaining'],0)
        self.assertTrue(other.exists())
        self.assertFalse(pointer.exists())

    def test_backup_and_rollback_restore_files_and_indexes(self):
        root,paths=create_pi(self.profile,'omp')
        store=PiProvider('omp',self.profile,lambda:[])
        original=store.snapshot
        failed=False
        def fail_once():
            nonlocal failed
            snap=original()
            if not failed and not paths[SELECTED].exists():
                failed=True
                raise OSError('injected failure')
            return snap
        backup={'mode':'custom','directory':str(self.profile/'backup')}
        with patch.object(store,'snapshot',side_effect=fail_once):
            with self.assertRaisesRegex(CleanupError,'rolled_back'):
                store.apply(store.preview([SELECTED],backup=backup))
        self.assertTrue(paths[SELECTED].exists())
        self.assertEqual(len(store.snapshot()['rows']),2)
        self.assertEqual(sql(root/'history.db','SELECT count(*) FROM session_recaps'),[(2,)])
        result=store.apply(store.preview([SELECTED],backup=backup))
        self.assertEqual(result['status'],'complete')
        self.assertTrue(Path(result['backup_dir'],'result.json').exists())

    def test_stale_preview_and_running_writer_block(self):
        root,paths=create_pi(self.profile,'pi')
        store=PiProvider('pi',self.profile,lambda:[])
        plan=store.preview([SELECTED])
        store.process_provider=lambda:[{'pid':123,'name':'omp.exe'}]
        with self.assertRaisesRegex(CleanupError,'退出'):store.apply(plan)
        store.process_provider=lambda:[]
        with paths[KEPT].open('a',encoding='utf-8') as stream:stream.write('{"type":"session_info","name":"changed"}\n')
        with self.assertRaisesRegex(CleanupError,'变化'):store.apply(plan)
        self.assertTrue(paths[SELECTED].exists())

    def test_optional_close_hook_runs_before_local_provider_offline_guard(self):
        root,paths=create_pi(self.profile,'pi')
        running=[{'pid':123,'name':'omp.exe'}]
        store=PiProvider('pi',self.profile,lambda:running)
        plan=store.preview([SELECTED])
        def close():
            self.assertTrue(paths[SELECTED].exists())
            running.clear()
        result=store.apply(plan,before_delete=close)
        self.assertEqual(result['deleted'],1)
        self.assertTrue(paths[KEPT].exists())

    def test_unknown_index_and_malformed_journal_block_before_writes(self):
        root,paths=create_pi(self.profile,'omp')
        store=PiProvider('omp',self.profile,lambda:[])
        sql(root/'stats.db','CREATE TABLE future(session_file TEXT)')
        with self.assertRaisesRegex(CleanupError,'未适配'):store.preview([SELECTED])
        sql(root/'stats.db','DROP TABLE future')
        paths[KEPT].write_text('{"type":"session",',encoding='utf-8')
        with self.assertRaisesRegex(CleanupError,'JSONL'):store.preview([SELECTED])
        self.assertTrue(paths[SELECTED].exists())

    def test_duplicate_ids_and_traversal_ids_are_rejected(self):
        root,paths=create_pi(self.profile,'pi')
        store=PiProvider('pi',self.profile,lambda:[])
        transcript(paths[SELECTED].with_name('duplicate.jsonl'),SELECTED)
        with self.assertRaisesRegex(CleanupError,'同一 ID'):store.snapshot()
        for target in ('../escape','..','x/y','x\\y','',None):
            self.assertFalse(store.valid_id(target))


if __name__=='__main__':unittest.main()
