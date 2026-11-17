"""Check CLI Node-process guards with inert processes owned by this test.
No application code or user data is opened; each marker is only an argument.
"""
import shutil
import os
import subprocess
from cleaner.processes import cli_processes


def main():
    node=shutil.which('node')
    if not node:raise RuntimeError('Node is required for this development probe.')
    prefix='C:/test-only' if os.name=='nt' else '/tmp/test-only'
    for app,marker in [('deepseek','.dsh/profiles/guard-fixture.js'),
                       ('pi','node_modules/@earendil-works/pi-coding-agent/dist/cli.js'),
                       ('pi','node_modules/@mariozechner/pi-coding-agent/dist/cli.js'),
                       ('pi','.pi/agent/bin/pi-launcher.js'),
                       ('omp','node_modules/@oh-my-pi/pi-coding-agent/dist/cli.js')]:
        child=subprocess.Popen([node,'-e','setInterval(()=>{}, 1000)',prefix+'/'+marker],
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            assert child.pid in {p['pid'] for p in cli_processes(app,set())}, app+' Node guard missed its test fixture'
            print(app+' Node process guard: passed')
        finally:
            child.terminate();child.wait(timeout=10)


if __name__=='__main__':main()
