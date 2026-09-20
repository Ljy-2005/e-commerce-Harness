@echo off
REM pre-commit (Windows cmd 回退版) —— 阻止敏感内容进入提交
REM 由 scripts/setup_hooks.py 安装到 .git\hooks\
REM 逃生舱：git commit --no-verify

setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0..\.."

if not exist "scripts\check_secrets.py" exit /b 0

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python scripts\check_secrets.py
) else (
    where python3 >nul 2>nul
    if %ERRORLEVEL%==0 (
        python3 scripts\check_secrets.py
    ) else (
        echo pre-commit: 未找到 python，跳过敏感内容扫描 1>&2
        exit /b 0
    )
)

if %ERRORLEVEL% neq 0 (
    echo. 1>&2
    echo 提交已被阻止。确认无误后可用：git commit --no-verify 1>&2
)

exit /b %ERRORLEVEL%
