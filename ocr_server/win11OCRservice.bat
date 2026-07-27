@echo off
setlocal

:: UTF-8-safe wrapper that hands off deployment to the final PowerShell script.
chcp 65001 >nul 2>&1

set "SCRIPT_DIR=%~dp0"
set "DEPLOY_SCRIPT=%SCRIPT_DIR%..\deploy_windows.ps1"

if not exist "%DEPLOY_SCRIPT%" (
    echo [ERROR] Deployment script not found: %DEPLOY_SCRIPT%
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%DEPLOY_SCRIPT%"
exit /b %ERRORLEVEL%