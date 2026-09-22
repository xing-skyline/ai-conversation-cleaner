"""Stop an unused browser-backed server without interrupting active operations."""
from contextlib import contextmanager
import re
import threading
import time

from .store import CleanupError


class BrowserLifetime:
    CLOSE_GRACE = 3
    HEARTBEAT_TIMEOUT = 180
    STARTUP_TIMEOUT = 120

    def __init__(self, expect_browser=False, clock=time.monotonic):
        self.clock = clock
        self.startup_deadline = clock() + self.STARTUP_TIMEOUT if expect_browser else None
        self.clients = {}
        self.idle_since = None
        self.active = 0
        self.exit_requested = False
        self.shutdown_started = False
        self.lock = threading.Lock()

    @contextmanager
    def operation(self):
        with self.lock:
            if self.exit_requested or self.shutdown_started:
                raise CleanupError('工具正在退出，请重新启动后操作。')
            self.active += 1
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1

    def browser_event(self, client, event, sequence):
        if (not isinstance(client, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', client)
                or event not in ('heartbeat', 'close') or type(sequence) is not int
                or not 0 < sequence < 2**53):
            raise CleanupError('浏览器连接信息无效。')
        with self.lock:
            now = self.clock()
            previous = self.clients.get(client)
            if previous and sequence <= previous[1]:
                return  # A late heartbeat must not undo pagehide.
            self.clients[client] = (now, sequence, event != 'close')
            self.startup_deadline = None
            if event == 'heartbeat':
                self.idle_since = None
            elif not any(connected and now - updated < self.HEARTBEAT_TIMEOUT
                         for updated, _, connected in self.clients.values()):
                self.idle_since = now

    def request_exit(self):
        with self.lock:
            self.exit_requested = True
            return self.active > 1  # The quit request itself counts as one operation.

    def should_stop(self):
        with self.lock:
            if self.shutdown_started:
                return False
            now = self.clock()
            connected = [updated for updated, _, alive in self.clients.values() if alive]
            live = any(now - updated < self.HEARTBEAT_TIMEOUT for updated in connected)
            if connected and not live and self.idle_since is None:
                self.idle_since = max(connected) + self.HEARTBEAT_TIMEOUT
            due = self.exit_requested or (
                not live and self.idle_since is not None and now - self.idle_since >= self.CLOSE_GRACE
            ) or (self.startup_deadline is not None and now >= self.startup_deadline)
            if not due or self.active:
                return False
            self.shutdown_started = True
            return True
