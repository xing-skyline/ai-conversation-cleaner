"""Check the DSH Node-process guard with an inert process owned by this test.
No Harness code or user data is opened; the marker is only a command-line arg.
"""
import shutil
import os
import subprocess
from cleaner.processes import dsh_processes


def main():
    node=shutil.which('node')
    if not node:raise RuntimeError('Node is required for this development probe.')
    child=subprocess.Popen([node,'-e','setInterval(()=>{}, 1000)',r'C:\test-only\.dsh\profiles\guard-fixture.js'],
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        assert child.pid in {p['pid'] for p in dsh_processes()}, 'DSH Node guard did not detect its test fixture'
        print('DSH Node process guard: passed')
    finally:
        child.terminate();child.wait(timeout=10)


if __name__=='__main__':main()
