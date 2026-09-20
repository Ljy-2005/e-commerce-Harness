@echo off
REM pre-push (Windows cmd 回退版) —— 推送前最后一道本地防线
REM 由 scripts/setup_hooks.py 安装到 .git\hooks\
REM 逃生舱：git push --no-verify

setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0..\.."

if not exist "scripts\check_secrets.py" exit /b 0

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python scripts\check_secrets.py --untracked
) else (
    where python3 >nul 2>nul
    if %ERRORLEVEL%==0 (
        python3 scripts\check_secrets.py --untracked
    ) else (
        echo pre-push: 未找到 python，跳过敏感内容扫描 1>&2
        exit /b 0
    )
)

if %ERRORLEVEL% neq 0 (
    echo. 1>&2
    echo ============================================================ 1>&2
    echo  推送已被阻止。 1>&2
    echo. 1>&2
    echo  注意：一旦推送到 GitHub，即使马上删除，内容仍会留在 1>&2
    echo  提交历史与 GitHub 的缓存中，必须按「已泄漏」处理—— 1>&2
    echo  去服务商控制台轮换该密钥。 1>&2
    echo. 1>&2
    echo  确认无害可用：git push --no-verify 1>&2
    echo ============================================================ 1>&2
)

exit /b %ERRORLEVEL%
