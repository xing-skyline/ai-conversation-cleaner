"""Package an explicit public allowlist and dependency licenses, not a worktree."""
import hashlib
import importlib.metadata
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def main():
    mac = sys.platform == 'darwin'
    executable=ROOT/'dist/AIConversationCleaner.app' if mac else ROOT/'AI会话清理器.exe'
    if not (executable.is_dir() if mac else executable.is_file()):raise SystemExit('Build the application first.')
    output=ROOT/'release';output.mkdir(exist_ok=True)
    target = 'macOS-' + platform.machine() if mac else 'Windows-x64'
    archive=output/('AIConversationCleaner-'+target+'.zip')
    with tempfile.TemporaryDirectory(prefix='ai-cleaner-public-package-') as directory:
        stage=Path(directory)
        if mac:shutil.copytree(executable,stage/executable.name,symlinks=True)
        else:shutil.copy2(executable,stage/'AIConversationCleaner.exe')
        for name in ['LICENSE','README.md','README.zh-CN.md','SECURITY.md','THIRD_PARTY_NOTICES.md','CHANGELOG.md']:
            shutil.copy2(ROOT/name,stage/name)
        licenses=stage/'licenses';licenses.mkdir()
        python_license=next((p for p in [Path(sys.base_prefix)/'LICENSE.txt',
                            Path(sys.base_prefix)/f'lib/python{sys.version_info.major}.{sys.version_info.minor}/LICENSE.txt']
                            if p.is_file()),None)
        if python_license is None:raise SystemExit('Python runtime license not found; refusing to package.')
        shutil.copy2(python_license,licenses/'Python-LICENSE.txt')
        distribution=importlib.metadata.distribution('pyinstaller')
        copying=next(p for p in distribution.files if str(p).endswith('/licenses/COPYING.txt'))
        shutil.copy2(distribution.locate_file(copying),licenses/'PyInstaller-COPYING.txt')
        if mac:
            # Preserve bundle symlinks, executable modes and code signatures.
            subprocess.run(['/usr/bin/ditto','-c','-k','--sequesterRsrc',str(stage),str(archive)],check=True)
        else:
            with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as stream:
                for path in sorted(stage.rglob('*')):
                    if path.is_file():stream.write(path,path.relative_to(stage).as_posix())
    checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/'SHA256SUMS.txt').write_text(f'{checksum}  {archive.name}\n',encoding='utf-8')
    print('Packaged public archive and SHA256SUMS.txt (explicit allowlist only).')


if __name__=='__main__':main()
