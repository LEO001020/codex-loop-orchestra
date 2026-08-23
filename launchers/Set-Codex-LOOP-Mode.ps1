# Set-Codex-LOOP-Mode.ps1 - Activate / Deactivate / Restore / Status LOOP global mode
# Usage: .\launchers\Set-Codex-LOOP-Mode.ps1 -Mode Activate [-LoopRoot <path>] [-CodexHome <path>]
#
# Parameters:
#   -Mode        Required. One of: Activate, Deactivate, Status, Restore
#   -LoopRoot    Optional. Path to the codex-loop-open-source root directory.
#                Defaults to parent of this script directory (launchers parent).
#   -CodexHome   Optional. Override Codex home. Defaults to CODEX_HOME, then $USERPROFILE\.codex

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Activate', 'Deactivate', 'Status', 'Restore')]
    [string]$Mode,

    [string]$LoopRoot = '',
    [string]$CodexHome = ''
)

$ErrorActionPreference = 'Stop'

if (-not $LoopRoot) {
    $LoopRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
$LoopRoot = [IO.Path]::GetFullPath($LoopRoot)

if (-not $CodexHome) {
    if ($env:CODEX_HOME) {
        $CodexHome = $env:CODEX_HOME
    } else {
        $CodexHome = Join-Path $env:USERPROFILE '.codex'
    }
}

$tool = Join-Path $LoopRoot 'harness\global_desktop_mode.py'
$configInstaller = Join-Path $LoopRoot 'harness\install_user_config.py'

if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    throw "global_desktop_mode.py not found at: $tool`nSet -LoopRoot to the codex-loop-open-source directory."
}
if (-not (Test-Path -LiteralPath $configInstaller -PathType Leaf)) {
    throw "install_user_config.py not found at: $configInstaller"
}

$venvPython = Join-Path $LoopRoot 'venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $python = $venvPython; $pythonArgs = @()
} elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
    $python = (Get-Command py.exe).Source; $pythonArgs = @('-3')
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    $python = (Get-Command python3).Source; $pythonArgs = @()
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = (Get-Command python).Source; $pythonArgs = @()
} else {
    throw 'Python 3.11+ is required but not found. Install from https://python.org'
}

$action = switch ($Mode) {
    'Activate'   { 'activate' }
    'Deactivate' { 'deactivate' }
    'Restore'    { 'restore' }
    default      { 'status' }
}

if ($Mode -eq 'Activate') {
    & $python @pythonArgs $configInstaller --root $LoopRoot --codex-home $CodexHome
    if ($LASTEXITCODE -ne 0) {
        throw "Codex LOOP user configuration install failed (exit $LASTEXITCODE)"
    }
}

& $python @pythonArgs $tool $action --root $LoopRoot --codex-home $CodexHome
if ($LASTEXITCODE -ne 0) {
    throw "Codex LOOP global mode action failed (exit $LASTEXITCODE): $Mode"
}

if ($Mode -eq 'Restore') {
    & $python @pythonArgs $configInstaller restore --root $LoopRoot --codex-home $CodexHome
    if ($LASTEXITCODE -ne 0) {
        throw "Codex LOOP user configuration restore failed (exit $LASTEXITCODE)"
    }
}

switch ($Mode) {
    'Activate' {
        Write-Host 'LOOP mode active. Managed hooks, context payload, active agreement, and spawn gate are installed.'
        Write-Host 'Every newly started or resumed Desktop task inherits LOOP orchestration regardless of project directory.'
        Write-Host 'Restart Codex Desktop for the runtime canary to take effect.'
    }
    'Deactivate' {
        Write-Host 'LOOP mode is inactive. New or resumed Desktop tasks will not inherit LOOP orchestration.'
    }
    'Restore' {
        Write-Host 'Pre-install global files and unchanged LOOP-managed agent/config files were restored.'
        Write-Host 'Files modified by the user after installation are preserved and reported as skipped.'
    }
}
