@echo off
cd /d "%~dp0"
title Station Monitor Control Panel

:menu
cls
echo ========================================================
echo      Station Monitor Control Panel
echo ========================================================
echo.

REM Check app status
set "APP_RUNNING=0"
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr :5000 ^| findstr LISTENING') do (
    set "APP_RUNNING=1"
    set "APP_PID=%%a"
)

if "%APP_RUNNING%"=="1" (
    echo [Status] Running (PID: %APP_PID%)
    echo [Access] http://localhost:5000
) else (
    echo [Status] Not Running
)
echo.
echo --------------------------------------------------------
echo.
echo  [1] Start Application
echo  [2] Stop Application
echo  [3] Restart Application
echo  [4] View Logs
echo  [5] Open Browser
echo  [0] Exit
echo.
echo --------------------------------------------------------
echo.
set /p choice="Select [0-5]: "

if "%choice%"=="1" goto :start_app
if "%choice%"=="2" goto :stop_app
if "%choice%"=="3" goto :restart_app
if "%choice%"=="4" goto :view_logs
if "%choice%"=="5" goto :open_browser
if "%choice%"=="0" goto :exit
goto :menu

:start_app
cls
echo [Start Application]
echo.
if "%APP_RUNNING%"=="1" (
    echo [Warning] Already running (PID: %APP_PID%)
    echo.
    pause
    goto :menu
)

cd /d "%~dp0"

if not exist "station_monitor.db" (
    echo [Warning] Database not found
    echo [Hint] Run: python init_db.py
    echo.
    pause
    goto :menu
)

echo [Starting] Flask application...
start "Station Monitor" python app.py
timeout /t 2 >nul
echo [Done] Application started
echo [Access] http://localhost:5000
echo.
pause
goto :menu

:stop_app
cls
echo [Stop Application]
echo.
if "%APP_RUNNING%"=="0" (
    echo [Info] Not running
    echo.
    pause
    goto :menu
)

echo [Stopping] PID: %APP_PID%
taskkill /PID %APP_PID% /F >nul 2>&1
if not errorlevel 1 (
    echo [Done] Stopped
) else (
    echo [Error] Failed to stop, trying window title...
    taskkill /FI "WINDOWTITLE eq Station Monitor*" /F >nul 2>&1
    echo [Done] Attempted to stop
)
echo.
pause
goto :menu

:restart_app
cls
echo [Restart Application]
echo.
if "%APP_RUNNING%"=="1" (
    echo [1/2] Stopping...
    taskkill /PID %APP_PID% /F >nul 2>&1
    timeout /t 2 >nul
)

cd /d "%~dp0"
echo [2/2] Starting...
start "Station Monitor" python app.py
timeout /t 2 >nul
echo [Done] Restarted
echo [Access] http://localhost:5000
echo.
pause
goto :menu

:view_logs
cls
echo [View Logs]
echo.
cd /d "%~dp0"
if exist "flask.log" (
    echo --------------------------------------------------------
    type flask.log | more
    echo --------------------------------------------------------
) else (
    echo [Info] Log file not found
)
echo.
pause
goto :menu

:open_browser
cls
echo [Open Browser]
echo.
if "%APP_RUNNING%"=="0" (
    echo [Warning] Not running, please start first
    echo.
    pause
    goto :menu
)

echo [Opening] http://localhost:5000
start http://localhost:5000
timeout /t 1 >nul
goto :menu

:exit
cls
echo.
echo Thank you!
echo.
timeout /t 1 >nul
exit
