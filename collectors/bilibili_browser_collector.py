"""B站浏览器采集器

用 Playwright 无头浏览器直接搜索 B站，抓取视频/图文内容。
比 Google News RSS 中转方案覆盖量大得多（实测 1051 条 vs 3 条）。

包含两个子模块：
1. 关键词搜索 — 按分类搜索 B站
2. 官号主页抓取 — 直接访问厂商 B站官号空间页，抓最新投稿+简介

适用场景：本地开发（中国 IP 无障碍访问 B站）
CI 环境默认关闭，由 BILIBILI_BROWSER=true 环境变量开启。
"""

import json
import os
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

from rich.console import Console

from config import CATEGORIES
from .base import BaseCollector

console = Console()

# ========== 搜索关键词（按分类组织） ==========
BILIBILI_SEARCH_KEYWORDS = {
    "steam_deck": [
        "Steam Deck 掌机",
        "Steam Deck 评测",
        "SteamOS 更新",
    ],
    "windows_handheld": [
        "ROG Ally 掌机",
        "AYANEO 掌机",
        "GPD Win 掌机",
        "Windows 掌机 新品",
    ],
    "android_handheld": [
        "安卓掌机",
        "Retroid Pocket",
        "Odin 掌机",
        "安卓掌机 新品",
    ],
    "linux_handheld": [
        "开源掌机",
        "Anbernic 掌机",
        "TrimUI 掌机",
        "复古掌机 新品",
    ],
    "console": [
        "Switch 2 评测",
        "PS5 Pro",
        "Xbox 掌机",
        "任天堂 新机",
    ],
    "emulator": [
        "模拟器 更新",
        "Winlator 安卓",
        "Switch 模拟器 安卓",
    ],
}

# ========== 厂商官号 B站 UID ==========
# 搜索品牌名时，B站搜索结果会自动包含官号内容
# 这里列出已知的官号 mids，用于识别和优先排序
MANUFACTURER_ACCOUNTS = {
    "AYANEO官方": {"mid": 17560816, "category": "windows_handheld"},
    "AYANEO掌机": {"mid": 366077183, "category": "windows_handheld"},
    "GPD掌机官方": {"mid": 437511465, "category": "windows_handheld"},
    "壹号本科技": {"mid": 394918220, "category": "windows_handheld"},
    "AOKZOE掌机": {"mid": 1760429024, "category": "windows_handheld"},
    "AYN掌机": {"mid": 479152595, "category": "android_handheld"},
    "Anbernic官方": {"mid": 3546587284712685, "category": "linux_handheld"},
    "TrimUI掌机": {"mid": 3546638793361253, "category": "linux_handheld"},
    "PowKiddy掌机": {"mid": 3493288993647151, "category": "linux_handheld"},
    "ROG玩家国度": {"mid": 259780464, "category": "windows_handheld"},
}

# 按分类组织的官号搜索关键词
MANUFACTURER_SEARCHES = [
    ("AYANEO", "windows_handheld"),
    ("AYANEO掌机", "windows_handheld"),
    ("GPD掌机官方", "windows_handheld"),
    ("壹号本科技 OneXPlayer", "windows_handheld"),
    ("AOKZOE掌机", "windows_handheld"),
    ("ROG Ally掌机 官方", "windows_handheld"),
    ("AYN Odin掌机", "android_handheld"),
    ("Retroid掌机 官方", "android_handheld"),
    ("Anbernic安伯尼克官方", "linux_handheld"),
    ("Miyoo掌机 官方", "linux_handheld"),
    ("TrimUI掌机 官方", "linux_handheld"),
    ("PowKiddy掌机", "linux_handheld"),
    ("霸王小子掌机", "linux_handheld"),
    ("PlayStation中国", "console"),
    ("任天堂Switch", "console"),
    ("Xbox中国", "console"),
]

# 每次采集的上限
MAX_SEARCH_PER_KEYWORD = 8
MAX_MANUFACTURER_PER_ACCOUNT = 5
SEARCH_DELAY_MIN = 2
SEARCH_DELAY_MAX = 4


def _parse_bilibili_count(count_str: str) -> int:
    """解析 B站播放量/弹幕数文字为整数"""
    if not count_str:
        return 0
    count_str = count_str.strip()
    try:
        if "万" in count_str:
            return int(float(count_str.replace("万", "")) * 10000)
        return int(count_str.replace(",", ""))
    except (ValueError, TypeError):
        return 0


