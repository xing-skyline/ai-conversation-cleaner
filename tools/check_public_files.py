"""Scan Git's staged/tracked public file manifest; never print matched secrets."""
import re
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
DENIED_PARTS={'.test-artifacts','.local-reports','.venv','.protocol','build','dist','release','.release','__pycache__'}
DENIED_SUFFIXES={'.exe','.zip','.db','.sqlite','.jsonl','.vscdb','.pyc','.pfx','.p12','.pem','.key','.log'}
PATTERNS={
    'possible GitHub token':rb'\bgh[pousr]_[A-Za-z0-9]{20,}\b',
    'possible API secret':rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b',
    'private key material':rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'hard-coded user profile':rb'(?i)[A-Z]:[\\/]+Users[\\/]+(?!Public\b|Default\b|example\b|sample\b)[^\\/\s\x22\x27]+',
}


def main():
    paths=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode('utf-8').split('\0')
    failures=[];count=0
    for name in filter(None,paths):
        path=Path(name);count+=1
        if set(path.parts)&DENIED_PARTS or path.suffix.lower() in DENIED_SUFFIXES or path.name.startswith('.env'):
            failures.append((name,'private/generated file type'));continue
        content=subprocess.check_output(['git','show',':'+name],cwd=ROOT)
        if len(content)>2_000_000:failures.append((name,'unexpected large source file'));continue
        if b'\0' in content:failures.append((name,'unexpected binary file'));continue
        for reason,pattern in PATTERNS.items():
            if re.search(pattern,content):failures.append((name,reason))
    if not count:raise SystemExit('No staged/tracked files: stage the intended public files before scanning.')
    for name,reason in failures:print(f'BLOCKED: {name}: {reason}')
    if failures:raise SystemExit(1)
    print(f'Public manifest checks passed ({count} text files). Manual privacy review is still required.')


if __name__=='__main__':main()
