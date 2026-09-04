@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo [ERROR] venv not found.
  echo Run once:
  echo   python -m venv venv
  echo   venv\Scripts\python.exe -m pip install -r requirements.txt -r ui\requirements.txt
  pause
  exit /b 1
)

powershell -NoProfile -Command "try { Invoke-WebRequest http://127.0.0.1:11434/api/tags -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
  where ollama >nul 2>&1
  if not errorlevel 1 (
    echo Starting Ollama...
    start "Ollama" /min ollama serve
  ) else (
    echo Ollama not in PATH - UI will work without LLM.
  )
) else (
  echo Ollama already running.
)

powershell -NoProfile -Command "try { $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',8000); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
  echo Starting backend :8000 ...
  start "Excel Agent Backend" /D "%~dp0backend" cmd /k "..\venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000"
) else (
  echo Backend already running: http://127.0.0.1:8000
)

echo Waiting for backend...
set /a _n=0
:wait_backend
powershell -NoProfile -Command "try { $r=Invoke-WebRequest http://127.0.0.1:8000/ -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto backend_ready
set /a _n+=1
if %_n% geq 30 (
  echo [WARN] Backend did not become ready in 30s. Starting UI anyway.
  goto start_ui
)
timeout /t 1 /nobreak >nul
goto wait_backend

:backend_ready
echo Backend ready: http://127.0.0.1:8000

:start_ui
powershell -NoProfile -Command "try { $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',8501); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
  echo Starting UI :8501 ...
  start "Excel Agent UI" /D "%~dp0" cmd /k "venv\Scripts\python.exe -m streamlit run ui\app.py --server.port 8501 --server.address 127.0.0.1"
) else (
  echo UI already running: http://127.0.0.1:8501
  start http://127.0.0.1:8501
)

echo.
echo UI:  http://127.0.0.1:8501
echo API: http://127.0.0.1:8000
echo Leave the Backend and UI windows open.
timeout /t 2 >nul
