@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto run

rem Find Python: try "python", then the "py" launcher.
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
    echo Could not find Python. Reinstall it from python.org and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

echo Creating virtual environment with %PY% ...
%PY% -m venv .venv
if errorlevel 1 (
    echo Failed to create the virtual environment.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Package install failed. See the messages above.
    pause
    exit /b 1
)

:run
".venv\Scripts\python.exe" graph.py %*
pause
