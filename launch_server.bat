@echo off
REM ==========================================================
REM  Launch Streamlit + expose it via Cloudflare Tunnel
REM  Share the printed https:// URL with anyone.
REM ==========================================================
setlocal
title Oracle to GCP - server (Cloudflare Tunnel)
cd /d "%~dp0"

echo ============================================================
echo   Oracle to GCP - Streamlit + Cloudflare Tunnel
echo   Public https:// URL will print below. Share it to expose
echo   this app. Ctrl+C to stop the tunnel.
echo ============================================================
echo.

set PORT=8501

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found.
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

where cloudflared >nul 2>&1
if errorlevel 1 (
    echo [ERROR] cloudflared is not installed.
    echo   winget install Cloudflare.cloudflared
    echo   -- or --
    echo   https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
    pause
    exit /b 1
)

echo [1/2] Starting Streamlit on port %PORT% ...
start "oracle-to-gcp-streamlit" /B ".venv\Scripts\python.exe" -m streamlit run "app\app.py" ^
    --server.port %PORT% ^
    --server.headless true ^
    --browser.gatherUsageStats=false

timeout /t 3 /nobreak >nul

echo [2/2] Opening Cloudflare Tunnel ...
echo.
echo   Share the https:// URL printed below with anyone.
echo   Press Ctrl+C to stop the tunnel, then close this window.
echo.
cloudflared tunnel --url http://localhost:%PORT% 2>&1 | findstr /V /C:"Cannot determine default origin certificate path"

taskkill /fi "windowtitle eq oracle-to-gcp-streamlit" /f >nul 2>&1
echo.
echo Server stopped.
pause
