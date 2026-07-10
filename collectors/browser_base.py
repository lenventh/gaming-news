"""浏览器采集器基类

抽象 Playwright 浏览器采集的公共流程：环境变量控制、启动配置、预热。
子类只需实现 _scrape(page) 做站点特定采集。
"""

import os
import random
import time
from abc import abstractmethod

from rich.console import Console

from .base import BaseCollector

console = Console()

# 公共浏览器启动参数（反检测）
BROWSER_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
]

# 中文用户代理
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# 反 webdriver 检测脚本
HIDE_WEBDRIVER_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
"""


class BrowserBaseCollector(BaseCollector):
    """Playwright 浏览器采集器基类

    子类需设置:
    - env_var: 环境变量名（如 "TIEDA_BROWSER"）
    - source_name: 采集器名称
    """

    env_var: str = ""
    warmup_url: str = "https://www.bilibili.com"

    def fetch(self) -> list[dict]:
        if not self._should_run():
            console.log(f"[dim]{self.name} 已跳过 (设置 {self.env_var}=true 启用)[/dim]")
            return []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            console.log(f"[red]playwright 未安装，跳过 {self.name}[/red]")
            return []

        console.print(f"\n[yellow]{self.name} 浏览器采集:[/yellow]")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=BROWSER_LAUNCH_ARGS)
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )
            page = context.new_page()
            page.add_init_script(HIDE_WEBDRIVER_SCRIPT)

            self._warmup(page)

            # 子类实现具体采集逻辑
            all_items = self._scrape(page)

            browser.close()

        console.log(f"[green]{self.name} 总计: {len(all_items)} 条[/green]")
        return all_items

    def _should_run(self) -> bool:
        """检查环境变量是否启用"""
        if not self.env_var:
            return True
        return os.getenv(self.env_var, "").lower() in ("1", "true", "yes")

    def _warmup(self, page):
        """预热浏览器会话"""
        try:
            page.goto(self.warmup_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
        except Exception as e:
            console.log(f"[dim]{self.name} 预热失败: {e}[/dim]")

    @staticmethod
    def _sleep(min_sec: float = 1, max_sec: float = 3):
        """请求间随机延迟"""
        time.sleep(random.uniform(min_sec, max_sec))

    @abstractmethod
    def _scrape(self, page) -> list[dict]:
        """子类实现具体的页面抓取逻辑"""
        ...
