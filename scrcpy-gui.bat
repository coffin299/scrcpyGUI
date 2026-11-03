@echo off
setlocal
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%~dp0"

where pyw >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" pyw -3 -m gui
  exit /b 0
)

where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" pythonw -m gui
  exit /b 0
)

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 -m gui
  if errorlevel 1 pause
  exit /b %ERRORLEVEL%
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python -m gui
  if errorlevel 1 pause
  exit /b %ERRORLEVEL%
)

echo Python 3 が見つかりません。python.org の Python 3 を入れ、py ランチャーを有効にしてください。
pause
exit /b 1
