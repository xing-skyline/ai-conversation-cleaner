"""Real official protocol test, in a disposable home with no user account data."""
import json
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

from cleaner.rpc import CodexRpc


def main():
    temporary = tempfile.TemporaryDirectory(prefix="codex-cleaner-protocol-")
    try:
        home = Path(temporary.name)
        with CodexRpc(home) as rpc:
            result = rpc.call("thread/start", {"cwd": str(Path(__file__).resolve().parent.parent), "ephemeral": False})
            target = result["thread"]["id"]
            print("created", target)
            try:
                print("delete", rpc.call("thread/delete", {"threadId": target}))
            except Exception as error:
                print("first delete", str(error))
                print("archive", rpc.call("thread/archive", {"threadId": target}))
                print("delete", rpc.call("thread/delete", {"threadId": target}))
        db = next(home.glob("state_*.sqlite"))
        with closing(sqlite3.connect(db)) as con:
            count = con.execute("SELECT count(*) FROM threads WHERE id=?", (target,)).fetchone()[0]
        remaining = list((home / "sessions").rglob("*.jsonl")) + list((home / "archived_sessions").rglob("*.jsonl"))
        assert count == 0, count
        assert not remaining, remaining
        print(json.dumps({"official_delete": "passed", "state_rows": count, "rollouts": len(remaining)}))
    finally:
        for attempt in range(30):
            try:
                temporary.cleanup()
                break
            except PermissionError:
                if attempt == 29:raise
                time.sleep(.1)


if __name__ == "__main__":
    main()
