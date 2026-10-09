"""Smoke-test a test-owned executable using isolated synthetic stores only."""
import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit,parse_qs

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_codex_catalog import add_catalog_auxiliaries
from tests.test_cleaner import sql
from tests.test_cursor import CursorTests
from cleaner.demo import IDS
from cleaner import cursor
from cleaner.providers import prune_json
from cleaner.store import connect


def main():
    parser=argparse.ArgumentParser();parser.add_argument('executable',type=Path);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='ai-cleaner-exe-smoke-') as folder:
        root=Path(folder);url_file=root/'url.txt'
        process=subprocess.Popen([str(args.executable.resolve()),'--demo','--no-browser','--url-file',str(url_file)],
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        call=None
        try:
            deadline=time.monotonic()+40
            while not url_file.exists():
                if process.poll() is not None:raise RuntimeError('Executable stopped before opening its demo server.')
                if time.monotonic()>deadline:raise TimeoutError('Executable startup timeout.')
                time.sleep(.1)
            url=urlsplit(url_file.read_text(encoding='utf-8'));token=parse_qs(url.fragment)['token'][0]
            origin=f'{url.scheme}://{url.netloc}'
            def api(path,body=None):
                request=urllib.request.Request(origin+'/api/'+path,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={'X-Cleaner-Token':token,'Content-Type':'application/json','Origin':origin})
                with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)
            call=api
            inventory=api('inventory');assert inventory['demo'] and len(inventory['rows'])==4
            # The executable created this isolated --demo home; never a real profile.
            catalog=Path(inventory['home'])/'sqlite/codex-dev.db'
            add_catalog_auxiliaries(catalog)
            remote=sql(catalog,"SELECT * FROM live_visualization_suggestions WHERE host_id<>'local' ORDER BY host_id,thread_id")
            for index,mode in enumerate(['none','custom','none']):
                plan=api('preview',{'ids':[inventory['rows'][index]['id']],
                                   'backup':{'mode':mode,'directory':str(root/'chosen-backups')}})
                result=api('delete',{'plan_token':plan['plan_token']})
                assert result['remaining']==3-index and result['backup_mode']==mode
                if mode=='none':assert result['backup_dir'] is None and result['backups']==[]
                else:assert Path(result['backup_dir'],'result.json').is_file()
            assert len(api('inventory')['rows'])==1
            assert sql(catalog,'SELECT count(*) FROM automation_runs')==[(0,)]
            assert sql(catalog,'SELECT count(*) FROM inbox_items WHERE thread_id IS NOT NULL')==[(0,)]
            assert sql(catalog,"SELECT count(*) FROM live_visualization_suggestions WHERE host_id='local'")==[(0,)]
            assert sql(catalog,"SELECT * FROM live_visualization_suggestions WHERE host_id<>'local' ORDER BY host_id,thread_id")==remote
            assert sql(catalog,'SELECT count(*) FROM automations')==[(1,)]
            print('Packaged demo smoke test passed: none/custom backup modes.')
            print('Packaged Codex catalog auxiliaries passed; remote suggestions and automation definition preserved.')
        finally:
            if call:
                try:call('quit',{})
                except (OSError,ValueError):pass
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate();process.wait(timeout=10)
                raise AssertionError('Exit endpoint left the executable running.')
            assert process.returncode == 0, 'Executable did not exit cleanly.'
    check_browser_close(args.executable)
    check_cursor(args.executable)


