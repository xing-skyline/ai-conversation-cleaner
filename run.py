import sys
import traceback

from cleaner.server import main

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if sys.stdout:
            traceback.print_exc()
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), "AI 会话清理器启动失败", 0x10)
        sys.exit(1)
