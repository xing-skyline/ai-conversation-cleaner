"""Small, bounded stdio client for the installed Codex app-server."""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from . import __version__


class RpcError(RuntimeError):
    pass


def find_codex() -> str | None:
    found = shutil.which("codex.exe") or shutil.which("codex")
    if found and Path(found).suffix.lower() not in {".cmd", ".bat", ".ps1"}:
        return found
    candidates = [
        Path.home() / "AppData/Local/Programs/OpenAI/Codex/bin/codex.exe",
        Path.home() / ".codex/packages/standalone/current/codex.exe",
    ]
    if sys.platform == 'darwin':
        # Finder launches do not inherit Homebrew/npm's interactive shell PATH.
        candidates = [Path('/opt/homebrew/bin/codex'), Path('/usr/local/bin/codex'),
                      Path.home()/'.local/bin/codex', Path.home()/'.codex/packages/standalone/current/codex']
        candidates += [root/app/'Contents/Resources/codex'
                       for root in [Path('/Applications'), Path.home()/'Applications']
                       for app in ['Codex.app', 'ChatGPT.app']]
    return next((str(p) for p in candidates if p.is_file() and (os.name == 'nt' or os.access(p, os.X_OK))), None)


class CodexRpc:
    def __init__(self, home: Path, executable: str | None = None):
        exe = executable or find_codex()
        if not exe:
            raise RpcError("没有找到 Codex CLI 可执行文件。")
        environment = dict(os.environ)
        # This is the app-server's actual data directory, never a shell variable.
        environment["CODEX_HOME"] = str(home)
        self.proc = subprocess.Popen(
            [exe, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="utf-8", text=True, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.inbox: queue.Queue = queue.Queue()
        self.seq = 0
        self.reader = threading.Thread(target=self._reader, daemon=True)
        self.reader.start()
        try:
            self.call("initialize", {"clientInfo": {"name": "ai_conversation_cleaner", "version": __version__},
                                     "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def _reader(self):
        try:
            for line in self.proc.stdout:
                try:
                    self.inbox.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            self.inbox.put(None)

    def send(self, message):
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def call(self, method, params, timeout=25):
        self.seq += 1
        request_id = self.seq
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            try:
                message = self.inbox.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty as error:
                raise RpcError(f"Codex 接口超时：{method}") from error
            if message is None:
                raise RpcError("Codex app-server 提前退出。")
            if "method" in message and "id" in message:
                self.send({"id": message["id"], "error": {"code": -32601, "message": "Unsupported"}})
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise RpcError(str(message["error"].get("message", message["error"])))
                return message.get("result", {})
            if time.monotonic() >= deadline:
                raise RpcError(f"Codex 接口超时：{method}")

    def close(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.terminate()
                self.proc.wait(timeout=5)
        self.reader.join(timeout=2)
        self.proc.stdout.close()
        if not self.proc.stdin.closed:
            self.proc.stdin.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
