"""Smoke-test only a test-owned executable's synthetic --demo home."""
import argparse
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
