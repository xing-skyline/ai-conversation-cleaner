param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $Python -m unittest discover -s tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name 'AI会话清理器' --distpath . --add-data 'web;web' run.py
    if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
    Get-Item -LiteralPath (Join-Path $PSScriptRoot 'AI会话清理器.exe') | Select-Object FullName,Length
}
finally { Pop-Location }
