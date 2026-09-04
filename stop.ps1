# ASCII only. Stops Excel Agent backend :8000 and UI :8501. Does not stop Ollama.
$ErrorActionPreference = "SilentlyContinue"

function Test-ExcelAgentProcess([string]$CommandLine) {
    if (-not $CommandLine) { return $false }
    $cmd = $CommandLine.ToLowerInvariant()
    if ($cmd -match 'uvicorn\s+app:app') { return $true }
    if ($cmd -match 'streamlit\s+run' -and $cmd -match 'ui\\app\.py|ui/app\.py') { return $true }
    return $false
}

function Stop-ProjectPort([int]$Port) {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $procId = $c.OwningProcess
        if (-not $procId) { continue }
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        $cmd = $proc.CommandLine
        if (Test-ExcelAgentProcess $cmd) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped PID $procId on port $Port"
        } else {
            Write-Host "Skip PID $procId on port $Port (not Excel Agent)"
        }
    }
}

Stop-ProjectPort 8000
Stop-ProjectPort 8501
Write-Host "Backend and UI stopped."
