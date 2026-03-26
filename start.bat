@echo off
:: start.bat - Launch Flask in background (no console window)
cd /d "%~dp0"

echo Checking port 5000...
netstat -ano | findstr ":5000" | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo Port 5000 is in use, stopping old processes...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
        taskkill /F /PID %%a >nul 2>&1
    )
    timeout /t 2 >nul
)

echo Starting Flask in background...
start "" pythonw.exe app.py

echo Waiting...
timeout /t 2 >nul

netstat -ano | findstr ":5000" | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo [OK] Flask started at http://localhost:5000
) else (
    echo [ERROR] Failed to start, check flask.log
)
