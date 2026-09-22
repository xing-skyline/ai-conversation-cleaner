"""Package an explicit public allowlist and dependency licenses, not a worktree."""
import hashlib
import importlib.metadata
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def main():
    executable=ROOT/'AI会话清理器.exe'
    if not executable.is_file():raise SystemExit('Build the executable first.')
    output=ROOT/'release';output.mkdir(exist_ok=True)
    archive=output/'AIConversationCleaner-Windows-x64.zip'
    with tempfile.TemporaryDirectory(prefix='ai-cleaner-public-package-') as directory:
        stage=Path(directory)
        shutil.copy2(executable,stage/'AIConversationCleaner.exe')
        for name in ['README.md','README.zh-CN.md','SECURITY.md','THIRD_PARTY_NOTICES.md','CHANGELOG.md']:
            shutil.copy2(ROOT/name,stage/name)
        if (ROOT/'LICENSE').is_file():shutil.copy2(ROOT/'LICENSE',stage/'LICENSE')
        licenses=stage/'licenses';licenses.mkdir()
        python_license=Path(sys.base_prefix)/'LICENSE.txt'
        if not python_license.is_file():raise SystemExit('Python runtime license not found; refusing to package.')
        shutil.copy2(python_license,licenses/'Python-LICENSE.txt')
        distribution=importlib.metadata.distribution('pyinstaller')
        copying=next(p for p in distribution.files if str(p).endswith('/licenses/COPYING.txt'))
        shutil.copy2(distribution.locate_file(copying),licenses/'PyInstaller-COPYING.txt')
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as stream:
            for path in sorted(stage.rglob('*')):
                if path.is_file():stream.write(path,path.relative_to(stage).as_posix())
    checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/'SHA256SUMS.txt').write_text(f'{checksum}  {archive.name}\n',encoding='utf-8')
    print('Packaged public archive and SHA256SUMS.txt (explicit allowlist only).')


if __name__=='__main__':main()
