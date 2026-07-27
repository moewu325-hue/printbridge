@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================
echo    PrintBridge 打印队列管理 v1.0
echo ================================
echo.
echo 启动中...
echo.

REM 使用项目目录下的虚拟环境(如有)或系统Python
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe main.py
) else (
    python main.py
)

pause
