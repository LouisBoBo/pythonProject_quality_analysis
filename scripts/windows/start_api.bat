@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."

echo ========================================
echo   PCB品质分析系统 v2.0
echo ========================================
echo.

set PORT=8765
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul
if %errorlevel% == 0 (
    echo [警告] 端口 %PORT% 已被占用，正在清理旧进程...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
        echo 终止进程: %%a
        taskkill /F /PID %%a >nul 2>&1
    )
    timeout /t 2 /nobreak >nul
)

echo 正在启动 API 服务...
python run.py api
pause
