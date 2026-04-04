@echo off
chcp 65001 >nul
echo ========================================
echo 变电站图像监控运维平台 - 启动脚本
echo ========================================
echo.

REM 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

REM 检查是否已经在运行
tasklist /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq 变电站监控*" 2>nul | find /I "python.exe" >nul
if not errorlevel 1 (
    echo [警告] 应用可能已在运行
    echo.
)

REM 切换到项目目录
cd /d "%~dp0"

REM 检查数据库文件
if not exist "station_monitor.db" (
    echo [警告] 数据库文件不存在，首次运行请先执行 init_db.py
    echo.
)

REM 启动 Flask 应用
echo [启动] 正在启动 Flask 应用...
echo [信息] 访问地址: http://localhost:5000
echo [信息] 按 Ctrl+C 停止服务
echo.
echo ========================================
echo.

start "变电站监控运维平台" python app.py

echo [完成] 应用已在新窗口启动
echo [提示] 关闭窗口或使用 stop.bat 停止服务
echo.
pause
