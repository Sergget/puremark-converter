[CmdletBinding()]
param(
    [ValidateSet("install", "uninstall", "start", "stop", "restart", "status", "check", "schedule")] # Added 'schedule' action
    [string]$Action = "install",

    [string]$ServiceName = "Win11OCRService",
    [string]$DisplayName = "Win11 OCR Service (PaddleOCR CPU)",
    [string]$Description = "Win11 OCR Node - PaddleOCR CPU, port 5000",
    [string]$PythonExe,
    [string]$AppScript = "ocr_server.py",
    [string]$Port = "5000",             # OCR服务监听端口，默认5000
    [string]$NodeName = "win11",         # 节点名称，用于日志和服务标识，默认win11
    [string]$NodeRole = "heavy",         # 节点角色，例如heavy、light，默认heavy
    [string]$OcrMaxFileMb = "100",       # OCR处理最大文件大小（MB），默认100
    [string]$OcrMaxPdfPages = "200",    # OCR处理PDF最大页数，默认200
    [string]$OcrTimeoutSec = "300",      # 单次OCR处理超时时间（秒），默认300
    [string]$OcrPdfDpi = "200",          # PDF渲染DPI，影响OCR精度和速度，默认200
    [switch]$NoStart                    # 安装服务后不立即启动，默认启动
)

<#!
.SYNOPSIS
Windows OCR 服务部署和管理脚本。

.DESCRIPTION
本脚本使用 NSSM (Non-Sucking Service Manager) 将 OCR 服务注册为 Windows 系统服务，并支持对服务状态的查询、启动、停止、卸载以及注册开机自启计划任务。

.PARAMETER Action
要执行的操作，可选值包括：
- install   : 安装或重新安装 NSSM Windows 服务 (默认值)
- uninstall : 卸载并删除该 Windows 服务
- start     : 启动服务
- stop      : 停止服务
- restart   : 重启服务
- status    : 查看当前服务运行状态
- check     : 预检环境（验证 Python 解释器、NSSM 等路径是否可用，不改变服务状态）
- schedule  : 创建 Windows 计划任务，以便在系统启动时（无需登录）自动启动该服务

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\deploy_windows.ps1
# 默认操作：安装或重新安装并配置 Windows OCR 服务。

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\deploy_windows.ps1 -Action status
# 查看当前服务的运行状态。

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\deploy_windows.ps1 -Action uninstall
# 卸载并移除 Windows OCR 服务。

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\deploy_windows.ps1 -Action check
# 预先检查本地路径、Python 环境和 NSSM 依赖，不修改或安装服务。

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\deploy_windows.ps1 -Action schedule
# 创建开机自启动计划任务（使用 SYSTEM 账户，在无用户登录时也可正常在后台拉起 OCR 服务）。
#>

$ErrorActionPreference = "Stop"

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

    # 检查服务是否存在，NSSM的status命令如果服务不存在会输出错误或返回非零值
    # 使用cmd /c来执行NSSM命令并捕获其错误级别
    cmd /c "nssm status $Name >nul 2>&1"
    return $LASTEXITCODE -eq 0
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ocrRoot = Join-Path $repoRoot "ocr_server"

