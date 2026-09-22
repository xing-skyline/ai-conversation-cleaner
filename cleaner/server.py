from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .demo import create_demo
from .store import CleanupError, Store
from .providers import LABELS, make_providers
from .backups import backup_destination
from .folders import choose_backup_directory
from .lifecycle import BrowserLifetime


def assets_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "web"


def select_provider(providers, requested, inventory=False):
    if requested in providers:return providers[requested]
    if inventory or not providers:raise CleanupError('应用的数据目录尚不存在。')
    return next(iter(providers.values()))


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, store, port=0, demo=False, providers=None, expect_browser=False):
        self.store = store
        self.providers = providers or {"codex":store}
        self.token = secrets.token_urlsafe(32)
        self.plans = {}
        self.plan_lock = threading.Lock()
        self.demo = demo
        self.lifecycle = BrowserLifetime(expect_browser=expect_browser)
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"

    def service_actions(self):
        if self.lifecycle.should_stop():
            # shutdown() must run outside the serve_forever thread.
            threading.Thread(target=self.shutdown, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, status, body, content_type="application/json; charset=utf-8"):
        payload = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def auth(self):
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            raise CleanupError("Host 校验失败。")
        supplied = self.headers.get("X-Cleaner-Token", "")
        if not hmac.compare_digest(supplied, self.server.token):
            raise CleanupError("访问凭证失效，请通过启动器重新打开。")
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.server.origin:
            raise CleanupError("不允许来自其他网站的请求。")

    def do_GET(self):
        self.guarded(self.get)

    def do_POST(self):
        self.guarded(self.post)

    def guarded(self, callback):
        try:
            with self.server.lifecycle.operation():
                callback()
        except CleanupError as error:
            self.respond(503, {"error": str(error)})

    def get(self):
        parsed = urlsplit(self.path)
        if parsed.path in {"/", "/app.js", "/style.css", "/apps.css"}:
            if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
                return self.respond(403, {"error": "Host 校验失败"})
            name, mime = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                          "/style.css": ("style.css", "text/css; charset=utf-8"),
                          "/apps.css": ("apps.css", "text/css; charset=utf-8")}[parsed.path]
            return self.respond(200, (assets_root() / name).read_bytes(), mime)
        try:
            self.auth()
            if parsed.path == "/api/apps":
                return self.respond(200, [{"id":key,"label":LABELS[key]} for key in self.server.providers])
            app = parse_qs(parsed.query).get("app", ["codex"])[0]
            store = self.server.providers.get(app)
            if store is None:
                raise CleanupError("应用不存在或数据目录不可用。")
            if parsed.path == "/api/inventory":
                return self.respond(200, store.inventory() | {"demo": self.server.demo, "app":app,"label":LABELS[app]})
            if parsed.path == "/api/detail":
                target = parse_qs(parsed.query).get("id", [""])[0]
                return self.respond(200, store.detail(target))
            self.respond(404, {"error": "接口不存在"})
        except (CleanupError, OSError, ValueError, RuntimeError, sqlite3.DatabaseError) as error:
            self.respond(400, {"error": str(error)})

    def post(self):
        try:
            self.auth()
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise CleanupError("必须使用 JSON 请求。")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 65536:
                raise CleanupError("请求体长度错误。")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise CleanupError("请求体必须是对象。")
            if self.path == '/api/browser':
                self.server.lifecycle.browser_event(data.get('client_id'), data.get('event'), data.get('sequence'))
                return self.respond(200, {'ok': True})
            if self.path == '/api/quit':
                pending = self.server.lifecycle.request_exit()
                return self.respond(200, {'ok': True, 'pending': pending})
            app = data.get("app", "codex")
            store = self.server.providers.get(app)
            if store is None:
                raise CleanupError("应用不存在或数据目录不可用。")
            if self.path == "/api/preview":
                plan = store.preview(data.get("ids"),backup=data.get('backup'))
                token = secrets.token_urlsafe(24)
                with self.server.plan_lock:
                    self.server.plans = {token: (time.monotonic(), plan, app)}
                return self.respond(200, plan | {"plan_token": token})
            if self.path == "/api/delete":
                with self.server.plan_lock:
                    entry = self.server.plans.pop(data.get("plan_token", ""), None)
                if entry is None or time.monotonic() - entry[0] > 900:
                    raise CleanupError("删除预览已失效，请重新预览。")
                if entry[2] != app:
                    raise CleanupError("预览后切换了应用，请重新预览。")
                return self.respond(200, store.apply(entry[1]))
            if self.path == "/api/open-backups":
                destination=backup_destination(store,data.get('backup'))
                if destination is None:raise CleanupError('当前选择不备份，没有本次备份目录。')
                destination.mkdir(parents=True, exist_ok=True)
                os.startfile(str(destination))
                return self.respond(200, {"ok": True})
            if self.path == '/api/choose-backup-directory':
                return self.respond(200, {'directory':choose_backup_directory(data.get('initial',''))})
            self.respond(404, {"error": "接口不存在"})
        except Exception as error:
            self.respond(400, {"error": str(error)})


def main():
    parser = argparse.ArgumentParser(description="AI Conversation Cleaner · 本地会话清理器")
    parser.add_argument("--app", choices=LABELS, default="codex")
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--demo", action="store_true", help="使用隔离示例数据，可放心试删")
    parser.add_argument("--inventory", action="store_true", help="只读盘点，输出 JSON 后退出")
    parser.add_argument("--url-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    temp = None
    try:
        if args.demo:
            temp = tempfile.TemporaryDirectory(prefix="codex-cleaner-demo-")
            home = Path(temp.name)
            create_demo(home)
            store = Store(home, process_provider=lambda: [], rpc_factory=None)
            providers = {"codex":store}
        else:
            providers = make_providers(args.home)
            store = select_provider(providers,args.app,inventory=args.inventory)
        if args.inventory:
            print(json.dumps(store.inventory(), ensure_ascii=False, indent=2))
            return
        with AppServer(store, port=args.port, demo=args.demo, providers=providers,
                       expect_browser=not args.no_browser) as server:
            url = server.origin + "/#token=" + server.token
            if args.url_file:
                args.url_file.write_text(url, encoding="utf-8")
            if sys.stdout:
                print(url, flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            server.serve_forever(poll_interval=.3)
    finally:
        if temp:
            temp.cleanup()


if __name__ == "__main__":
    main()
