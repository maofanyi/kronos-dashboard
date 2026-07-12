$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing dashboard virtualenv Python: $python"
}

if (-not $env:KRONOS_DATA_DIR) {
    $tradeData = Join-Path $root "..\Kronos-pm-trade-bot-trade-node\data\checkpoints"
    if (Test-Path -LiteralPath $tradeData) {
        $env:KRONOS_DATA_DIR = (Resolve-Path $tradeData).Path
    }
}
$env:DASHBOARD_HOST = if ($env:DASHBOARD_HOST) { $env:DASHBOARD_HOST } else { "0.0.0.0" }
$env:DASHBOARD_PORT = if ($env:DASHBOARD_PORT) { $env:DASHBOARD_PORT } else { "8090" }

$logDir = Join-Path $root "data\logs"
$runtimeDir = Join-Path $root "data\runtime"
New-Item -ItemType Directory -Force -Path $logDir, $runtimeDir | Out-Null

function Rotate-Log {
    param([string]$Path, [long]$MaxBytes = 10MB)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $file = Get-Item -LiteralPath $Path
    if ($file.Length -lt $MaxBytes) { return }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Move-Item -LiteralPath $Path -Destination "$Path.$stamp" -Force
}

function Test-PidRunning {
    param([string]$PidPath)
    if (-not (Test-Path -LiteralPath $PidPath)) { return $false }
    $rawPid = (Get-Content -Raw -LiteralPath $PidPath).Trim()
    if ($rawPid -notmatch "^\d+$") { return $false }
    return $null -ne (Get-Process -Id ([int]$rawPid) -ErrorAction SilentlyContinue)
}

function Start-LoggedDashboardProcess {
    param(
        [string]$Name,
        [string]$ScriptPath
    )
    $pidPath = Join-Path $runtimeDir "$Name.pid"
    if (Test-PidRunning $pidPath) {
        return
    }
    $logPath = Join-Path $logDir "$Name.log"
    Rotate-Log $logPath
    $command = "Set-Location -LiteralPath '$($root.Replace("'", "''"))'; & '$($python.Replace("'", "''"))' '$($ScriptPath.Replace("'", "''"))' *>> '$($logPath.Replace("'", "''"))'"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $process = Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-NonInteractive", "-EncodedCommand", $encoded) `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ascii
}

Start-LoggedDashboardProcess -Name "dashboard-sync" -ScriptPath "sync_service\main.py"
Start-Sleep -Seconds 2
Start-LoggedDashboardProcess -Name "dashboard-api" -ScriptPath "api\server.py"