if (-not $PythonExe) {
    $PythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
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

$logDir = Join-Path $repoRoot "log"
$stdoutLog = Join-Path $logDir "nssm_stdout.log"
$stderrLog = Join-Path $logDir "nssm_stderr.log"

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
    throw "NSSM was not found in PATH. Please install NSSM first and ensure nssm.exe is available."
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

Write-Info "Action: $Action"
Write-Info "Project root: $repoRoot"
Write-Info "OCR root: $ocrRoot"
Write-Info "Python executable: $PythonExe"
Write-Info "Service name: $ServiceName"

switch ($Action) {
    "check" {
        Write-Success "Pre-check passed. Paths and NSSM are available."
        if (Test-ServiceExists -Name $ServiceName) {
            Write-Info "Service $ServiceName is installed."
        }
        else {
            Write-Warn "Service $ServiceName is not installed yet."
        }
        return
    }

    "status" {
        if (-not (Test-ServiceExists -Name $ServiceName)) {
            Write-Warn "Service $ServiceName is not installed."
            return
        }

        & nssm status $ServiceName
        return
    }

    "start" {
        if (-not (Test-ServiceExists -Name $ServiceName)) {
            throw "Service $ServiceName does not exist."
        }

        Write-Info "Starting service $ServiceName...";
        & nssm start $ServiceName
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to start service $ServiceName."
        }
        Write-Success "Service $ServiceName started."
        return
    }

    "stop" {
        if (-not (Test-ServiceExists -Name $ServiceName)) {
            throw "Service $ServiceName does not exist."
        }

        Write-Info "Stopping service $ServiceName...";
        cmd /c "nssm stop $ServiceName >nul 2>&1"
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to stop service $ServiceName."
        }
        Write-Success "Service $ServiceName stopped."
        return
    }

    "restart" {
        if (-not (Test-ServiceExists -Name $ServiceName)) {
            throw "Service $ServiceName does not exist."
        }

        Write-Info "Restarting service $ServiceName...";
        cmd /c "nssm restart $ServiceName >nul 2>&1"
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to restart service $ServiceName."
        }
        Write-Success "Service $ServiceName restarted."
        return
    }

    "uninstall" {
        if (Test-ServiceExists -Name $ServiceName) {
            Write-Warn "Removing existing service $ServiceName...";
            cmd /c "nssm stop $ServiceName >nul 2>&1"
            cmd /c "nssm remove $ServiceName confirm >nul 2>&1"
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to remove service $ServiceName."
            }
        }
        else {
            Write-Warn "Service $ServiceName is not installed."
        }

        Write-Success "Service $ServiceName removed."
        return
    }
    "schedule" {
        Write-Info "Creating scheduled task to start service $ServiceName at startup..."

        $taskName = "$($ServiceName)Startup"
        $taskDescription = "Starts the $DisplayName service at system startup."
        $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$repoRoot\deploy_windows.ps1`" -Action start -ServiceName `"$ServiceName`""

        $taskTrigger = New-ScheduledTaskTrigger -AtStartup

        $taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable

        # Register the scheduled task
        # 使用 -User "SYSTEM" 注册任务本身就会以最高权限运行（因为是 SYSTEM 账户）
        Register-ScheduledTask -TaskName $taskName -Description $taskDescription -Action $taskAction -Trigger $taskTrigger -Settings $taskSettings -User "SYSTEM" -Force

        Write-Success "Scheduled task $taskName created successfully."
        return
    }
}

Write-Info "Starting Windows OCR service deployment..."

if (Test-ServiceExists -Name $ServiceName) {
    Write-Warn "Service $ServiceName already exists. Reinstalling it..."
    cmd /c "nssm stop $ServiceName >nul 2>&1"
    cmd /c "nssm remove $ServiceName confirm >nul 2>&1"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to remove existing service $ServiceName."
    }
}

Write-Info "Installing service $ServiceName...";
& nssm install $ServiceName $PythonExe $appScriptFull
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install service $ServiceName."
}

$envExtra = @(
    "NODE_NAME=$NodeName",
    "NODE_ROLE=$NodeRole",
    "PORT=$Port",
    "OCR_MAX_FILE_MB=$OcrMaxFileMb",
    "OCR_MAX_PDF_PAGES=$OcrMaxPdfPages",
    "OCR_TIMEOUT_SEC=$OcrTimeoutSec",
    "OCR_PDF_DPI=$OcrPdfDpi",
    "PYTHONIOENCODING=utf-8",
    "PYTHONUNBUFFERED=1",
    "LANG=zh_CN.UTF-8"
) -join " "

Write-Info "Configuring service settings..."
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
    throw "Failed to configure service $ServiceName."
}

if (-not $NoStart) {
    Write-Info "Starting service $ServiceName...";
    & nssm start $ServiceName
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to start service $ServiceName."
    }
}
else {
    Write-Warn "Skipping automatic start because -NoStart was supplied."
}

Write-Success "OCR service deployment completed successfully."
Write-Host ""
Write-Host "Service details:"
Write-Host "  Name: $ServiceName"
Write-Host "  Display Name: $DisplayName"
Write-Host "  Python: $PythonExe"
Write-Host "  App Directory: $ocrRoot"
Write-Host "  Stdout Log: $stdoutLog"
Write-Host "  Stderr Log: $stderrLog"
Write-Host "  Encoding: UTF-8"
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  nssm start `"$ServiceName`""
Write-Host "  nssm stop `"$ServiceName`""
Write-Host "  nssm restart `"$ServiceName`""
Write-Host "  nssm status `"$ServiceName`""
Write-Host "  nssm edit `"$ServiceName`""
Write-Host "  nssm remove `"$ServiceName`""
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$repoRoot\deploy_windows.ps1`" -Action schedule # New: Create scheduled task"