@echo off
:: stop.bat - Stop all Python Flask processes
cd /d "%~dp0"

echo [INFO] Stopping Flask app...
taskkill /F /IM python.exe >nul 2>&1

echo.
echo [OK] All Python processes stopped
pause