class BilibiliBrowserCollector(BaseCollector):
    """用 Playwright 浏览器采集 B站内容：关键词搜索 + 官号主页"""

    def __init__(self):
        super().__init__("BilibiliBrowser")
        self._seen_bvs: set[str] = set()
        self._browser = None
        self._context = None
        self._page = None

    def _warmup(self):
        """预热浏览器环境"""
        try:
            self._page.goto("https://www.bilibili.com", wait_until="domcontentloaded", timeout=15000)
            self._page.wait_for_timeout(2000)
        except Exception as e:
            console.log(f"[dim]B站预热失败: {e}[/dim]")

    def _fetch_video_description(self, bvid: str) -> str:
        """通过 B站 API 获取视频简介文字"""
        try:
            result = self._page.evaluate(f"""
                async () => {{
                    try {{
                        const res = await fetch('https://api.bilibili.com/x/web-interface/view?bvid={bvid}');
                        const json = await res.json();
                        return json.data?.desc || '';
                    }} catch(e) {{ return ''; }}
                }}
            """)
            return result or ""
        except Exception:
            return ""

    def _search_keyword(self, keyword: str, cat_hint: str) -> list[dict]:
        """搜索一个关键词，提取视频卡片数据"""
        encoded = quote(keyword)
        url = f"https://search.bilibili.com/all?keyword={encoded}&order=pubdate"

        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            return []

        try:
            self._page.wait_for_selector(".bili-video-card", timeout=8000)
        except Exception:
            return []

        self._page.wait_for_timeout(1000)

        # 轻量滚动
        for _ in range(1):
            self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self._page.wait_for_timeout(1000)

        videos = self._page.evaluate("""
            () => {
                const videos = [];
                document.querySelectorAll('.bili-video-card').forEach(card => {
                    try {
                        const titleEl = card.querySelector('.bili-video-card__info--tit');
                        const title = titleEl ? (titleEl.getAttribute('title') || titleEl.textContent.trim()) : '';
                        if (!title) return;

                        const linkEl = card.querySelector('a[href*="/video/"]');
                        const url = linkEl ? linkEl.href : '';

                        const authorEl = card.querySelector('.bili-video-card__info--author');
                        const author = authorEl ? authorEl.textContent.trim() : '';

                        const statsItems = card.querySelectorAll('.bili-video-card__stats--item');
                        let plays = '', danmaku = '';
                        statsItems.forEach(item => {
                            const text = item.textContent.trim();
                            if (!plays) plays = text;
                            else if (!danmaku) danmaku = text;
                        });

                        videos.push({ title, url, author, plays, danmaku });
                    } catch(e) {}
                });
                return videos;
            }
        """)

        results = []
        for v in videos[:MAX_SEARCH_PER_KEYWORD]:
            title = v.get("title", "").strip()
            url = v.get("url", "").strip()
            if not title or not url:
                continue

            bv_match = re.search(r"/video/(BV[\w]+)", url)
            bvid = bv_match.group(1) if bv_match else url

            if bvid in self._seen_bvs:
                continue
            self._seen_bvs.add(bvid)

            author = v.get("author", "")
            summary_parts = []
            if author:
                summary_parts.append(f"UP主: {author}")
            if v.get("plays"):
                summary_parts.append(f"播放: {v['plays']}")

            # 识别是否为官号
            is_official = any(
                author == name or name in author
                for name in MANUFACTURER_ACCOUNTS
            )
            source_name = f"B站@{author}" if is_official else f"B站搜索(via {keyword})"

            results.append({
                "title": title,
                "url": url,
                "published_at": None,
                "summary": " | ".join(summary_parts),
                "raw": {
                    "bvid": bvid,
                    "author": author,
                    "play_count": _parse_bilibili_count(v.get("plays", "")),
                    "danmaku": v.get("danmaku", ""),
                    "keyword": keyword,
                    "is_official": is_official,
                },
                "category_hint": cat_hint,
            })

        return results

    def _search_manufacturer(self, query: str, cat_hint: str) -> list[dict]:
        """搜索厂商官号内容，通过 B站搜索 API 获取含简介的视频数据"""
        api_url = (
            "https://api.bilibili.com/x/web-interface/search/type"
            f"?search_type=video&keyword={quote(query)}&page=1&order=pubdate"
        )

        try:
            raw = self._page.evaluate(
                f"""async () => {{
                    try {{
                        const r = await fetch({json.dumps(api_url)});
                        const j = await r.json();
                        if (j.code !== 0 || !j.data?.result) return [];
                        return j.data.result.slice(0, {MAX_MANUFACTURER_PER_ACCOUNT});
                    }} catch(e) {{ return []; }}
                }}"""
            )
            result = raw if isinstance(raw, list) else []
        except Exception:
            return []

        if not result:
            return []

        results = []
        for v in result:
            # API 返回的 title 含 HTML 标签（如 <em class="keyword">）
            raw_title = v.get("title", "")
            title = re.sub(r"<[^>]+>", "", raw_title).strip()
            bvid = v.get("bvid", "")
            if not title or not bvid:
                continue

            if bvid in self._seen_bvs:
                continue

            url = f"https://www.bilibili.com/video/{bvid}"
            author = v.get("author", "")
            description = v.get("description", "").strip()
            if description in ("-", "暂无简介"):
                description = ""

            # 摘要：作者 + 播放量 + 简介
            summary_parts = []
            if author:
                summary_parts.append(f"UP主: {author}")
            play_count = v.get("play", 0)
            if play_count > 0:
                summary_parts.append(
                    f"播放: {play_count/10000:.1f}万" if play_count >= 10000
                    else f"播放: {play_count}"
                )
            if description:
                summary_parts.append(description[:200])

            # 时间戳转换
            published_at = None
            pubdate = v.get("pubdate", 0)
            if pubdate > 0:
                try:
                    published_at = datetime.fromtimestamp(pubdate, tz=timezone.utc)
                except Exception:
                    pass

            # 识别官号
            is_official = any(
                author == name or name in author
                for name in MANUFACTURER_ACCOUNTS
            )

            results.append({
                "title": title,
                "url": url,
                "published_at": published_at,
                "summary": " | ".join(summary_parts),
                "raw": {
                    "bvid": bvid,
                    "author": author,
                    "mid": v.get("mid", 0),
                    "play_count": play_count,
                    "danmaku": str(v.get("video_review", "")),
                    "duration": v.get("length", ""),
                    "tags": v.get("tag", ""),
                    "is_official": is_official,
                    "manufacturer": query,
                },
                "category_hint": cat_hint,
            })

            self._seen_bvs.add(bvid)

        return results

    def fetch(self) -> list[dict]:
        """采集 B站内容：关键词搜索 + 官号搜索"""
        if os.getenv("BILIBILI_BROWSER", "").lower() not in ("1", "true", "yes"):
            console.log("[dim]B站浏览器采集已跳过 (设置 BILIBILI_BROWSER=true 启用)[/dim]")
            return []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            console.log("[red]playwright 未安装，跳过 B站浏览器采集[/red]")
            return []

        total_kw = sum(len(kws) for kws in BILIBILI_SEARCH_KEYWORDS.values())
        console.print(f"\n[yellow]B站浏览器采集: {total_kw} 关键词 + {len(MANUFACTURER_SEARCHES)} 官号[/yellow]")

        all_items = []

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
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

            self._page = page
            self._warmup()

            # ===== 阶段 1：关键词搜索 =====
            for cat_key, keywords in BILIBILI_SEARCH_KEYWORDS.items():
                for kw in keywords:
                    try:
                        videos = self._search_keyword(kw, cat_key)
                        for v in videos:
                            item = self.normalize_item(
                                title=v["title"],
                                url=v["url"],
                                source_name=v.get("raw", {}).get("is_official")
                                    and f"B站官号@{v['raw']['author']}"
                                    or f"B站(via {kw})",
                                source_type="bilibili_browser",
                                published_at=v.get("published_at"),
                                summary=v.get("summary", ""),
                                raw_data=v.get("raw", {}),
                            )
                            item["category"] = v["category_hint"]
                            all_items.append(item)
                        if videos:
                            console.log(f"[dim]B站搜索 '{kw}': {len(videos)} 条[/dim]")
                    except Exception as e:
                        console.log(f"[red]B站搜索 '{kw}' 失败: {e}[/red]")

                    time.sleep(random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX))

            # ===== 阶段 2：官号搜索（API + 简介） =====
            console.log("\n[yellow]  搜索厂商官号 (含视频简介):[/yellow]")
            for query, cat_hint in MANUFACTURER_SEARCHES:
                try:
                    videos = self._search_manufacturer(query, cat_hint)
                    for v in videos:
                        is_official = v.get("raw", {}).get("is_official", False)
                        source = f"B站官号@{v['raw']['author']}" if is_official else f"B站(via {query})"
                        item = self.normalize_item(
                            title=v["title"],
                            url=v["url"],
                            source_name=source,
                            source_type="bilibili_manufacturer",
                            published_at=v.get("published_at"),
                            summary=v.get("summary", ""),
                            raw_data=v.get("raw", {}),
                        )
                        item["category"] = v["category_hint"]
                        all_items.append(item)
                    if videos:
                        console.log(f"[dim]B站官号 '{query}': {len(videos)} 条[/dim]")
                except Exception as e:
                    console.log(f"[red]B站官号 '{query}' 失败: {e}[/red]")

                time.sleep(random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX))

            browser.close()

        console.log(f"[green]B站浏览器总计: {len(all_items)} 条[/green]")
        return all_items
