[CmdletBinding()]
param(
    [ValidateSet('install', 'uninstall', 'start', 'stop', 'restart', 'status', 'check')]
    [string]$Action = 'install',

    [string]$ServiceName = 'Win11OCRService',
    [string]$DisplayName = 'Win11 OCR Service (PaddleOCR CPU)',
    [string]$Description = 'Win11 OCR Node - PaddleOCR CPU, port 5000',
    [string]$PythonExe,
    [string]$AppScript = 'ocr_server.py',
    [string]$Port = '5000',
    [switch]$NoStart
)

<#!
.SYNOPSIS
Windows OCR service deployment and management script.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\deploy_windows.ps1
# Default: install or reinstall the NSSM service.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\deploy_windows.ps1 -Action status
# Check the current service status.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\deploy_windows.ps1 -Action uninstall
# Remove the NSSM service.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\deploy_windows.ps1 -Action check
# Validate paths, Python interpreter, and NSSM availability without changing the service.
#>

$ErrorActionPreference = 'Stop'

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message"
}

function Write-Success {
    param([string]$Message)
    Write-Host "[OK] $Message"
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message"
}

function Test-ServiceExists {
    param([string]$Name)

    & nssm query $Name 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ocrRoot = Join-Path $repoRoot 'ocr_server'

if (-not $PythonExe) {
    $PythonExe = Join-Path $repoRoot '.venv\Scripts\python.exe'
}

if (-not [System.IO.Path]::IsPathRooted($PythonExe)) {
    $PythonExe = Join-Path $repoRoot $PythonExe
}

$appScriptFull = if ([System.IO.Path]::IsPathRooted($AppScript)) {
    $AppScript
}
else {
    Join-Path $ocrRoot $AppScript
}

$logDir = Join-Path $repoRoot 'log'
$stdoutLog = Join-Path $logDir 'nssm_stdout.log'
$stderrLog = Join-Path $logDir 'nssm_stderr.log'

if (-not (Test-Path $ocrRoot)) {
    throw "OCR server directory not found: $ocrRoot"
}

if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}

if (-not (Test-Path $appScriptFull)) {
    throw "Application script not found: $appScriptFull"
}

$command = Get-Command nssm -ErrorAction SilentlyContinue
if (-not $command) {
    throw 'NSSM was not found in PATH. Please install NSSM first and ensure nssm.exe is available.'
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

Write-Info "Action: $Action"
Write-Info "Project root: $repoRoot"
Write-Info "OCR root: $ocrRoot"
Write-Info "Python executable: $PythonExe"
Write-Info "Service name: $ServiceName"

switch ($Action) {
    'check' {
        Write-Success 'Pre-check passed. Paths and NSSM are available.'
        if (Test-ServiceExists -Name $ServiceName) {
            Write-Info "Service '$ServiceName' is installed."
        }
        else {
            Write-Warn "Service '$ServiceName' is not installed yet."
        }
        return
    }

    'status' {
        if (-not (Test-ServiceExists -Name $ServiceName)) {
            Write-Warn "Service '$ServiceName' is not installed."
            return
        }

        & nssm status $ServiceName
        return
    }

    'start' {
        if (-not (Test-ServiceExists -Name $ServiceName)) {
            throw "Service '$ServiceName' does not exist."
        }

        Write-Info "Starting service '$ServiceName'..."
        & nssm start $ServiceName
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to start service '$ServiceName'."
        }
        Write-Success "Service '$ServiceName' started."
        return
    }

    'stop' {
        if (-not (Test-ServiceExists -Name $ServiceName)) {
            throw "Service '$ServiceName' does not exist."
        }

        Write-Info "Stopping service '$ServiceName'..."
        & nssm stop $ServiceName 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to stop service '$ServiceName'."
        }
        Write-Success "Service '$ServiceName' stopped."
        return
    }

    'restart' {
        if (-not (Test-ServiceExists -Name $ServiceName)) {
            throw "Service '$ServiceName' does not exist."
        }

        Write-Info "Restarting service '$ServiceName'..."
        & nssm restart $ServiceName 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to restart service '$ServiceName'."
        }
        Write-Success "Service '$ServiceName' restarted."
        return
    }

    'uninstall' {
        if (Test-ServiceExists -Name $ServiceName) {
            Write-Warn "Removing existing service '$ServiceName'..."
            & nssm stop $ServiceName 2>$null | Out-Null
            & nssm remove $ServiceName confirm 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to remove service '$ServiceName'."
            }
        }
        else {
            Write-Warn "Service '$ServiceName' is not installed."
        }

        Write-Success "Service '$ServiceName' removed."
        return
    }
}

Write-Info 'Starting Windows OCR service deployment...'

if (Test-ServiceExists -Name $ServiceName) {
    Write-Warn "Service '$ServiceName' already exists. Reinstalling it..."
    & nssm stop $ServiceName 2>$null | Out-Null
    & nssm remove $ServiceName confirm 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to remove existing service '$ServiceName'."
    }
}

Write-Info "Installing service '$ServiceName'..."
& nssm install $ServiceName $PythonExe $appScriptFull
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install service '$ServiceName'."
}

$envExtra = @(
    "NODE_NAME=win11",
    'NODE_ROLE=heavy',
    "PORT=$Port",
    'OCR_MAX_FILE_MB=100',
    'OCR_MAX_PDF_PAGES=200',
    'OCR_TIMEOUT_SEC=300',
    'OCR_PDF_DPI=200',
    'PYTHONIOENCODING=utf-8',
    'PYTHONUNBUFFERED=1',
    'LANG=zh_CN.UTF-8'
) -join ' '

Write-Info 'Configuring service settings...'
& nssm set $ServiceName AppDirectory $ocrRoot
& nssm set $ServiceName AppEnvironmentExtra $envExtra
& nssm set $ServiceName Start SERVICE_AUTO_START
& nssm set $ServiceName AppExit Default Restart
& nssm set $ServiceName AppStdout $stdoutLog
& nssm set $ServiceName AppStderr $stderrLog
& nssm set $ServiceName AppStdoutCreationDisposition 2
& nssm set $ServiceName AppStderrCreationDisposition 2
& nssm set $ServiceName AppRotateFiles 1
& nssm set $ServiceName AppRotateSeconds 3600
& nssm set $ServiceName AppRotateOnline 1
& nssm set $ServiceName AppRotateBytes 10485760
& nssm set $ServiceName DisplayName $DisplayName
& nssm set $ServiceName Description $Description

if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure service '$ServiceName'."
}

if (-not $NoStart) {
    Write-Info "Starting service '$ServiceName'..."
    & nssm start $ServiceName
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to start service '$ServiceName'."
    }
}
else {
    Write-Warn 'Skipping automatic start because -NoStart was supplied.'
}

Write-Success 'OCR service deployment completed successfully.'
Write-Host ''
Write-Host 'Service details:'
Write-Host "  Name: $ServiceName"
Write-Host "  Display Name: $DisplayName"
Write-Host "  Python: $PythonExe"
Write-Host "  App Directory: $ocrRoot"
Write-Host "  Stdout Log: $stdoutLog"
Write-Host "  Stderr Log: $stderrLog"
Write-Host '  Encoding: UTF-8'
Write-Host ''
Write-Host 'Useful commands:'
Write-Host "  nssm start \"$ServiceName\""
Write-Host "  nssm stop \"$ServiceName\""
Write-Host "  nssm restart \"$ServiceName\""
Write-Host "  nssm status \"$ServiceName\""
Write-Host "  nssm edit \"$ServiceName\""
Write-Host "  nssm remove \"$ServiceName\""
