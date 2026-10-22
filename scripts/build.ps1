param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath $project
try {
    & $Python -B scripts/collect_licenses.py
    if ($LASTEXITCODE -ne 0) { throw '许可文本收集失败' }
    & $Python -B -m unittest discover -s tests -q
    if ($LASTEXITCODE -ne 0) { throw '测试未通过，停止构建' }
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $project 'build\pyinstaller-cache'
    & $Python -m PyInstaller --clean --noconfirm --workpath build/pyinstaller --distpath dist PrintBridge.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller构建失败' }
    Get-FileHash -LiteralPath 'dist\PrintBridge.exe' -Algorithm SHA256
} finally {
    Pop-Location
}
