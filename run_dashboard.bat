@echo off
cd /d "%~dp0"

echo Starting Gaming News floating widget...
echo.

echo Pulling latest CI data...
git pull 2>&1 | findstr /V "Already up to date" 2>nul

echo Starting widget (semi-transparent, always on top)...
start /min python floating_widget.py --run

echo.
echo Widget should be visible on your desktop (top-right corner).
echo Right-click the widget for: Open Dashboard / Refresh / Close
echo.
timeout /t 3 >nul
