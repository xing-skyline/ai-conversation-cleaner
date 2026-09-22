"""Native Windows directory selection; no browser file-upload workaround."""
import base64
import os
import subprocess
import threading

from .store import CleanupError

_picker_lock=threading.Lock()


def choose_backup_directory(initial=''):
    if os.name!='nt':raise CleanupError('文件夹选择窗口仅支持 Windows；也可手动输入路径。')
    if not isinstance(initial,str):raise CleanupError('初始文件夹路径无效。')
    if not _picker_lock.acquire(blocking=False):raise CleanupError('文件夹选择窗口已经打开，请先完成选择。')
    script=r'''
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$picker=New-Object System.Windows.Forms.FolderBrowserDialog
$picker.Description='选择 AI 会话清理器的备份文件夹'
$picker.ShowNewFolderButton=$true
if(Test-Path -LiteralPath $env:AI_CLEANER_INITIAL_DIR -PathType Container){$picker.SelectedPath=$env:AI_CLEANER_INITIAL_DIR}
$owner=New-Object System.Windows.Forms.Form
$owner.TopMost=$true
$owner.ShowInTaskbar=$false
try {
    if($picker.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){
        [Console]::Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($picker.SelectedPath)))
    }
} finally {$picker.Dispose();$owner.Dispose()}
'''
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-STA','-EncodedCommand',
                               base64.b64encode(script.encode('utf-16-le')).decode('ascii')],
                              env={**os.environ,'AI_CLEANER_INITIAL_DIR':initial or os.environ.get('USERPROFILE','C:\\')},
                              capture_output=True,timeout=300,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:raise CleanupError('无法打开文件夹选择窗口，请直接在界面输入完整路径。')
        return base64.b64decode(result.stdout.strip()).decode('utf-8') if result.stdout.strip() else None
    except subprocess.TimeoutExpired as error:
        raise CleanupError('文件夹选择超时，请重试或直接输入路径。') from error
    finally:_picker_lock.release()
