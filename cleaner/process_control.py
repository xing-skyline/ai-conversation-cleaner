"""Opt-in process shutdown, restricted to identities captured by a preview.

Windows uses a process handle and creation time (GetProcessTimes). macOS uses
libproc's PROC_PIDTBSDINFO, including microsecond creation time:
https://github.com/apple/darwin-xnu/blob/main/bsd/sys/proc_info.h
No process groups, terminal hosts, or unrecognized descendants are terminated.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import errno
import os
import signal
import sys
import time

from .processes import mac_process_table, windows_process_table


class ProcessCloseError(RuntimeError):
    pass


def protected_pids():
    if os.name == 'nt':
        parents = {row['pid']: row['ppid'] for row in windows_process_table()}
    elif sys.platform == 'darwin':
        parents = {pid: int(parent) for pid, parent in mac_process_table('ppid').items()}
    else:
        raise ProcessCloseError('自动关闭仅支持 Windows 和 macOS。')
    protected = {0, 1}
    pid = os.getpid()
    while pid not in protected:
        protected.add(pid)
        pid = parents.get(pid, 0)
    return protected


def _windows_process(pid, expected=None, close=False):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, plus termination only
    # when the user has opted in. Hold this handle through the final wait.
    handle = kernel.OpenProcess(0x101000 | (1 if close else 0), False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: PID exited
            return None
        raise ProcessCloseError(f'无法访问进程 PID {pid}，请手动退出应用后重新预览。')
    try:
        if kernel.WaitForSingleObject(handle, 0) == 0:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            raise ProcessCloseError(f'无法核验进程 PID {pid}，本次停止删除。')
        identity = (times[0].dwHighDateTime, times[0].dwLowDateTime)
        if expected is not None and identity != expected:
            raise ProcessCloseError('进程在预览后发生变化，请重新预览。')
        if close:
            # Give GUI applications a chance to close normally before ending
            # the exact process. CLI processes have no top-level window.
            user = ctypes.WinDLL('user32', use_last_error=True)
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
            user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            @callback_type
            def request_close(window, _):
                owner = wintypes.DWORD()
                user.GetWindowThreadProcessId(window, ctypes.byref(owner))
                if owner.value == pid:
                    user.PostMessageW(window, 0x0010, 0, 0)  # WM_CLOSE
                return True
            user.EnumWindows(request_close, 0)
            if kernel.WaitForSingleObject(handle, 2000) != 0:
                if not kernel.TerminateProcess(handle, 1) and kernel.WaitForSingleObject(handle, 0) != 0:
                    raise ProcessCloseError(f'无法结束进程 PID {pid}，请手动退出后重试。')
                if kernel.WaitForSingleObject(handle, 5000) != 0:
                    raise ProcessCloseError(f'进程 PID {pid} 尚未退出，本次停止删除。')
        return identity
    finally:
        kernel.CloseHandle(handle)


class _BsdInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid',
        'rgid', 'svuid', 'svgid', 'reserved')]
    _fields_ += [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in ('nfiles', 'pgid', 'jobc', 'tdev', 'tpgid')]
    _fields_ += [('nice', ctypes.c_int32), ('start_sec', ctypes.c_uint64), ('start_usec', ctypes.c_uint64)]


def _mac_identity(pid):
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidinfo.restype = ctypes.c_int
    info = _BsdInfo()
    ctypes.set_errno(0)
    count = lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    if count == 0 and ctypes.get_errno() == errno.ESRCH:
        return None
    if count != ctypes.sizeof(info) or info.pid != pid:
        raise ProcessCloseError(f'无法核验进程 PID {pid}，请手动退出后重新预览。')
    if info.status == 5:  # SZOMB: no longer executing or holding files open
        return None
    return (info.start_sec, info.start_usec)


def process_identity(pid):
    if os.name == 'nt':
        return _windows_process(pid)
    if sys.platform == 'darwin':
        return _mac_identity(pid)
    raise ProcessCloseError('自动关闭仅支持 Windows 和 macOS。')


def _mac_signal(pid, identity, sig):
    current = _mac_identity(pid)
    if current is None:
        return
    if current != identity:
        raise ProcessCloseError('进程在预览后发生变化，请重新预览。')
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def close_process(pid, identity):
    if os.name == 'nt':
        _windows_process(pid, expected=identity, close=True)
        return
    if sys.platform != 'darwin':
        raise ProcessCloseError('自动关闭仅支持 Windows 和 macOS。')
    _mac_signal(pid, identity, signal.SIGTERM)
    for sig, timeout in ((None, 2), (signal.SIGKILL, 5)):
        if sig is not None:
            _mac_signal(pid, identity, sig)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _mac_identity(pid) != identity:
                return
            time.sleep(.05)
    raise ProcessCloseError(f'进程 PID {pid} 尚未退出，本次停止删除。')


def capture_processes(provider):
    """Keep creation times server-side; expose only the reviewed names/PIDs."""
    rows = list({row['pid']: row for row in provider()}.values())
    tickets = []
    try:
        protected = protected_pids() if rows else set()
        for row in rows:
            if row['pid'] in protected:
                raise ProcessCloseError('对应应用启动了当前清理器，不能自动关闭。请单独启动清理器，或手动退出应用。')
            identity = process_identity(row['pid'])
            if identity is not None:
                tickets.append({'pid': row['pid'], 'name': row['name'], 'identity': identity})
        return rows, tickets, None
    except (OSError, RuntimeError, ValueError) as error:
        return rows, [], str(error)


def close_selected_processes(provider, tickets):
    """Fail before sending any signal if an unreviewed process is now present."""
    reviewed = {row['pid']: row for row in tickets}
    current = {row['pid']: row for row in provider()}
    protected = protected_pids() if current else set()
    for pid in current:
        if pid in protected:
            raise ProcessCloseError('不能自动关闭清理器自身或启动它的应用，请手动退出后重试。')
        if pid not in reviewed:
            raise ProcessCloseError('检测到预览后新启动的应用进程，请重新预览。')
        identity = process_identity(pid)
        if identity is not None and identity != reviewed[pid]['identity']:
            raise ProcessCloseError('进程在预览后发生变化，请重新预览。')
    for pid in current:
        close_process(pid, reviewed[pid]['identity'])
    if provider():
        raise ProcessCloseError('对应应用仍在运行或已重新启动，本次没有删除会话；请退出后重新预览。')
