$ErrorActionPreference = 'Stop'

# Windows-native SubagentStart meter for Codex LOOP F2.  This is observation
# only and therefore fail-open: every path exits zero and raw hook input is
# never copied into the output stream.
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$dataDir = if ([string]::IsNullOrWhiteSpace($env:LOOP_DATA_DIR)) {
    Join-Path $projectRoot 'data'
}
else {
    [IO.Path]::GetFullPath($env:LOOP_DATA_DIR)
}
$outFile = Join-Path $dataDir 'events.ndjson'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Get-PayloadValue {
    param(
        [object]$Payload,
        [string[]]$Names,
        [object]$Default = $null
    )

    foreach ($name in $Names) {
        $property = $Payload.PSObject.Properties[$name]
        if ($null -ne $property -and $null -ne $property.Value -and "$($property.Value)" -ne '') {
            return $property.Value
        }
    }
    return $Default
}

function Write-MeterRecord {
    param([hashtable]$Record)

    [IO.Directory]::CreateDirectory($dataDir) | Out-Null
    # The project ledger is LF-only even when the hook runs on Windows; CRLF
    # would make every runtime record fail `git diff --check` as trailing CR.
    $line = ($Record | ConvertTo-Json -Compress -Depth 6) + "`n"

    # Hooks may start concurrently.  Serialize each one-line append so records
    # cannot interleave and corrupt NDJSON.
    $mutex = New-Object Threading.Mutex($false, 'CodexLoopF2SubagentStartMeter')
    $locked = $false
    try {
        $locked = $mutex.WaitOne([TimeSpan]::FromSeconds(10))
        if ($locked) {
            [IO.File]::AppendAllText($outFile, $line, $utf8NoBom)
        }
    }
    finally {
        if ($locked) {
            $mutex.ReleaseMutex()
        }
        $mutex.Dispose()
    }
}

try {
    $raw = [Console]::In.ReadToEnd()
    $payload = if ([string]::IsNullOrWhiteSpace($raw)) {
        [pscustomobject]@{}
    }
    else {
        $raw | ConvertFrom-Json
    }

    $record = [ordered]@{
        event = 'SubagentStart'
        ts_utc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
        model = Get-PayloadValue $payload @('model', 'agent_model') 'unknown'
        cwd = Get-PayloadValue $payload @('cwd') 'unknown'
        agent_role = Get-PayloadValue $payload @('agent_type', 'agent_role') 'unknown'
        agent_id = Get-PayloadValue $payload @('agent_id', 'thread_id')
        turn_id = Get-PayloadValue $payload @('turn_id')
        session_id = Get-PayloadValue $payload @('session_id', 'parent_thread_id')
        permission_mode = Get-PayloadValue $payload @('permission_mode', 'sandbox_mode')
        source = 'codex_hook_windows_native'
        degraded = $false
    }
    Write-MeterRecord $record
}
catch {
    try {
        Write-MeterRecord ([ordered]@{
            event = 'SubagentStart'
            ts_utc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
            model = 'unknown'
            cwd = 'unknown'
            agent_role = 'unknown'
            source = 'codex_hook_windows_native'
            degraded = $true
        })
    }
    catch {
        # Metering must never block a subagent spawn.
    }
}

exit 0
