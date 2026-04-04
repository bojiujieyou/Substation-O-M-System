@echo off
chcp 65001 >nul
echo ========================================
echo 变电站图像监控运维平台 - 停止脚本
echo ========================================
echo.

REM 查找并关闭 Flask 应用窗口
echo [停止] 正在查找运行中的应用...

REM 方法1: 通过窗口标题关闭
taskkill /FI "WINDOWTITLE eq 变电站监控*" /F >nul 2>&1
if not errorlevel 1 (
    echo [完成] 已通过窗口标题停止应用
    goto :done
)

REM 方法2: 通过端口查找进程
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5000 ^| findstr LISTENING') do (
    set PID=%%a
    goto :kill_pid
)

echo [信息] 未找到运行在 5000 端口的进程
goto :done

:kill_pid
if defined PID (
    echo [停止] 正在终止进程 PID: %PID%
    taskkill /PID %PID% /F >nul 2>&1
    if not errorlevel 1 (
        echo [完成] 应用已停止
    ) else (
        echo [错误] 无法停止进程，可能需要管理员权限
    )
) else (
    echo [信息] 未找到运行中的应用
)

:done
echo.
pause
