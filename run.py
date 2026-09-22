import sys
import os
import subprocess
import traceback

from cleaner.server import main

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if sys.stderr:
            traceback.print_exc()
        elif os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), "AI 会话清理器启动失败", 0x10)
        elif sys.platform == 'darwin':
            subprocess.run(['/usr/bin/osascript', '-e',
                            'on run argv\n display alert "AI 会话清理器启动失败" message (item 1 of argv) as critical\nend run',
                            str(error)], timeout=120)
        sys.exit(1)
