"""Platform regressions use only temporary data and inert owned processes."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cleaner import folders, processes, rpc
from cleaner.demo import create_demo
from cleaner.providers import LocalProvider
from cleaner.store import CleanupError, Store


class PlatformPathsTests(unittest.TestCase):
    def test_native_data_and_backup_locations(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory).resolve()
            for system, appdata, backup in [
                ('darwin', 'Library/Application Support', 'Library/Application Support'),
                ('win32', 'AppData/Roaming', 'AppData/Local'),
            ]:
                with self.subTest(system=system), patch('sys.platform', system):
                    cursor = LocalProvider('cursor', profile)
                    anti = LocalProvider('antigravity', profile)
                    self.assertEqual(cursor.home, profile/appdata/'Cursor/User')
                    self.assertIn(profile/appdata/'Antigravity/User', anti.roots)
                    self.assertEqual(cursor.backup_root, profile/backup/'AIConversationCleaner/backups/cursor')
                    for app, folder in [('claude', '.claude'), ('grok', '.grok'), ('deepseek', '.dsh'), ('opencode', '.local/share/opencode')]:
                        self.assertEqual(LocalProvider(app, profile).home, profile/folder)

    def test_finder_launch_finds_bundled_codex_without_shell_path(self):
        target = Path('/Applications/ChatGPT.app/Contents/Resources/codex')
        with patch('sys.platform', 'darwin'), patch('cleaner.rpc.shutil.which', return_value=None), \
             patch.object(Path, 'is_file', lambda p: p == target), patch('os.access', return_value=True):
            self.assertEqual(rpc.find_codex(), str(target))


@patch('sys.platform', 'darwin')
class MacProcessesTests(unittest.TestCase):
    def test_native_apps_helpers_and_paths_with_spaces(self):
        listing = ''' 10 /Applications/ChatGPT.app/Contents/MacOS/ChatGPT
 11 /Applications/Codex.app/Contents/Frameworks/Codex (Renderer).app/Contents/MacOS/Codex (Renderer)
 12 /Applications/Cursor.app/Contents/MacOS/Cursor
 13 /Applications/Cursor.app/Contents/Frameworks/Cursor Helper (Plugin).app/Contents/MacOS/Cursor Helper (Plugin)
 14 /Applications/Antigravity.app/Contents/Resources/language_server_macos_arm
 15 /Applications/Other.app/Contents/MacOS/CursorUIViewService
 16 /Applications/AIConversationCleaner.app/Contents/MacOS/AIConversationCleaner
'''
        with patch('cleaner.processes.subprocess.run', return_value=subprocess.CompletedProcess([], 0, listing, '')):
            self.assertEqual({p['pid'] for p in processes.codex_processes()}, {10, 11})
            self.assertEqual({p['pid'] for p in processes.cli_processes('cursor', {'cursor.exe'})}, {12, 13})
            self.assertEqual({p['pid'] for p in processes.cli_processes('antigravity', {'antigravity.exe'})}, {14})

    def test_only_node_and_bun_commands_are_scanned_for_package_names(self):
        executables = '21 /opt/homebrew/bin/node\n22 /usr/local/bin/bun\n23 /usr/bin/python3\n24 /opt/homebrew/bin/node\n'
        arguments = '21 node /Users/example/My Tools/node_modules/@anthropic-ai/claude-code/cli.js\n22 bun /tmp/node_modules/@anthropic-ai/claude-code/cli.js\n23 python3 check.py /tmp/node_modules/@anthropic-ai/claude-code/cli.js\n24 node /tmp/ordinary-server.js\n'
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, arguments if 'pid=,args=' in command else executables, '')
        with patch('cleaner.processes.subprocess.run', side_effect=run):
            self.assertEqual({p['pid'] for p in processes.cli_processes('claude', {'claude.exe'})}, {21, 22})

    def test_process_inspection_failure_blocks_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalProvider('claude', Path(directory))
            for result in [subprocess.CompletedProcess([], 1, '', 'denied'),
                           subprocess.CompletedProcess([], 0, 'invalid process listing', '')]:
                with self.subTest(result=result), patch('cleaner.processes.subprocess.run', return_value=result):
                    with self.assertRaisesRegex(RuntimeError, '进程'):
                        store.assert_offline()


class MacFolderTests(unittest.TestCase):
    @patch('sys.platform', 'darwin')
    def test_picker_unicode_cancel_failure_and_argument_safety(self):
        initial = '/tmp/我的备份 "quoted"; $(ignored)'
        with patch('cleaner.folders.subprocess.run', return_value=subprocess.CompletedProcess([], 0, (initial+'/\n').encode(), b'')) as run:
            self.assertEqual(folders.choose_backup_directory(initial), initial+'/')
            args = run.call_args.args[0]
            self.assertEqual(args[-1], initial)
            self.assertNotIn(initial, args[2])
        with patch('cleaner.folders.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'\n', b'')):
            self.assertIsNone(folders.choose_backup_directory())
        with patch('cleaner.folders.subprocess.run', return_value=subprocess.CompletedProcess([], 1, b'', b'failed')):
            with self.assertRaises(CleanupError): folders.choose_backup_directory()

    @patch('sys.platform', 'darwin')
    def test_open_backup_folder_uses_finder_without_a_shell(self):
        target = Path('/tmp/我的 备份')
        with patch('cleaner.folders.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as run:
            folders.open_directory(target)
            self.assertEqual(run.call_args.args[0], ['/usr/bin/open', str(target)])
            self.assertFalse(run.call_args.kwargs.get('shell', False))


class ExclusiveOperationTests(unittest.TestCase):
    def test_multiple_live_codex_versions_still_block(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            create_demo(home)
            shutil.copy2(home/'state_5.sqlite', home/'state_6.sqlite')
            with self.assertRaisesRegex(CleanupError, '多个 state'):
                Store(home).databases()

    def test_codex_database_backup_is_not_treated_as_a_live_version(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            create_demo(home)
            backup = home/'state_5.sqlite.remote-control-backup-20000101-000000.sqlite'
            shutil.copy2(home/'state_5.sqlite', backup)
            original = backup.read_bytes()
            store = Store(home, process_provider=lambda: [], rpc_factory=None)
            rows = store.inventory()['rows']
            result = store.apply(store.preview([rows[0]['id']]))
            self.assertEqual(result['remaining'], 3)
            self.assertEqual(backup.read_bytes(), original)

    def test_another_process_cannot_lock_the_same_home_then_can_after_release(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            create_demo(home)
            store = Store(home, process_provider=lambda: [], rpc_factory=None)
            code = '''
import sys
from pathlib import Path
from cleaner.store import Store, CleanupError
try:
    with Store(Path(sys.argv[1])).exclusive(): pass
except CleanupError:
    sys.exit(23)
'''
            def attempt():
                return subprocess.run([sys.executable, '-c', code, str(home)], cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=15)
            with store.exclusive():
                self.assertEqual(attempt().returncode, 23)
            result = attempt()
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__': unittest.main()
