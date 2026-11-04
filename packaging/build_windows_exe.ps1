# scrcpy GUI を、Python 不要の Windows exe にまとめる。
$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install pyinstaller
python -m PyInstaller `
    --noconfirm `
    --clean `
    --noupx `
    --onefile `
    --windowed `
    --name scrcpy-gui `
    --paths . `
    --collect-submodules gui `
    --add-data "doc/gui.html;doc" `
    --add-data "doc/gui.md;doc" `
    gui/__main__.py
