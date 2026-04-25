@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul

echo ========================================
echo Station Monitor Platform - Hidden Startup
echo ========================================
echo.

set "PY_CMD="
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py -3"
) else (
    python --version >nul 2>&1
    if not errorlevel 1 (
        set "PY_CMD=python"
    )
)

if "%PY_CMD%"=="" (
    echo [Error] Python 3.8+ is required.
    pause
    exit /b 1
)

for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr :5000 ^| findstr LISTENING') do (
    echo [Warn] The application may already be running on port 5000.
    echo [PID] %%a
    echo.
    pause
    exit /b 0
)

cd /d "%~dp0"
call :load_env_file

if "%DATABASE_URL%"=="" if not exist "station_monitor.db" (
    echo [Warn] station_monitor.db was not found.
    echo [Hint] Run python init_db.py first if this is a new setup.
    echo.
    pause
    exit /b 1
)

%PY_CMD% -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [Error] Flask is not available in the selected Python environment: %PY_CMD%
    echo [Hint] Install dependencies first or use the Python environment that has Flask installed.
    pause
    exit /b 1
)

echo [Start] Launching Flask application in background...
echo [Python] %PY_CMD%
echo [URL] http://localhost:5000
echo [Logs] flask.log / flask.err.log
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'cd /d "%CD%" && %PY_CMD% app.py 1^> "%CD%\flask.log" 2^> "%CD%\flask.err.log"' -WindowStyle Hidden"

if errorlevel 1 (
    echo [Error] Failed to start the application in hidden mode.
    pause
    exit /b 1
)

echo [Done] Started in background with hidden window.
echo.
pause
goto :eof

:load_env_file
if not exist ".env" (
    echo [Info] .env not found, using system environment.
    goto :eof
)

echo [Info] Loaded .env configuration.
for /f "usebackq tokens=* delims=" %%L in (".env") do (
    set "line=%%L"
    if defined line (
        if not "!line:~0,1!"=="#" (
            for /f "tokens=1* delims==" %%A in ("!line!") do (
                if not "%%A"=="" (
                    set "%%A=%%B"
                )
            )
        )
    )
)
goto :eof
