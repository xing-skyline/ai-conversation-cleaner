"""Verify actual installed storage formats on isolated copies. Original stores
are opened read-only. Only copied data beneath TemporaryDirectory is deleted.
"""
import contextlib
import argparse
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from cleaner.providers import LocalProvider


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--backup-mode',choices=['default','custom','none'],default='default')
    args=parser.parse_args()
    results=[]
    for app in ['claude','grok','cursor','antigravity','deepseek']:
        live=LocalProvider(app);snapshot=live.snapshot()
        with tempfile.TemporaryDirectory(prefix='ai-cleaner-storage-copy-') as folder:
            profile=Path(folder)
            for source in snapshot['databases']:
                dest=profile/source.relative_to(live.profile);dest.parent.mkdir(parents=True,exist_ok=True)
                with contextlib.closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as a,contextlib.closing(sqlite3.connect(dest)) as b:a.backup(b)
            files=set(snapshot['shared'])|{p for group in snapshot['files'].values() for p in group}
            for source in files:
                dest=profile/source.relative_to(live.profile);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
            store=LocalProvider(app,profile,process_provider=lambda:[])
            before=store.inventory()
            if not before['rows']:
                results.append({'app':app,'status':'no local data'});continue
            # Selecting all copied rows tests both complete and malformed remnants.
            policy={'mode':args.backup_mode,'directory':str(profile/'custom-backups')}
            report=store.apply(store.preview([r['id'] for r in before['rows']],backup=policy))
            assert report['remaining']==0
            for source in snapshot['databases']:
                assert source.is_file()
            results.append({'app':app,'backup_mode':args.backup_mode,'status':'passed on isolated copy','deleted':report['deleted'],'remaining':report['remaining']})
    print(json.dumps(results,ensure_ascii=False,indent=2))
    suffix='' if args.backup_mode=='default' else '-'+args.backup_mode
    output=Path(__file__).resolve().parent.parent/'.local-reports'/('storage-copy-verification'+suffix+'.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
