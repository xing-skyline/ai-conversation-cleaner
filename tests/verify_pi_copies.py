"""Opt-in Pi/OMP deletion probe on isolated copies; originals stay read-only."""
import argparse
import contextlib
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from cleaner.pi import PiProvider, STATS_TABLES
from cleaner.store import canonical_path, quote, tables


def verify(app, mode):
    live=PiProvider(app,process_provider=lambda:[])
    snap=live.snapshot()
    with tempfile.TemporaryDirectory(prefix='pi-cleaner-storage-copy-') as folder:
        profile=Path(folder).resolve()
        store=PiProvider(app,profile,lambda:[])
        mappings=[(live.home,store.home)]
        for index,root in enumerate(live.sessions):
            if not root.is_relative_to(live.home):
                mappings.append((root,profile/'copied-sessions'/str(index)))
        for index,path in enumerate(sorted(live.registered)):
            if not any(path.is_relative_to(source) for source,_ in mappings):
                dest=profile/'registered'/str(index)/path.name
                mappings.extend([(path,dest),(path.with_suffix(''),dest.with_suffix(''))])
        mappings.sort(key=lambda pair:len(pair[0].parts),reverse=True)
        def mapped(path):
            path=canonical_path(path)
            for source,dest in mappings:
                if path==source or path.is_relative_to(source):
                    result=dest/path.relative_to(source)
                    assert result.resolve().is_relative_to(profile)
                    return result
            raise AssertionError('Unmapped source')
        store.sessions=[mapped(p) for p in live.sessions]
        store.roots=list(dict.fromkeys([store.home,*store.sessions]))
        files={p for group in snap['files'].values() for p in group}
        hashes={p:hashlib.sha256(p.read_bytes()).digest() for p in files}
        for source in files:
            dest=mapped(source);dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,dest)
            if source.parent.name in {'custom-session-files','terminal-sessions'} and source.parent.parent==live.home:
                lines=source.read_text(encoding='utf-8-sig').splitlines()
                index=1 if source.parent.name=='terminal-sessions' else 0
                target=Path(lines[index])
                if not target.is_absolute():target=Path(lines[0])/target
                lines[index]=str(mapped(target))
                dest.write_text('\n'.join(lines)+'\n',encoding='utf-8')
        for source in snap['databases']:
            dest=mapped(source);dest.parent.mkdir(parents=True,exist_ok=True)
            with contextlib.closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as a,contextlib.closing(sqlite3.connect(dest)) as b:
                a.backup(b)
                if source.name=='stats.db':
                    for table in STATS_TABLES & tables(b):
                        for (value,) in b.execute(f'SELECT DISTINCT session_file FROM {quote(table)}').fetchall():
                            if not isinstance(value,str) or not Path(value).is_absolute():continue
                            try:copy_path=mapped(value)
                            except AssertionError:continue
                            b.execute(f'UPDATE {quote(table)} SET session_file=? WHERE session_file=?',(str(copy_path),value))
                    b.commit()
        copied=store.snapshot()
        assert {r['id'] for r in copied['rows']}=={r['id'] for r in snap['rows']}
        assert all(p.is_relative_to(profile) for group in copied['files'].values() for p in group)
        count=len(copied['rows'])
        if count:
            # First delete one and verify that every unselected copied file keeps its bytes.
            selected=copied['rows'][0]['id']
            kept={p:p.read_bytes() for i,group in copied['files'].items() if i!=selected for p in group}
            policy={'mode':mode,'directory':str(profile/'backups')}
            store.apply(store.preview([selected],backup=policy))
            assert all(p.read_bytes()==data for p,data in kept.items())
            remaining=[r['id'] for r in store.snapshot()['rows']]
            for start in range(0,len(remaining),500):
                store.apply(store.preview(remaining[start:start+500],backup=policy))
            assert not store.snapshot()['rows']
            assert all(not p.exists() for group in copied['files'].values() for p in group)
        assert all(hashlib.sha256(p.read_bytes()).digest()==value for p,value in hashes.items())
        return {'app':app,'backup_mode':mode,'sessions':count,'status':'passed on isolated copies' if count else 'no local data','original_files_unchanged':True}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--app',choices=['pi','omp'])
    parser.add_argument('--backup-mode',choices=['none','custom'],default='none')
    args=parser.parse_args()
    result=[verify(app,args.backup_mode) for app in ([args.app] if args.app else ['pi','omp'])]
    print(json.dumps(result,ensure_ascii=False,indent=2))
    output=Path(__file__).resolve().parents[1]/'.local-reports'/('pi-copy-verification-'+args.backup_mode+'.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
