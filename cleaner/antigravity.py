"""Recognized Antigravity 2.x conversation databases and hub summary files."""
import contextlib
from pathlib import Path

from .protobuf import summary_map_bytes
from .store import CleanupError, columns, connect, tables


HUB_SUMMARIES = 'agyhub_summaries_proto.pb'


def sidecars(path):
    return {Path(str(path)+suffix) for suffix in ('-wal', '-shm')}


def validate_conversation(path, target):
    # Filenames use cascade_id; trajectory_id identifies an internal trajectory.
    with contextlib.closing(connect(path)) as con:
        if not {'trajectory_meta', 'steps'} <= tables(con) or not {
                'trajectory_id', 'cascade_id'} <= columns(con, 'trajectory_meta') or not {
                'idx', 'step_payload'} <= columns(con, 'steps'):
            raise CleanupError('Antigravity 会话数据库结构已变化：'+str(path))
        owners = {row[0] for row in con.execute('SELECT cascade_id FROM trajectory_meta')}
        if owners != {target}:
            raise CleanupError('Antigravity 会话数据库 ID 与文件名不一致，无法确认归属：'+str(path))


def prune_hub(path, ids):
    _, kept = summary_map_bytes(path.read_bytes(), ids)
    temporary = path.with_suffix(path.suffix+'.cleaner.tmp')
    try:
        temporary.write_bytes(kept)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_deleted(roots, ids):
    """Check the physical stores independently of inventory discovery."""
    for base in roots[:2]:
        for target in ids:
            db = base/'conversations'/(target+'.db')
            for path in {db, *sidecars(db), db.with_suffix('.pb')}:
                if path.exists():
                    raise CleanupError('Antigravity 删除后仍有会话正文或数据库附属文件：'+str(path))
        hub = base/HUB_SUMMARIES
        if hub.is_file() and ids.intersection(summary_map_bytes(hub.read_bytes())[0]):
            raise CleanupError('Antigravity 删除后仍有 Hub 摘要记录。')
