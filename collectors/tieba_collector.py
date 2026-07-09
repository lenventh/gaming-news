"""贴吧采集器

通过 Google News RSS 抓取贴吧帖子。
不直接抓取贴吧页面（从 US IP 会被拦截），走 Google News 索引中转。
DDG 在 GitHub Actions 中被封锁，已移除。
"""

from datetime import datetime, timezone
from urllib.parse import quote

import requests
import feedparser
from rich.console import Console

from config import CATEGORIES, CUTOFF_DATE
from .base import BaseCollector

console = Console()

# 贴吧名 → 分类
TIEBA_BOARDS = {
    # 综合
    "掌机": None,
    # Steam Deck
    "steamdeck": "steam_deck",
    "steamdeck掌机": "steam_deck",
    "steam": "steam_deck",
    "蒸汽平台": "steam_deck",
    # Windows 掌机 — 厂商吧
    "rogally": "windows_handheld",
    "ayaneo": "windows_handheld",
    "gpd掌机": "windows_handheld",
    "壹号本": "windows_handheld",
    "onexplayer": "windows_handheld",
    "win掌机": "windows_handheld",
    "msiclaw": "windows_handheld",
    "legiongo": "windows_handheld",
    "联想掌机": "windows_handheld",
    "飞行家": "windows_handheld",
    "aya掌机": "windows_handheld",
    # 安卓掌机 — 品牌吧
    "retroid": "android_handheld",
    "沙雕": "android_handheld",
    "odin掌机": "android_handheld",
    "安卓掌机": "android_handheld",
    "天马前端": "android_handheld",
    "天马g": "android_handheld",
    "爱吾游戏": "android_handheld",
    "盖世小鸡": "android_handheld",
    "拉伸手柄": "android_handheld",
    "奥丁掌机": "android_handheld",
    "rp5": "android_handheld",
    # 开源掌机
    "开源掌机": "linux_handheld",
    "anbernic": "linux_handheld",
    "周哥掌机": "linux_handheld",
    "miyoo": "linux_handheld",
    "trimui": "linux_handheld",
    "吹米": "linux_handheld",
    "powkiddy": "linux_handheld",
    "霸王小子": "linux_handheld",
    "rg35xx": "linux_handheld",
    "rgcube": "linux_handheld",
    "rg掌机": "linux_handheld",
    "方律师": "linux_handheld",
    "泡机堂": "linux_handheld",
    # 主机
    "switch2": "console",
    "ns2": "console",
    "ps5": "console",
    "ps5pro": "console",
    "xboxone": "console",
    "xboxseriesx": "console",
    "nintendo": "console",
    "switch": "console",
    "playstation": "console",
    "主机游戏": "console",
    # 模拟器
    "模拟器": "emulator",
    "yuzu": "emulator",
    "ryujinx": "emulator",
    "sudachi": "emulator",
    "citra": "emulator",
    "cemu": "emulator",
    "rpcs3": "emulator",
    "vita3k": "emulator",
    "winlator": "emulator",
    "ns模拟器": "emulator",
    "ps3模拟器": "emulator",
    "psv模拟器": "emulator",
    # 传闻（归入具体板块）
    "索尼掌机": "console",
    "xbox掌机": "console",
    "新掌机": "windows_handheld",
    "steam主机": "console",
    "任天堂新机": "console",
}

class TiebaCollector(BaseCollector):
    """通过 Google News RSS 采集贴吧帖子（绕过 US IP 封锁）"""

    def __init__(self):
        super().__init__("Tieba")

    def _search_google(self, query: str, max_results: int = 8) -> list[dict]:
        """Google News RSS 搜索"""
        results = []
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"

        try:
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (compatible; GamingNewsBot/1.0)"
            })
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)

            for entry in feed.entries[:max_results]:
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "")
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(link)
                params = parse_qs(parsed.query)
                real_url = params.get("url", [link])[0]

                pub_date = None
                tp = getattr(entry, "published_parsed", None)
                if tp:
                    try:
                        pub_date = datetime(*tp[:6], tzinfo=timezone.utc)
                    except Exception:
                        pass

                source = getattr(entry, "source", {})
                source_name = source.get("title", "Web") if isinstance(source, dict) else str(source)

                results.append({
                    "title": title.split(" - ")[0],
                    "url": real_url,
                    "summary": "",
                    "source_name": source_name,
                    "published_at": pub_date,
                })
        except Exception as e:
            console.log(f"[dim]贴吧Google搜索失败 [{query[:30]}]: {e}[/dim]")
        return results

    def _fetch_board(self, board_name: str) -> list[dict]:
        """通过 Google News RSS 搜索贴吧帖子"""
        results = []
        seen = set()

        queries = [
            f"site:tieba.baidu.com {board_name}",
            f"{board_name} 吧 site:tieba.baidu.com",
            f"{board_name} 吧 讨论 site:tieba.baidu.com",
            f"{board_name} 吧 新帖 site:tieba.baidu.com",
        ]

        for query in queries:
            for r in self._search_google(query):
                url = r.get("url", "")
                if not url:
                    continue
                if url in seen:
                    continue
                seen.add(url)

                pub = r.get("published_at")
                if pub and pub < CUTOFF_DATE:
                    continue

                results.append({
                    "title": r["title"],
                    "url": url,
                    "summary": r.get("summary", ""),
                    "source_name": f"贴吧{board_name}吧",
                    "published_at": pub,
                    "board": board_name,
                })

        return results

    def fetch(self) -> list[dict]:
        all_items = []
        for board_name, cat_hint in TIEBA_BOARDS.items():
            posts = self._fetch_board(board_name)
            for post in posts:
                item = self.normalize_item(
                    title=post["title"],
                    url=post["url"],
                    source_name=post["source_name"],
                    source_type="tieba",
                    published_at=post.get("published_at"),
                    summary=post.get("summary", ""),
                    raw_data={"board": post.get("board", board_name)},
                )
                if cat_hint:
                    item["category"] = cat_hint
                all_items.append(item)

            console.log(f"[dim]贴吧 [{board_name}]: {len(posts)} 帖[/dim]")

        console.log(f"[green]贴吧总计: {len(all_items)} 条[/green]")
        return all_items
