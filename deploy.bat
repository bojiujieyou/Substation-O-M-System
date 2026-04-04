@echo off
if "%~1"=="" (
    echo 用法: deploy.bat "提交说明"
    exit /b 1
)
git add .
git commit -m "%~1"
git push nas design-review-fixes:refs/heads/main
