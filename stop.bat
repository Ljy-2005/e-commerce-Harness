@echo off
rem Stop E-Commerce Harness services listening on ports 8000 / 5173
chcp 65001 >nul
echo Stopping E-Commerce Harness services (ports 8000 / 5173)...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5173 " ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
echo Done.
pause
