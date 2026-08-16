@echo off
title Ad Ops Command Center
cd /d "%~dp0"

REM Already running? Just open the browser and exit.
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try{$c.Connect('127.0.0.1',7480); $c.Close(); exit 0}catch{exit 1}" >nul 2>&1
if not errorlevel 1 (
    echo Ad Ops is already running - opening browser...
    start "" http://localhost:7480
    exit /b
)

if not exist ".deps_installed" (
    echo Installing dependencies...
    python -m pip install -r requirements.txt && echo ok > .deps_installed
)
if not exist "data\adops.db" (
    echo Seeding database from vault...
    python -m seed.seed_vault
)

REM Open the browser only once the server is actually accepting connections.
start "" /min powershell -NoProfile -WindowStyle Hidden -Command ^
 "$end=[DateTime]::Now.AddSeconds(90); while([DateTime]::Now -lt $end){ try{ $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',7480); $c.Close(); Start-Process 'http://localhost:7480'; break }catch{ Start-Sleep -Milliseconds 300 } }"

echo Starting Ad Ops Command Center on http://localhost:7480
echo Close this window (or press Ctrl+C) to stop the server.
python -m uvicorn app.main:app --port 7480
