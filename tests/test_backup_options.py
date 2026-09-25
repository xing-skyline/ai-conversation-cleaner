import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cleaner.demo import IDS, create_demo
from cleaner.providers import LocalProvider
from cleaner.store import CleanupError, Store
from tests.test_cleaner import put


class BackupOptionsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ai-cleaner-backup-tests-')
        self.root=Path(self.temp.name)
        codex=self.root/'codex';codex.mkdir();create_demo(codex)
        profile=self.root/'profile'
        put(profile/'.claude/history.jsonl',[{'sessionId':i,'display':'private fixture content'} for i in IDS[:2]],True)
        self.stores=[Store(codex,process_provider=lambda:[],rpc_factory=None),LocalProvider('claude',profile,process_provider=lambda:[])]

    def tearDown(self):self.temp.cleanup()

    def test_omitted_backup_defaults_to_none(self):
        for store in self.stores:
            plan=store.preview([IDS[0]])
            self.assertEqual(plan['backup'],{'mode':'none','directory':None})
            report=store.apply(plan)
            self.assertIsNone(report['backup_dir']);self.assertEqual(report['backups'],[])

    def test_no_backup_does_not_copy_content(self):
        for store in self.stores:
            with self.subTest(store=type(store).__name__),patch('shutil.copy2',side_effect=AssertionError('must not copy')):
                report=store.apply(store.preview([IDS[0]],backup={'mode':'none'}))
            self.assertIsNone(report['backup_dir']);self.assertEqual(report['backups'],[])
            self.assertEqual(report['backup_mode'],'none')
            self.assertFalse(list(store.backup_root.glob('*/result.json')))
            journal=json.loads(Path(report['journal_path']).read_text(encoding='utf-8'))
            self.assertNotIn('plan',journal);self.assertNotIn('rows',journal)
            self.assertNotIn('private fixture content',json.dumps(journal))
            self.assertNotIn(IDS[0],{r['id'] for r in store.inventory()['rows']})

    def test_custom_directory_contains_verified_backup(self):
        custom=self.root/'我的 备份'
        for store in self.stores:
            report=store.apply(store.preview([IDS[0]],backup={'mode':'custom','directory':str(custom)}))
            directory=Path(report['backup_dir'])
            # Windows TEMP can use an 8.3 alias; the policy returns resolved paths.
            self.assertTrue(directory.resolve().is_relative_to(custom.resolve()),
                            f'{directory} is outside the selected backup directory {custom.resolve()}')
            self.assertTrue((directory/'result.json').is_file());self.assertTrue(report['backups'])
            self.assertTrue(all(Path(item['backup']).is_file() for item in report['backups']))
            self.assertFalse(list(store.backup_root.glob('*/result.json')))

    def fail_after_deletion(self,store):
        original=store.snapshot;failed=False
        def inspect():
            nonlocal failed
            snapshot=original()
            if not failed and IDS[0] not in {r['id'] for r in snapshot['rows']}:
                failed=True;raise OSError('injected verification failure')
            return snapshot
        return patch.object(store,'snapshot',side_effect=inspect)

    def test_no_backup_failure_never_claims_rollback(self):
        for store in self.stores:
            plan=store.preview([IDS[0]],backup={'mode':'none'})
            with self.fail_after_deletion(store),self.assertRaisesRegex(CleanupError,'failed_no_backup'):
                store.apply(plan)
            self.assertNotIn(IDS[0],{r['id'] for r in store.inventory()['rows']})
            journals=list((store.backup_root/'.operations').glob('*.json'))
            self.assertEqual(json.loads(journals[0].read_text(encoding='utf-8'))['status'],'failed_no_backup')

    def test_custom_backup_still_rolls_back(self):
        for store in self.stores:
            before={r['id'] for r in store.inventory()['rows']}
            plan=store.preview([IDS[0]],backup={'mode':'custom','directory':str(self.root/'recovery')})
            with self.fail_after_deletion(store),self.assertRaisesRegex(CleanupError,'rolled_back'):
                store.apply(plan)
            self.assertEqual({r['id'] for r in store.inventory()['rows']},before)

    def test_invalid_policy_and_active_data_directory_rejected(self):
        for store in self.stores:
            for choice in [{'mode':'default'},{'mode':'other'},{'mode':'custom','directory':''},{'mode':'custom','directory':'relative'},
                           {'mode':'custom','directory':str(store.home/'sessions')}]:
                with self.subTest(choice=choice),self.assertRaises(CleanupError):store.preview([IDS[0]],backup=choice)

    def test_custom_unfinished_operation_blocks_all_modes(self):
        for store in self.stores:
            journal=store.backup_root/'.operations'/'unfinished.json'
            put(journal,{'status':'executing','backup_dir':str(self.root/'external')})
            with self.assertRaisesRegex(CleanupError,'未完成'):
                store.apply(store.preview([IDS[0]],backup={'mode':'none'}))


if __name__=='__main__':unittest.main()