def check_cursor(executable):
    """Exercise the bundled adapter, including search-only rows absent in old EXEs."""
    fixture=CursorTests()
    fixture.setUp()
    process=None;call=None
    try:
        root=fixture.profile.resolve();url_file=root/'url.txt'
        # Path.home() uses USERPROFILE on Windows and HOME on macOS. Redirect
        # both, plus CODEX_HOME, so the executable cannot select real stores.
        env=os.environ.copy()
        env.update(USERPROFILE=str(root),HOME=str(root),CODEX_HOME=str(root/'.codex'))
        process=subprocess.Popen([str(executable.resolve()),'--app','cursor','--no-browser','--url-file',str(url_file)],
                                 env=env,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        deadline=time.monotonic()+40
        while not url_file.exists():
            if process.poll() is not None:raise RuntimeError('Executable exited during Cursor startup.')
            if time.monotonic()>deadline:raise TimeoutError('Cursor executable startup timeout.')
            time.sleep(.1)
        url=urlsplit(url_file.read_text(encoding='utf-8'));token=parse_qs(url.fragment)['token'][0]
        origin=f'{url.scheme}://{url.netloc}'
        def api(path,body=None):
            if body is not None:body={**body,'app':'cursor'}
            else:path+='?app=cursor'
            request=urllib.request.Request(origin+'/api/'+path,
                data=json.dumps(body).encode() if body is not None else None,
                headers={'X-Cleaner-Token':token,'Content-Type':'application/json','Origin':origin})
            with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)
        call=api
        inventory=api('inventory')
        assert inventory['home']=='；'.join(str(p) for p in fixture.store.roots), 'Executable selected a different profile.'
        assert {r['id'] for r in inventory['rows']}==set(IDS[:3]), 'Packaged Cursor inventory missed modern storage.'
        other=sql(fixture.db,'SELECT * FROM composerHeaders WHERE composerId=?',(IDS[1],))
        cloud=sql(fixture.search,"SELECT * FROM conversations WHERE source='cloud-cache'")
        cloud_fts=sql(fixture.search,'SELECT rowid,* FROM conversation_fts WHERE rowid=3')
        shared=sql(fixture.db,"SELECT * FROM cursorDiskKV WHERE key IN ('agentKv:blob:shared','composer.content.shared') ORDER BY key")
        for index,mode in enumerate(['none','custom']):
            ids=[IDS[0]] if index==0 else list(IDS[1:3])
            plan=api('preview',{'ids':ids,'backup':{'mode':mode,'directory':str(root/'chosen-backups')}})
            assert Path(plan['home']).resolve()==fixture.store.home.resolve()
            result=api('delete',{'plan_token':plan['plan_token']})
            assert result['status']=='complete' and result['remaining']==(2 if index==0 else 0)
            assert result['backup_mode']==mode
            if mode=='none':assert result['backup_dir'] is None and result['backups']==[]
            else:assert Path(result['backup_dir'],'result.json').is_file()
            with contextlib.closing(connect(fixture.db)) as con:cursor.verify_deleted(con,ids,prune_json)
            with contextlib.closing(connect(fixture.search)) as con:
                cursor.verify_deleted(con,ids,prune_json,search=True,fts_rowids=[1] if index==0 else [2,4])
            assert sql(fixture.search,"SELECT * FROM conversations WHERE source='cloud-cache'")==cloud
            assert sql(fixture.search,'SELECT rowid,* FROM conversation_fts WHERE rowid=3')==cloud_fts
            assert sql(fixture.db,"SELECT * FROM cursorDiskKV WHERE key IN ('agentKv:blob:shared','composer.content.shared') ORDER BY key")==shared
            if index==0:assert sql(fixture.db,'SELECT * FROM composerHeaders WHERE composerId=?',(IDS[1],))==other
        assert api('inventory')['rows']==[]
        assert sql(fixture.db,'SELECT count(*) FROM composerHeaders WHERE isArchived=1')==[(2,)]
        print('Packaged Cursor smoke test passed: modern inventory, none/custom deletion, native markers, cloud and shared data preserved.')
    finally:
        try:
            if process is not None:
                if call:
                    try:call('quit',{})
                    except (OSError,ValueError):pass
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate();process.wait(timeout=10)
                    raise AssertionError('Cursor smoke test left the executable running.')
                assert process.returncode==0, 'Cursor executable did not exit cleanly.'
        finally:
            fixture.tearDown()


def check_browser_close(executable):
    """Require the real packaged process to exit without calling /api/quit."""
    with tempfile.TemporaryDirectory(prefix='ai-cleaner-exit-smoke-') as folder:
        url_file=Path(folder)/'url.txt'
        process=subprocess.Popen([str(executable.resolve()),'--demo','--no-browser','--url-file',str(url_file)],
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            deadline=time.monotonic()+40
            while not url_file.exists():
                if process.poll() is not None:raise RuntimeError('Executable exited during startup.')
                if time.monotonic()>deadline:raise TimeoutError('Executable startup timeout.')
                time.sleep(.1)
            url=urlsplit(url_file.read_text(encoding='utf-8'))
            token=parse_qs(url.fragment)['token'][0]
            for sequence,event in enumerate(['heartbeat','close'],1):
                request=urllib.request.Request(f'{url.scheme}://{url.netloc}/api/browser',
                    data=json.dumps({'client_id':'packaged-exit-test','event':event,'sequence':sequence}).encode(),
                    headers={'X-Cleaner-Token':token,'Content-Type':'application/json'})
                with urllib.request.urlopen(request,timeout=10) as response:assert response.status==200
            process.wait(timeout=10)
            assert process.returncode==0, 'Executable did not exit cleanly after browser close.'
            print('Packaged last-browser-close exit passed (no forced termination).')
        finally:
            if process.poll() is None:
                process.terminate();process.wait(timeout=10)


if __name__=='__main__':main()
