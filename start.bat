@echo off
setlocal

cd /d "%~dp0"

set "VENV_DIR=.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

echo [Setup] Initializing virtual environment...
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv "%VENV_DIR%"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [Error] Python 3 was not found. Install Python 3.10 or later and try again.
        pause
        exit /b 1
    )
    python -m venv "%VENV_DIR%"
)

if errorlevel 1 (
    echo [Error] Failed to initialize the virtual environment.
    pause
    exit /b 1
)

echo [Setup] Installing dependencies...
"%VENV_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :setup_failed

"%VENV_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto :setup_failed

echo [Server] Starting in a separate window at http://127.0.0.1:8000
start "AVE Server" cmd /c ""%VENV_PYTHON%" -m uvicorn app.main:app --reload"
if errorlevel 1 (
    echo [Error] Failed to start the server.
    pause
    exit /b 1
)

exit /b 0

:setup_failed
echo [Error] Dependency installation failed.
pause
exit /b 1
