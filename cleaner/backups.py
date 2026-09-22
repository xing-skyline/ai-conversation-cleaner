"""Backup policy and metadata-only operation journals shared by all adapters."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path


def normalize_backup(store, choice=None):
    from .store import CleanupError
    if choice is None:choice={'mode':'default'}
    if not isinstance(choice,dict) or choice.get('mode') not in {'default','custom','none'}:
        raise CleanupError('备份方式无效，请重新选择。')
    mode=choice['mode']
    if mode=='none':return {'mode':'none','directory':None}
    if mode=='default':return {'mode':'default','directory':str(store.backup_root)}
    directory=choice.get('directory')
    if not isinstance(directory,str) or not directory.strip() or not Path(directory.strip()).is_absolute():
        raise CleanupError('请选择或输入备份文件夹的绝对路径。')
    target=Path(directory.strip()).resolve()
    if target.exists() and not target.is_dir():raise CleanupError('备份路径不是文件夹。')
    # Do not place copied histories where an application will discover them as live sessions.
    for root in getattr(store,'roots',[store.home]):
        if target.is_relative_to(root.resolve()):
            raise CleanupError('自定义备份目录不能位于应用的会话数据目录内。')
    return {'mode':'custom','directory':str(target)}


def backup_destination(store, choice=None):
    choice=normalize_backup(store,choice)
    if choice['mode']=='none':return None
    root=Path(choice['directory'])
    if choice['mode']=='custom':root=root/'AIConversationCleaner'/getattr(store,'app','codex')
    return root


def check_pending(store):
    from .store import CleanupError
    paths=[*store.backup_root.glob('*/result.json'),*(store.backup_root/'.operations').glob('*.json')]
    for path in paths:
        value=json.loads(path.read_text(encoding='utf-8'))
        if value.get('status') in {'executing','recovery_required'}:
            raise CleanupError('上次删除未完成，请先检查操作记录及备份：'+str(path))


class Operation:
    def __init__(self,store,plan):
        self.policy=normalize_backup(store,plan.get('backup'))
        self.app=getattr(store,'app','codex')
        self.ids=plan['ids']
        run_id=dt.datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8]
        root=backup_destination(store,self.policy)
        self.run=root/run_id if root is not None else None
        self.journal=store.backup_root/'.operations'/(run_id+'.json')
        if self.run is not None:self.run.mkdir(parents=True)
        self.journal.parent.mkdir(parents=True,exist_ok=True)

    def fields(self):
        return {'backup_mode':self.policy['mode'],'backup_dir':str(self.run) if self.run else None,
                'journal_path':str(self.journal)}

    def save(self,record):
        from .store import atomic_json
        # No-backup mode never persists the plan, titles, messages or a database copy.
        # Only target IDs and operation state are kept for crash detection.
        if self.run is not None:atomic_json(self.run/'result.json',record)
        atomic_json(self.journal,{'app':self.app,'ids':self.ids,'status':record['status'],**self.fields()})
