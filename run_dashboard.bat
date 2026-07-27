@echo off
cd /d "%~dp0"

echo Starting Gaming News Dashboard...
echo.

echo [1/2] Pulling latest CI data...
git pull 2>&1 | findstr /V "Already up to date" 2>nul

echo [2/2] Starting dashboard + pipeline...
start /min python dashboard.py --run

echo.
echo Dashboard: http://127.0.0.1:8766
echo Opening browser window...
start "" dashboard_startup.vbs

echo.
echo Keep this window open while dashboard is running.
echo Close this window to shut down dashboard.
echo.
pause
