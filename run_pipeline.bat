@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 周刊管道 - CI增量模式
echo ========================================
echo   游戏设备资讯周刊 - CI 增量模式
echo   git pull + 浏览器采集 + 处理 + 生成
echo ========================================
echo.
echo [1/3] 拉取 CI 最新数据...
git pull
echo.
echo [2/3] 启动管道 (跳过 RSS/Google News, 仅浏览器采集)...
python main.py --from-ci
echo.
echo ========================================
echo   管道完成!
echo.
echo   下一步: 双击 run_review.bat 审核过滤条目
echo   或直接发布 output/ 下的周刊文件
echo ========================================
pause
