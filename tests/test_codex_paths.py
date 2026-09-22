import os
import tempfile
import unittest
from pathlib import Path

from cleaner.demo import IDS, create_demo
from cleaner.store import CleanupError, Store, windows_path_text
from tests.test_cleaner import sql


class WindowsNamespaceTests(unittest.TestCase):
    def test_unc_prefix_is_normalized_without_accessing_a_share(self):
        self.assertEqual(windows_path_text(r'\\?\UNC\server\share\sessions\a.jsonl'),
                         r'\\server\share\sessions\a.jsonl')

    def test_device_and_ambiguous_verbatim_names_are_rejected(self):
        for value in (r'\\.\PhysicalDrive0', r'\\?\GLOBALROOT\Device\HarddiskVolume1',
                      r'\\?\C:\Example\ambiguous.\a.jsonl', r'\\?\C:\Example\ambiguous \a.jsonl'):
            with self.subTest(path=value), self.assertRaises(CleanupError):
                windows_path_text(value)


@unittest.skipUnless(os.name == 'nt', 'Windows path namespace regression')
class CodexPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cleaner-path-tests-')
        self.root = Path(self.temp.name).resolve()
        create_demo(self.root)
        self.store = Store(self.root, process_provider=lambda: [], rpc_factory=None)

    def tearDown(self):
        self.temp.cleanup()

    def test_extended_rollout_can_be_previewed_and_deleted(self):
        path = self.store.detail(IDS[0])['files'][0]
        extended = '\\\\?\\' + path
        sql(self.root/'state_5.sqlite', 'UPDATE threads SET rollout_path=? WHERE id=?', (extended, IDS[0]))
        result = self.store.apply(self.store.preview([IDS[0]], backup={'mode': 'none'}))
        self.assertEqual(result['remaining'], 3)
        self.assertFalse(Path(path).exists())

    def test_extended_home_and_regular_home_have_same_identity(self):
        extended = Store(Path('\\\\?\\' + str(self.root)), process_provider=lambda: [], rpc_factory=None)
        self.assertEqual(extended.home, self.store.home)
        self.assertEqual(extended.snapshot()['fingerprint'], self.store.snapshot()['fingerprint'])

    def test_extended_outside_rollout_remains_blocked(self):
        with tempfile.TemporaryDirectory(prefix='cleaner-outside-') as directory:
            outside = Path(directory).resolve()/'rollout.jsonl'
            outside.write_text('{}', encoding='utf-8')
            sql(self.root/'state_5.sqlite', 'UPDATE threads SET rollout_path=? WHERE id=?',
                ('\\\\?\\' + str(outside), IDS[0]))
            with self.assertRaisesRegex(CleanupError, '超出'):
                self.store.preview([IDS[0]])
            self.assertTrue(outside.exists())


if __name__ == '__main__':
    unittest.main()
