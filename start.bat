@echo off
setlocal
cd /d "%~dp0"

set "SYSTEM_PY=%LocalAppData%\Programs\Python\Python312\python.exe"
set "SYSTEM_PY_CFG=executable = %SYSTEM_PY%"

if exist ".venv\Scripts\python.exe" (
  findstr /C:"%SYSTEM_PY_CFG%" ".venv\pyvenv.cfg" >nul 2>nul
  if errorlevel 1 goto CREATE_VENV
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>nul
  if errorlevel 1 goto CREATE_VENV
  ".venv\Scripts\python.exe" switch_codex_account.py
  exit /b %errorlevel%
)

:CREATE_VENV
if exist "%SYSTEM_PY%" (
  "%SYSTEM_PY%" -m venv --clear .venv
  if errorlevel 1 exit /b %errorlevel%
  ".venv\Scripts\python.exe" switch_codex_account.py
  exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
  python -m venv --clear .venv
  if errorlevel 1 exit /b %errorlevel%
  ".venv\Scripts\python.exe" switch_codex_account.py
  exit /b %errorlevel%
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m venv --clear .venv
  if errorlevel 1 exit /b %errorlevel%
  ".venv\Scripts\python.exe" switch_codex_account.py
  exit /b %errorlevel%
)

echo Python was not found. Install Python 3 or update SYSTEM_PY in start.bat.
pause
exit /b 1
