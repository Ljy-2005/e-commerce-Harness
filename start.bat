@echo off
rem E-Commerce Harness one-click launcher (double-click to run)
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python start.py %*
echo.
echo Launcher exited (code %ERRORLEVEL%).
pause >nul
