import unittest

from cleaner.lifecycle import BrowserLifetime
from cleaner.store import CleanupError


class BrowserLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.life = BrowserLifetime(clock=lambda: self.now)

    def event(self, client='tab-a', event='heartbeat', sequence=1):
        self.life.browser_event(client, event, sequence)

    def test_last_tab_close_stops_after_refresh_grace(self):
        self.event()
        self.event(event='close', sequence=2)
        self.now = 2
        self.assertFalse(self.life.should_stop())
        self.now = 4
        self.assertTrue(self.life.should_stop())
        self.assertFalse(self.life.should_stop())  # Only dispatch shutdown once.
        with self.assertRaises(CleanupError):
            with self.life.operation():
                pass

    def test_refresh_and_another_open_tab_keep_server_alive(self):
        self.event()
        self.event(event='close', sequence=2)
        self.now = 1
        self.event('new-document')
        self.now = 5
        self.assertFalse(self.life.should_stop())
        self.event('second-tab')
        self.event('new-document', 'close', 2)
        self.now = 10
        self.assertFalse(self.life.should_stop())

    def test_delayed_heartbeat_cannot_reopen_a_closed_document(self):
        self.event(sequence=1)
        self.event(event='close', sequence=3)
        self.event(sequence=2)
        self.now = 4
        self.assertTrue(self.life.should_stop())

    def test_back_forward_cache_pageshow_renews_same_document(self):
        self.event()
        self.event(event='close', sequence=2)
        self.now = 1
        self.event(sequence=3)
        self.now = 5
        self.assertFalse(self.life.should_stop())

    def test_lost_browser_expires_but_background_heartbeat_stays_alive(self):
        self.event()
        self.now = 65
        self.assertFalse(self.life.should_stop())
        self.event(sequence=2)
        self.now = 240
        self.assertFalse(self.life.should_stop())
        self.now = 250
        self.assertTrue(self.life.should_stop())

    def test_auto_exit_waits_for_operation_and_rejects_new_work_after_quit(self):
        self.event()
        with self.life.operation():
            self.event(event='close', sequence=2)
            self.now = 4
            self.assertFalse(self.life.should_stop())
        self.assertTrue(self.life.should_stop())

    def test_explicit_quit_drains_in_flight_work(self):
        with self.life.operation():
            self.life.request_exit()
            self.assertFalse(self.life.should_stop())
            with self.assertRaises(CleanupError):
                with self.life.operation():
                    pass
        self.assertTrue(self.life.should_stop())

    def test_headless_is_opt_in_and_unopened_browser_has_startup_timeout(self):
        browser = BrowserLifetime(expect_browser=True, clock=lambda: self.now)
        self.now = 119
        self.assertFalse(browser.should_stop())
        self.now = 121
        self.assertTrue(browser.should_stop())
        self.assertFalse(self.life.should_stop())  # --no-browser API usage

    def test_malformed_browser_events_are_rejected(self):
        for client, event, sequence in [([], 'close', 1), ('tab', 'invalid', 1),
                                         ('tab', 'heartbeat', False), ('tab', 'close', -1)]:
            with self.assertRaises(CleanupError):
                self.life.browser_event(client, event, sequence)


if __name__ == '__main__':
    unittest.main()
