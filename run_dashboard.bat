@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 周刊仪表盘
echo ========================================
echo   游戏设备资讯周刊 - 统一仪表盘
echo   git pull + 管道 + 审核 + 回捞
echo ========================================
echo.
echo [1/2] 拉取 CI 最新数据...
git pull
echo.
echo [2/2] 启动仪表盘 + 管道...
start python dashboard.py --run
echo.
echo 浏览器将自动打开仪表盘页面
echo 仪表盘会显示实时进度，完成后可直接审核回捞
echo.
timeout /t 3 >nul
