@echo off
cd /d "%~dp0"

echo ========================================
echo   Gaming News Weekly - Dashboard
echo ========================================
echo.
echo Pulling latest CI data...
git pull
echo.
echo Starting dashboard + pipeline...
start python dashboard.py --run
echo.
echo Browser will open with live dashboard.
echo Shows: progress stepper, timer, samples, review, recover.
echo.
timeout /t 3 >nul
