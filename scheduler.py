#!/usr/bin/env python3
"""定时调度器

本地运行：python scheduler.py
GitHub Actions：配合 .github/workflows/weekly.yml
"""

import time
from datetime import datetime
from rich.console import Console

import schedule
from main import run

console = Console()


def job():
    console.print(f"\n[bold]⏰ 定时任务触发 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/bold]")
    try:
        run()
    except Exception as e:
        console.print_exception()


def main():
    console.print("[bold cyan]定时调度器已启动[/bold cyan]")
    console.print("默认每周一早上 8:00 (北京时间) 执行")
    console.print("按 Ctrl+C 退出\n")

    # 每周一 UTC 0:00 = 北京时间 8:00
    schedule.every().monday.at("00:00").do(job)

    # 也支持立即运行一次（测试用）
    # job()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
