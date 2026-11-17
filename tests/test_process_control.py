import ctypes
import unittest
from unittest.mock import patch, Mock

from cleaner import process_control as control


class ProcessControlTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{'pid': 12345, 'name': 'fixture'}]
        self.tickets = [dict(self.rows[0], identity=(100, 20))]
        self.identity = patch.object(control, 'process_identity', return_value=(100, 20)).start()
        self.protected = patch.object(control, 'protected_pids', return_value={0, 1, 999}).start()
        self.close = patch.object(control, 'close_process').start()
        self.addCleanup(patch.stopall)

    def test_capture_deduplicates_and_keeps_identity_private(self):
        rows, tickets, error = control.capture_processes(lambda: self.rows * 2)
        self.assertEqual(rows, self.rows)
        self.assertEqual(tickets, self.tickets)
        self.assertIsNone(error)
        self.close.assert_not_called()

    def test_protected_preview_disables_close_without_blocking_manual_delete(self):
        self.protected.return_value = {12345}
        rows, tickets, error = control.capture_processes(lambda: self.rows)
        self.assertEqual(rows, self.rows)
        self.assertEqual(tickets, [])
        self.assertIn('单独启动', error)

    def test_close_only_reviewed_process_and_wait_for_offline(self):
        provider = Mock(side_effect=[self.rows, []])
        control.close_selected_processes(provider, self.tickets)
        self.close.assert_called_once_with(12345, (100, 20))
        self.assertEqual(provider.call_count, 2)

    def test_new_process_blocks_every_signal(self):
        with self.assertRaisesRegex(control.ProcessCloseError, '新启动'):
            control.close_selected_processes(lambda: self.rows + [{'pid': 23456}], self.tickets)
        self.close.assert_not_called()

    def test_reused_pid_blocks_every_signal(self):
        self.identity.return_value = (100, 21)
        with self.assertRaisesRegex(control.ProcessCloseError, '进程在预览后'):
            control.close_selected_processes(lambda: self.rows, self.tickets)
        self.close.assert_not_called()

    def test_protected_parent_is_never_closed(self):
        self.protected.return_value = {12345}
        with self.assertRaisesRegex(control.ProcessCloseError, '自身或启动它'):
            control.close_selected_processes(lambda: self.rows, self.tickets)
        self.close.assert_not_called()

    def test_already_exited_is_harmless(self):
        control.close_selected_processes(lambda: [], self.tickets)
        self.close.assert_not_called()

    def test_failed_close_or_restart_blocks_delete(self):
        with self.assertRaisesRegex(control.ProcessCloseError, '仍在运行'):
            control.close_selected_processes(lambda: self.rows, self.tickets)

    def test_unreadable_identity_disables_close(self):
        self.identity.side_effect = PermissionError('fixture access denied')
        rows, tickets, error = control.capture_processes(lambda: self.rows)
        self.assertEqual(rows, self.rows)
        self.assertEqual(tickets, [])
        self.assertIn('access denied', error)

    def test_darwin_bsd_struct_matches_native_abi(self):
        self.assertEqual(ctypes.sizeof(control._BsdInfo), 136)
        self.assertEqual(control._BsdInfo.start_sec.offset, 120)

    def test_mac_signal_rechecks_identity(self):
        with patch.object(control, '_mac_identity', return_value=(200, 20)), patch.object(control.os, 'kill') as kill:
            with self.assertRaises(control.ProcessCloseError):
                control._mac_signal(12345, (100, 20), 15)
            kill.assert_not_called()

    def test_mac_signal_tolerates_exit(self):
        with patch.object(control, '_mac_identity', return_value=None), patch.object(control.os, 'kill') as kill:
            control._mac_signal(12345, (100, 20), 15)
            kill.assert_not_called()
