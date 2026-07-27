@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 过滤条目可视化审核
echo ========================================
echo   过滤条目可视化审核
echo   浏览器打开后打勾 → Save and Close
echo ========================================
echo.
start python review_filtered.py
echo.
echo 审核完成后, 双击 run_recover.bat 回捞重生成
echo 或者直接关闭此窗口
pause
