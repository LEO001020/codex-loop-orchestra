# Start-Codex-LOOP-Monitor.ps1 - Start the LOOP headless concurrency dashboard (port 8765)
# Usage: .\launchers\Start-Codex-LOOP-Monitor.ps1 [-LoopRoot <path>] [-Port <int>]

#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$LoopRoot = '',
    [string]$CodexHome = '',
    [int]$Port = 8765
)
$ErrorActionPreference = 'Stop'

if (-not $LoopRoot) {
    $LoopRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
$LoopRoot = [IO.Path]::GetFullPath($LoopRoot)
$serverScript = Join-Path $LoopRoot 'launchers\loop_monitor_server.py'

if (-not (Test-Path -LiteralPath $serverScript -PathType Leaf)) {
    Write-Warning "loop_monitor_server.py not found at: $serverScript"
    exit 0
}

if (-not $CodexHome) {
    if ($env:CODEX_HOME) { $CodexHome = $env:CODEX_HOME }
    else { $CodexHome = Join-Path $env:USERPROFILE '.codex' }
}
$sessionsRoot = Join-Path $CodexHome 'sessions'
$logFile   = Join-Path $env:TEMP "codex-loop-monitor-$Port.log"
$mutexName = "Local\Codex-LOOP-Monitor-${Port}-Start"
$mutex     = [System.Threading.Mutex]::new($false, $mutexName)
$hasMutex  = $false

try {
    $hasMutex = $mutex.WaitOne(0)
    if (-not $hasMutex) {
        Write-Host "Monitor already running on port $Port."
        exit 0
    }

    # Locate Python
    $python = $null
    $pythonArgs = @()
    $venvPython = Join-Path $LoopRoot 'venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $python = $venvPython
    } elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $python = (Get-Command py.exe).Source; $pythonArgs = @('-3')
    } elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
        $python = (Get-Command python3).Source
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $python = (Get-Command python).Source
    } else {
        Write-Warning 'Python not found; monitor not started.'
        exit 1
    }

    Write-Host "Starting Codex LOOP monitor on http://127.0.0.1:${Port}/ ..."
    & $python @pythonArgs $serverScript --root $LoopRoot --sessions-root $sessionsRoot --port $Port 2>&1 | Tee-Object -FilePath $logFile
} finally {
    if ($hasMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
