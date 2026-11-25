@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."
echo [1] Web API 模式
echo [2] 桌面 GUI 模式
set /p choice=请选择: 
if "%choice%"=="2" (
  python run.py gui
) else (
  python run.py api
)
pause
