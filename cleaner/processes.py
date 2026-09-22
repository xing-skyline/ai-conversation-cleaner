from __future__ import annotations

import ctypes
import os
import json
import subprocess
from ctypes import wintypes

NODE_PATTERNS={
    'claude':r'(?i)(@anthropic-ai[\\/]claude-code|[\\/]claude-code[\\/]|[\\/]claude(?:\.m?js)?(?:["\s]|$))',
    'grok':r'(?i)(@xai[\\/]grok|[\\/]grok(?:-build|-cli)?[\\/]|[\\/]grok(?:-build)?(?:\.m?js)?(?:["\s]|$))',
    'deepseek':r'(?i)(@deepseek-ai[\\/]dsh|deepseek-harness|[\\/]dsh[\\/]|[\\/]dsh(?:\.m?js)?(?:["\s]|$)|\.dsh[\\/]profiles)',
}


def app_processes(names: set[str]) -> list[dict]:
    """Read Windows processes without invoking a shell or terminating anything."""
    if os.name != "nt":
        raise RuntimeError("当前版本的进程保护仅支持 Windows。")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError("无法检查应用进程；本次停止删除。")
    entry = Entry()
    entry.dwSize = ctypes.sizeof(entry)
    found = []
    try:
        ok = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            name = entry.szExeFile
            # ChatGPT.exe is also the Codex desktop package process name.
            if name.lower() in names:
                found.append({"pid": entry.th32ProcessID, "name": name})
            ok = kernel.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    return found


def codex_processes():
    return app_processes({"codex.exe", "chatgpt.exe", "codex-app.exe"})


def cli_processes(app,names):
    """Check native executables and known Node/Bun package launch paths."""
    found=app_processes(names)
    if app not in NODE_PATTERNS:return found
    command=r'''$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process -Filter "Name='node.exe' OR Name='bun.exe'" | Where-Object { $_.CommandLine -match $env:AI_CLEANER_PROCESS_PATTERN } | ForEach-Object { @{pid=$_.ProcessId;name=$_.Name+' ('+$env:AI_CLEANER_PROCESS_LABEL+')'} }) | ConvertTo-Json -Compress'''
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
                          env={**os.environ,'AI_CLEANER_PROCESS_PATTERN':NODE_PATTERNS[app],'AI_CLEANER_PROCESS_LABEL':app},
                          capture_output=True,text=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise RuntimeError('无法核验 '+app+' 的 Node/Bun 进程，请稍后重试。')
    values=json.loads(result.stdout) if result.stdout.strip() else []
    found.extend(values if isinstance(values,list) else [values])
    return found


def dsh_processes():
    return cli_processes('deepseek',{'dsh desktop.exe','dsh.exe','deepseek-harness.exe'})
