#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != Darwin ]]; then
    echo 'Build the macOS app on macOS.' >&2
    exit 1
fi
python_bin="${1:-python3}"
"$python_bin" -m unittest discover -s tests -q
"$python_bin" -m PyInstaller --noconfirm --clean --onedir --windowed \
    --name AIConversationCleaner --osx-bundle-identifier com.xing-skyline.ai-conversation-cleaner \
    --add-data 'web:web' run.py
echo 'Built: dist/AIConversationCleaner.app'
