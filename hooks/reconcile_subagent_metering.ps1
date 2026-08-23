$ErrorActionPreference = 'Stop'

# Stop-hook wrapper for the F2 rollout-metadata second truth source.  It is
# fail-open and processes only this exact project root.
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$expectedRoot = [IO.Path]::GetFullPath($projectRoot)
$codexHome = if ([string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
    Join-Path $env:USERPROFILE '.codex'
} else {
    $env:CODEX_HOME
}
$sessions = Join-Path $codexHome 'sessions'
$output = Join-Path $projectRoot 'data\events.ndjson'
$reconciler = Join-Path $PSScriptRoot 'reconcile_subagent_metering.py'
$meterBridge = Join-Path $projectRoot 'metering\model_token_share_bridge.py'
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$python = if ($null -eq $pythonCommand) { $null } else { $pythonCommand.Source }

function Complete-Hook {
    # Stop hooks must emit valid JSON when they exit 0.  Keep the response
    # advisory and invisible while allowing metering to remain fail-open.
    # Codex CLI 0.147.0 accepts an empty JSON object for an advisory Stop
    # hook.  Avoid newer optional fields so this package remains compatible
    # with the pinned runtime while still satisfying the JSON-only contract.
    [Console]::Out.WriteLine('{}')
    exit 0
}

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) {
        Complete-Hook
    }
    $payload = $raw | ConvertFrom-Json
    if ($payload.hook_event_name -ne 'Stop') {
        Complete-Hook
    }
    if ([string]::IsNullOrWhiteSpace("$($payload.session_id)")) {
        Complete-Hook
    }
    if ([IO.Path]::GetFullPath("$($payload.cwd)") -ne $expectedRoot) {
        Complete-Hook
    }
    if ([string]::IsNullOrWhiteSpace($python) -or
            -not (Test-Path -LiteralPath $python) -or
            -not (Test-Path -LiteralPath $reconciler)) {
        Complete-Hook
    }

    $mutex = New-Object Threading.Mutex($false, 'CodexLoopF2RolloutMeterReconcile')
    $locked = $false
    try {
        $locked = $mutex.WaitOne([TimeSpan]::FromSeconds(20))
        if ($locked) {
            & $python $reconciler `
                --sessions $sessions `
                --output $output `
                --root-session-id "$($payload.session_id)" `
                --expected-cwd $expectedRoot | Out-Null
            if (Test-Path -LiteralPath $meterBridge) {
                & $python $meterBridge `
                    --root $expectedRoot `
                    --sessions-dir $sessions | Out-Null
            }
        }
    }
    finally {
        if ($locked) {
            $mutex.ReleaseMutex()
        }
        $mutex.Dispose()
    }
}
catch {
    # Reconciliation is evidence collection and must never block a turn.
}

Complete-Hook
