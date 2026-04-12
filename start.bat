@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul

echo ========================================
echo Station Monitor Platform - Startup
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [Error] Python 3.8+ is required.
    pause
    exit /b 1
)

tasklist /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq Station Monitor*" 2>nul | find /I "python.exe" >nul
if not errorlevel 1 (
    echo [Warn] The application may already be running.
    echo.
)

cd /d "%~dp0"
call :load_env_file

if "%DATABASE_URL%"=="" if not exist "station_monitor.db" (
    echo [Warn] station_monitor.db was not found.
    echo [Hint] Run python init_db.py first if this is a new setup.
    echo.
)

echo [Start] Launching Flask application...
echo [URL] http://localhost:5000
echo.

start "Station Monitor" python app.py

echo [Done] Started in a new window.
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
