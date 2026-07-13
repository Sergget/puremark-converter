@echo off
setlocal enabledelayedexpansion

:: ============================================================================
:: Win11 OCR Service Installation Script
:: Purpose: Install and configure NSSM service for OCR server
:: Requirements: Administrator privileges, NSSM, Python 3.x
:: ============================================================================

:: Set UTF-8 encoding for console and Python output
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"

:: Color codes for output (requires color-enabled console)
set "INFO=[INFO]"
set "SUCCESS=[OK]"
set "ERROR=[ERROR]"
set "WARN=[WARN]"

:: Define variables for maintainability
set "SERVICE_NAME=Win11OCRService"
set "PROJECT_ROOT=%~dp0"
set "PYTHON_EXE=%PROJECT_ROOT%.venv\Scripts\python.exe"
set "APP_SCRIPT=ocr_server.py"
set "LOG_DIR=%PROJECT_ROOT:~0,-1%\log"
set "STDOUT_LOG=%LOG_DIR%\nssm_stdout.log"
set "STDERR_LOG=%LOG_DIR%\nssm_stderr.log"

:: Service configuration
set "DISPLAY_NAME=Win11 OCR Service (PaddleOCR CPU)"
set "DESCRIPTION=Win11 OCR Node - PaddleOCR CPU, port 5000"

:: Environment variables
set "NODE_NAME=win11"
set "NODE_ROLE=heavy"
set "PORT=5000"
set "OCR_MAX_FILE_MB=100"
set "OCR_MAX_PDF_PAGES=200"
set "OCR_TIMEOUT_SEC=300"
set "OCR_PDF_DPI=200"

echo %INFO% Starting OCR Service installation...
echo.

:: Check for administrator privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo %ERROR% This script requires Administrator privileges.
    echo %WARN% Please run this batch file as Administrator.
    exit /b 1
)
echo %SUCCESS% Administrator privileges confirmed.

:: Validate Python executable exists
if not exist "%PYTHON_EXE%" (
    echo %ERROR% Python executable not found at: %PYTHON_EXE%
    exit /b 1
)
echo %SUCCESS% Python executable found.

:: Validate project directory exists
if not exist "%PROJECT_ROOT%ocr_server.py" (
    echo %ERROR% Application script not found at: %PROJECT_ROOT%%APP_SCRIPT%
    exit /b 1
)
echo %SUCCESS% Application script found.

:: Create log directory if it doesn't exist
if not exist "%LOG_DIR%" (
    echo %INFO% Creating log directory: %LOG_DIR%
    mkdir "%LOG_DIR%"
    if !errorlevel! neq 0 (
        echo %WARN% Failed to create log directory, continuing...
    )
)

:: Check if service already exists
nssm query "%SERVICE_NAME%" >nul 2>&1
if %errorlevel% equ 0 (
    echo %WARN% Service "%SERVICE_NAME%" already exists.
    echo %INFO% Stopping existing service...
    nssm stop "%SERVICE_NAME%" >nul 2>&1
    if !errorlevel! neq 0 (
        echo %WARN% Failed to stop service, continuing with removal...
    )
    echo %INFO% Removing existing service...
    nssm remove "%SERVICE_NAME%" confirm
    if !errorlevel! neq 0 (
        echo %ERROR% Failed to remove existing service.
        exit /b 1
    )
    echo %SUCCESS% Existing service removed.
)

:: Install the service
echo %INFO% Installing service "%SERVICE_NAME%"...
nssm install "%SERVICE_NAME%" "%PYTHON_EXE%" "%APP_SCRIPT%"
if %errorlevel% neq 0 (
    echo %ERROR% Failed to install service.
    exit /b 1
)
echo %SUCCESS% Service installed.

:: Configure service settings
echo %INFO% Configuring service settings...

nssm set "%SERVICE_NAME%" AppDirectory "%PROJECT_ROOT%"
nssm set "%SERVICE_NAME%" AppEnvironmentExtra NODE_NAME="!NODE_NAME!" NODE_ROLE="!NODE_ROLE!" PORT="!PORT!" OCR_MAX_FILE_MB="!OCR_MAX_FILE_MB!" OCR_MAX_PDF_PAGES="!OCR_MAX_PDF_PAGES!" OCR_TIMEOUT_SEC="!OCR_TIMEOUT_SEC!" OCR_PDF_DPI="!OCR_PDF_DPI!" PYTHONIOENCODING="utf-8" PYTHONUNBUFFERED="1" LANG="zh_CN.UTF-8"
nssm set "%SERVICE_NAME%" Start SERVICE_AUTO_START
nssm set "%SERVICE_NAME%" AppExit Default Restart
nssm set "%SERVICE_NAME%" AppStdout "%STDOUT_LOG%"
nssm set "%SERVICE_NAME%" AppStderr "%STDERR_LOG%"
nssm set "%SERVICE_NAME%" AppStdoutCreationDisposition 2
nssm set "%SERVICE_NAME%" AppStderrCreationDisposition 2
nssm set "%SERVICE_NAME%" AppRotateFiles 1
nssm set "%SERVICE_NAME%" AppRotateSeconds 3600
nssm set "%SERVICE_NAME%" AppRotateOnline 1
nssm set "%SERVICE_NAME%" AppRotateBytes 10485760
nssm set "%SERVICE_NAME%" DisplayName "%DISPLAY_NAME%"
nssm set "%SERVICE_NAME%" Description "%DESCRIPTION%"

if %errorlevel% neq 0 (
    echo %ERROR% Failed to configure service settings.
    exit /b 1
)
echo %SUCCESS% Service configured successfully.

:: Start the service
echo %INFO% Starting service "%SERVICE_NAME%"...
nssm start "%SERVICE_NAME%"
if %errorlevel% neq 0 (
    echo %ERROR% Failed to start service.
    exit /b 1
)
echo %SUCCESS% Service started successfully.

echo.
echo ============================================================================
echo %SUCCESS% OCR Service installation completed successfully!
echo ============================================================================
echo Service Name: %SERVICE_NAME%
echo Display Name: %DISPLAY_NAME%
echo Python Exe: %PYTHON_EXE%
echo App Directory: %PROJECT_ROOT%
echo Log Output: %STDOUT_LOG%
echo Log Errors: %STDERR_LOG%
echo Port: %PORT%
echo Encoding: UTF-8 (Python output will use UTF-8 encoding)
echo ============================================================================
echo.
echo To manage the service, use:
echo   nssm start "%SERVICE_NAME%"   - Start the service
echo   nssm stop "%SERVICE_NAME%"    - Stop the service
echo   nssm restart "%SERVICE_NAME%" - Restart the service
echo   nssm status "%SERVICE_NAME%"  - Check service status
echo   nssm edit "%SERVICE_NAME%"    - Edit service configuration
echo   nssm remove "%SERVICE_NAME%"  - Remove the service
echo.

exit /b 0