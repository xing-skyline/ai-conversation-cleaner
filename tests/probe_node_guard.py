"""Check CLI Node-process guards with inert processes owned by this test.
No application code or user data is opened; each marker is only an argument.
"""
import shutil
import os
import subprocess
from cleaner.processes import cli_processes
from cleaner.process_control import capture_processes, close_selected_processes


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
            if app == 'deepseek':
                # Only this inert child is eligible, even on a developer's
                # machine with real CLI processes running. Never close them.
                provider=lambda:[p for p in cli_processes(app,set()) if p['pid']==child.pid]
                rows,tickets,error=capture_processes(provider)
                assert not error and len(tickets)==1, error or 'missing fixture identity'
                close_selected_processes(provider,tickets)
                child.wait(timeout=10)
                print('Opt-in close of exact test-owned process: passed')
        finally:
            child.terminate();child.wait(timeout=10)


if __name__=='__main__':main()
