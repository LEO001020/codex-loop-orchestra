# Start-Codex-LOOP-Desktop.ps1 - Activate LOOP and open a target workspace in Codex Desktop
# Usage: .\launchers\Start-Codex-LOOP-Desktop.ps1 [-TargetWorkspace <path>] [-LoopRoot <path>] [-NoLaunch]
#
# Parameters:
#   -TargetWorkspace  Path to the git repo you want to open. Default: current directory.
#   -LoopRoot         Path to the codex-loop-open-source root. Defaults to parent of launchers/.
#   -NoLaunch         Activate LOOP mode only; do not open Codex Desktop.

[CmdletBinding()]
param(
    [string]$TargetWorkspace = '',
    [string]$LoopRoot = '',
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'

if (-not $TargetWorkspace) { $TargetWorkspace = $PWD.Path }
if (-not $LoopRoot) {
    $LoopRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
$LoopRoot = [IO.Path]::GetFullPath($LoopRoot)
$TargetWorkspace = [IO.Path]::GetFullPath($TargetWorkspace)

if (-not (Test-Path -LiteralPath $TargetWorkspace -PathType Container)) {
    throw "Target workspace does not exist: $TargetWorkspace"
}

$modeLauncher    = Join-Path $LoopRoot 'launchers\Set-Codex-LOOP-Mode.ps1'
$monitorLauncher = Join-Path $LoopRoot 'launchers\Start-Codex-LOOP-Monitor.ps1'

& $modeLauncher -Mode Activate -LoopRoot $LoopRoot

if (Test-Path -LiteralPath $monitorLauncher -PathType Leaf) {
    Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @(
        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', $monitorLauncher,
        '-LoopRoot', $LoopRoot
    ) -WorkingDirectory $LoopRoot
}

if (-not $NoLaunch) {
    $uri = 'codex://new?path=' + [Uri]::EscapeDataString($TargetWorkspace)
    Start-Process $uri
    Write-Host "Codex LOOP opened target workspace: $TargetWorkspace"
}