import tempfile
import unittest
from pathlib import Path

from cleaner import processes, server
from cleaner.providers import make_providers
from cleaner.store import CleanupError


class ReleaseReadinessTests(unittest.TestCase):
    def test_web_starts_without_a_codex_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            providers=make_providers(Path(directory)/'.codex',Path(directory))
            selected=server.select_provider(providers,'codex',inventory=False)
            self.assertIn(selected,providers.values())
            with self.assertRaises(CleanupError):server.select_provider(providers,'codex',inventory=True)

    def test_node_cli_patterns_match_only_expected_apps(self):
        cases={
            'claude':r'node "C:\sample\node_modules\@anthropic-ai\claude-code\cli.js"',
            'grok':r'node "C:\sample\node_modules\@xai\grok-build\cli.js"',
            'deepseek':r'node "C:\sample\.dsh\profiles\node_modules\@deepseek-ai\dsh\lib\bin.js"',
            'opencode':r'node "C:\sample\node_modules\opencode-ai\bin\opencode"',
        }
        import re
        for app,command in cases.items():
            self.assertRegex(command,processes.NODE_PATTERNS[app])
            self.assertIsNone(re.search(processes.NODE_PATTERNS[app],r'node C:\sample\ordinary-server.js'))


if __name__=='__main__':unittest.main()
