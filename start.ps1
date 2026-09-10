# ASCII only: Windows PowerShell 5.1 misparses UTF-8 Cyrillic without BOM.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "[ERROR] venv not found. Run once:"
    Write-Host "  python -m venv venv"
    Write-Host "  venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

function Test-LocalPort([int]$Port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", $Port)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

function Test-Ollama {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (Test-Ollama) {
    Write-Host "Ollama already running."
} elseif (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Host "Starting Ollama..."
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Minimized
} else {
    Write-Host "Ollama not in PATH - UI will work without LLM."
}

if (Test-LocalPort 8000) {
    Write-Host "Backend already running: http://127.0.0.1:8000"
} else {
    Write-Host "Starting backend :8000 ..."
    Start-Process -FilePath $Python -ArgumentList @(
        "-m", "uvicorn", "app:app",
        "--host", "127.0.0.1", "--port", "8000"
    ) -WorkingDirectory (Join-Path $Root "backend")
}

Write-Host "Waiting for backend..."
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/" -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}
if ($ready) {
    Write-Host "Backend ready: http://127.0.0.1:8000"
} else {
    Write-Host "[WARN] Backend did not become ready in 30s. Starting UI anyway."
}

$Npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $Npm) {
    Write-Host "[ERROR] npm not found. Install Node.js 20+, then:"
    Write-Host "  cd web"
    Write-Host "  npm install"
    exit 1
}
$Web = Join-Path $Root "web"
if (-not (Test-Path (Join-Path $Web "node_modules"))) {
    Write-Host "Installing UI dependencies..."
    Push-Location $Web
    npm install
    Pop-Location
}

if (Test-LocalPort 8501) {
    Write-Host "UI already running: http://127.0.0.1:8501"
} else {
    Write-Host "Starting UI :8501 ..."
    Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", "npm run dev") -WorkingDirectory $Web
}

Write-Host "Waiting for UI..."
$uiReady = $false
for ($i = 0; $i -lt 30; $i++) {
    if (Test-LocalPort 8501) {
        $uiReady = $true
        break
    }
    Start-Sleep -Seconds 1
}
if ($uiReady) {
    Write-Host "UI ready: http://127.0.0.1:8501"
    Start-Process "http://127.0.0.1:8501"
} else {
    Write-Host "[WARN] UI did not become ready in 30s. Open http://127.0.0.1:8501"
}

Write-Host ""
Write-Host "UI:  http://127.0.0.1:8501"
Write-Host "API: http://127.0.0.1:8000"
Write-Host "Leave the Backend and UI windows open."
