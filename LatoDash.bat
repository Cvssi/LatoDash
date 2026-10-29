@echo off
setlocal
title LatoDash
cd /d "%~dp0"

rem ---- find Python 3.11+
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo Python 3.11 or newer is needed. Install it with:
  echo     winget install Python.Python.3.12
  echo then run this file again.
  pause
  exit /b 1
)

rem ---- private environment, created once
if not exist ".venv\Scripts\python.exe" (
  echo First run: creating a private Python environment in .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (echo Could not create .venv & pause & exit /b 1)
)

rem ---- (re)install packages only when requirements.txt changed
fc /b requirements.txt .venv\requirements.installed >nul 2>&1
if errorlevel 1 (
  echo Installing packages ...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
  if errorlevel 1 (echo Package install failed - see the messages above. & pause & exit /b 1)
  copy /y requirements.txt .venv\requirements.installed >nul
)

echo Starting LatoDash on http://127.0.0.1:8765  (close this window to stop it)
".venv\Scripts\python.exe" -m latodash %*
if errorlevel 1 pause
