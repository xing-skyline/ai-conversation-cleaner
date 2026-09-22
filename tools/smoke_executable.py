"""Smoke-test only a test-owned executable's synthetic --demo home."""
import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit,parse_qs


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
            for index,mode in enumerate(['none','custom','default']):
                plan=api('preview',{'ids':[inventory['rows'][index]['id']],
                                   'backup':{'mode':mode,'directory':str(root/'chosen-backups')}})
                result=api('delete',{'plan_token':plan['plan_token']})
                assert result['remaining']==3-index and result['backup_mode']==mode
                if mode=='none':assert result['backup_dir'] is None and result['backups']==[]
                else:assert Path(result['backup_dir'],'result.json').is_file()
            assert len(api('inventory')['rows'])==1
            print('Packaged demo smoke test passed: none/custom/default backup modes.')
        finally:
            if call:
                try:call('quit',{})
                except (OSError,ValueError):pass
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)


if __name__=='__main__':main()
