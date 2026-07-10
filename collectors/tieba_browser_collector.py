"""贴吧浏览器采集器

用 Playwright 无头浏览器直接访问贴吧页面，抓取帖子列表。
比 Google News RSS 中转方案覆盖更全，能捕获所有日常讨论帖。

适用场景：本地开发（需要能访问贴吧的中国 IP）
已知限制：
- 贴吧改版后用虚拟滚动列表（React），需滚动触发加载
- 百度可能弹出安全验证，需要预热浏览器环境
- GitHub Actions (US IP) 可能被贴吧拦截，建议 CI 环境默认关闭
"""

import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from rich.console import Console

from config import CATEGORIES
from .base import BaseCollector

console = Console()

# ========== 贴吧列表（按覆盖优先级排序） ==========
TIEBA_BOARDS = {
    # === 开源/复古掌机（核心覆盖） ===
    "开源掌机": "linux_handheld",
    "复古掌机": "linux_handheld",
    "掌机": None,
    "游戏掌机": None,
    "寨机": "linux_handheld",
    # === 开源掌机品牌吧 ===
    "anbernic": "linux_handheld",
    "周哥掌机": "linux_handheld",
    "rg35xx": "linux_handheld",
    "rgcube": "linux_handheld",
    "rg掌机": "linux_handheld",
    "rg406": "linux_handheld",
    "miyoo": "linux_handheld",
    "trimui": "linux_handheld",
    "吹米": "linux_handheld",
    "powkiddy": "linux_handheld",
    "霸王小子": "linux_handheld",
    "泡机堂": "linux_handheld",
    # === 安卓掌机品牌吧 ===
    "retroid": "android_handheld",
    "沙雕": "android_handheld",
    "沙雕3": "android_handheld",
    "rp5": "android_handheld",
    "odin掌机": "android_handheld",
    "奥丁掌机": "android_handheld",
    "安卓掌机": "android_handheld",
    "天马前端": "android_handheld",
    "天马g": "android_handheld",
    "爱吾游戏": "android_handheld",
    "盖世小鸡": "android_handheld",
    "拉伸手柄": "android_handheld",
    # === Windows 掌机 ===
    "win掌机": "windows_handheld",
    "rogally": "windows_handheld",
    "ayaneo": "windows_handheld",
    "aya掌机": "windows_handheld",
    "gpd掌机": "windows_handheld",
    "壹号本": "windows_handheld",
    "onexplayer": "windows_handheld",
    "legiongo": "windows_handheld",
    "联想掌机": "windows_handheld",
    "msiclaw": "windows_handheld",
    "飞行家": "windows_handheld",
    # === Steam Deck ===
    "steamdeck": "steam_deck",
    "steamdeck掌机": "steam_deck",
    # === 主机/掌机 ===
    "switch2": "console",
    "ns2": "console",
    "switch": "console",
    "ps5": "console",
    "ps5pro": "console",
    "psv": "console",
    "psvita": "console",
    "3ds": "console",
    "nds": "console",
    "psp": "console",
    "nintendo": "console",
    "playstation": "console",
    "xboxone": "console",
    "xboxseriesx": "console",
    "索尼掌机": "console",
    "xbox掌机": "console",
    "任天堂新机": "console",
    # === 模拟器 ===
    "模拟器": "emulator",
    "yuzu": "emulator",
    "ryujinx": "emulator",
    "sudachi": "emulator",
    "citra": "emulator",
    "winlator": "emulator",
    "ns模拟器": "emulator",
}

# 每次采集的贴吧数量上限
MAX_BOARDS_PER_RUN = 30
# 每个吧滚动加载次数
SCROLL_TIMES = 4
# 贴吧间延迟（秒）
BOARD_DELAY_MIN = 3
BOARD_DELAY_MAX = 8


