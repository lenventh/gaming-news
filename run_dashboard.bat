@echo off
cd /d "%~dp0"
git pull >nul 2>&1
start "" pythonw floating_widget.py --run
