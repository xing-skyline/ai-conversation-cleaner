#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
# Finder has a minimal PATH; prefer the project environment, then common Python installs.
for python_bin in .venv/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if "$python_bin" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        exec "$python_bin" run.py "$@"
    fi
done
echo '需要 Python 3.11 或更高版本。也可直接使用打包的 AIConversationCleaner.app。' >&2
read -r -p '按回车键关闭…'
exit 1