def _parse_tieba_date(date_str: str) -> Optional[datetime]:
    """解析贴吧显示的日期文字为 datetime

    贴吧格式举例：
    - "2小时前" / "30分钟前"
    - "昨天 14:30" / "昨天"
    - "07-09" (今年)
    - "2025-12-01"
    - "回复于2小时前" / "回复于07-09"
    """
    if not date_str:
        return None

    date_str = date_str.strip()
    now = datetime.now()

    try:
        # "回复于X小时前" / "回复于X分钟前"
        if "回复于" in date_str:
            date_str = date_str.split("回复于", 1)[1].strip()

        # "X小时前"
        if "小时前" in date_str:
            hours = int(date_str.replace("小时前", "").strip())
            return now - timedelta(hours=hours)

        # "X分钟前"
        if "分钟前" in date_str:
            mins = int(date_str.replace("分钟前", "").strip())
            return now - timedelta(minutes=mins)

        # "昨天 HH:MM" / "昨天"
        if "昨天" in date_str:
            result = now - timedelta(days=1)
            time_part = date_str.replace("昨天", "").strip()
            if time_part:
                try:
                    h, m = time_part.split(":")
                    result = result.replace(hour=int(h), minute=int(m))
                except ValueError:
                    pass
            return result

        # "前天"
        if "前天" in date_str:
            return now - timedelta(days=2)

        # "MM-DD" (今年)
        if len(date_str) == 5 and date_str[2] == "-":
            month, day = date_str.split("-")
            return datetime(now.year, int(month), int(day))

        # "MM-DD HH:MM"
        if len(date_str) >= 11 and date_str[2] == "-":
            parts = date_str.split(" ")
            month, day = parts[0].split("-")
            result = datetime(now.year, int(month), int(day))
            if len(parts) >= 2:
                try:
                    h, m = parts[1].split(":")
                    result = result.replace(hour=int(h), minute=int(m))
                except ValueError:
                    pass
            return result

        # "YYYY-MM-DD"
        try:
            return datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            pass

        # "YYYY-MM-DD HH:MM"
        try:
            return datetime.strptime(date_str[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            pass

    except Exception:
        pass

    return None


class TiebaBrowserCollector(BaseCollector):
    """用 Playwright 浏览器抓取贴吧帖子（适配新版贴吧 React 页面）"""

    def __init__(self, boards: Optional[dict[str, Optional[str]]] = None):
        super().__init__("TiebaBrowser")
        self._boards = boards or TIEBA_BOARDS
        self._seen_tids: set[str] = set()

    def _warmup_browser(self, page) -> bool:
        """预热浏览器环境，建立百度贴吧的 cookie 会话，降低触发验证的概率"""
        try:
            # 先访问百度首页
            page.goto("https://www.baidu.com", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1500)

            # 再访问贴吧首页
            page.goto("https://tieba.baidu.com", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)

            return True
        except Exception as e:
            console.log(f"[dim]浏览器预热失败: {e}[/dim]")
            return False

    def _extract_board_posts(self, page, board_name: str) -> list[dict]:
        """从已打开的贴吧页面提取帖子列表（新版 React 页面 + 虚拟滚动）"""
        # 等待帖子卡片出现
        try:
            page.wait_for_selector(".thread-card", timeout=10000)
        except Exception:
            title = page.title()
            if "验证" in title or "安全" in title:
                console.log(f"[yellow]贴吧 [{board_name}]: 触发安全验证，跳过[/yellow]")
            else:
                console.log(f"[dim]贴吧 [{board_name}]: 无帖子卡片[/dim]")
            return []

        # 滚动加载更多帖子（虚拟列表渲染有限数量）
        for i in range(SCROLL_TIMES):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(random.randint(800, 1500))

        # 回到顶部，确保所有卡片都在 DOM 中
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)

        # JS 提取帖子数据（新版 DOM 结构）
        posts_data = page.evaluate(r"""
            () => {
                const threads = [];
                const seenTids = new Set();
                document.querySelectorAll('.thread-card').forEach(card => {
                    try {
                        const titleLink = card.querySelector('a[href*="/p/"]');
                        if (!titleLink) return;

                        const href = titleLink.getAttribute('href');
                        const tidMatch = href.match(/\/p\/(\d+)/);
                        if (!tidMatch) return;
                        const tid = tidMatch[1];
                        if (seenTids.has(tid)) return;
                        seenTids.add(tid);

                        const titleEl = card.querySelector('[class*="title"]');
                        const title = titleEl
                            ? titleEl.textContent.trim()
                            : (titleLink.getAttribute('title') || titleLink.textContent.trim());

                        const fullText = card.textContent.trim();

                        threads.push({
                            tid: tid,
                            title: title.substring(0, 150),
                            url: titleLink.href,
                            fullText: fullText.substring(0, 500),
                        });
                    } catch(e) {}
                });
                return threads;
            }
        """)

        if not posts_data:
            return []

        results = []
        for post in posts_data:
            title = post.get("title", "").strip()
            tid = post.get("tid")
            if not title or not tid:
                continue

            full_text = post.get("fullText", "")

            # 从全文提取日期（"回复于XX" 模式）
            date_match = re.search(r"回复于(\S+?)(?:\s|$)", full_text)
            date_raw = date_match.group(1) if date_match else ""
            published_at = _parse_tieba_date(date_raw) if date_raw else None

            # 从全文提取作者名（开头到第一个连续空格之前）
            author = full_text.split("  ")[0].strip() if "  " in full_text else ""

            # 提取回复/分享数（全文末尾的数字对）
            nums = re.findall(r"(\d+)", full_text.split("分享")[-1] if "分享" in full_text else "")
            reply_num = int(nums[1]) if len(nums) >= 2 else (int(nums[0]) if len(nums) == 1 else 0)

            results.append({
                "title": title,
                "url": post.get("url") or f"https://tieba.baidu.com/p/{tid}",
                "published_at": published_at,
                "summary": full_text[:300],
                "raw": {
                    "tid": tid,
                    "author": author,
                    "reply_num": reply_num,
                    "board": board_name,
                },
            })

        return results

    def fetch(self) -> list[dict]:
        """用浏览器访问贴吧页面，抓取帖子列表"""
        # 环境变量控制是否启用（CI 默认关闭）
        if os.getenv("TIEDA_BROWSER", "").lower() not in ("1", "true", "yes"):
            console.log("[dim]贴吧浏览器采集已跳过 (设置 TIEDA_BROWSER=true 启用)[/dim]")
            return []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            console.log("[red]playwright 未安装，跳过浏览器采集[/red]")
            console.log("[dim]安装: pip install playwright && playwright install chromium[/dim]")
            return []

        all_items = []
        boards_to_fetch = dict(list(self._boards.items())[:MAX_BOARDS_PER_RUN])

        console.print(f"\n[yellow]贴吧浏览器采集: {len(boards_to_fetch)} 个吧[/yellow]")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )
            page = context.new_page()
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
            """)

            # 预热浏览器环境
            self._warmup_browser(page)

            for board_name, cat_hint in boards_to_fetch.items():
                try:
                    url = f"https://tieba.baidu.com/f?kw={board_name}"
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)

                    posts = self._extract_board_posts(page, board_name)

                    new_count = 0
                    for post in posts:
                        tid = post.get("raw", {}).get("tid", "")
                        if tid in self._seen_tids:
                            continue
                        self._seen_tids.add(tid)

                        item = self.normalize_item(
                            title=post["title"],
                            url=post["url"],
                            source_name=f"贴吧{board_name}吧",
                            source_type="tieba_browser",
                            published_at=post.get("published_at"),
                            summary=post.get("summary", ""),
                            raw_data=post.get("raw", {}),
                        )
                        if cat_hint:
                            item["category"] = cat_hint
                        all_items.append(item)
                        new_count += 1

                    if new_count > 0:
                        console.log(f"[green]贴吧 [{board_name}]: {new_count} 帖[/green]")
                    else:
                        console.log(f"[dim]贴吧 [{board_name}]: 0 帖[/dim]")

                except Exception as e:
                    console.log(f"[red]贴吧 [{board_name}] 失败: {e}[/red]")

                # 贴吧之间随机延迟
                delay = random.uniform(BOARD_DELAY_MIN, BOARD_DELAY_MAX)
                time.sleep(delay)

            browser.close()

        console.log(f"[green]贴吧浏览器总计: {len(all_items)} 条[/green]")
        return all_items
