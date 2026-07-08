"""贴吧采集器

通过 Google News + DuckDuckGo 文本搜索抓取贴吧帖子。
不直接抓取贴吧页面（从 US IP 会被拦截），走搜索引擎中转。
"""

import time
import random
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
    # Windows 掌机 — 厂商吧
    "rogally": "windows_handheld",
    "ayaneo": "windows_handheld",
    "gpd掌机": "windows_handheld",
    "壹号本": "windows_handheld",
    "win掌机": "windows_handheld",
    "msiclaw": "windows_handheld",
    # 安卓掌机 — 品牌吧
    "retroid": "android_handheld",
    "沙雕": "android_handheld",
    "odin掌机": "android_handheld",
    "安卓掌机": "android_handheld",
    # 开源掌机
    "开源掌机": "linux_handheld",
    "anbernic": "linux_handheld",
    "miyoo": "linux_handheld",
    "trimui": "linux_handheld",
    "powkiddy": "linux_handheld",
    "霸王小子": "linux_handheld",
    # 主机
    "switch2": "console",
    "ps5": "console",
    "xboxone": "console",
    "nintendo": "console",
    "switch": "console",
    # 模拟器
    "模拟器": "emulator",
    "yuzu": "emulator",
    "ryujinx": "emulator",
    # 传闻
    "索尼掌机": "handheld_rumors",
    "xbox掌机": "handheld_rumors",
}

# DDG 实例（延迟加载 + 复用）
_DDGS = None

def _get_ddgs():
    global _DDGS
    if _DDGS is None:
        try:
            from duckduckgo_search import DDGS
            _DDGS = DDGS()
        except Exception:
            _DDGS = False
    return _DDGS if _DDGS is not False else None


class TiebaCollector(BaseCollector):
    """通过搜索引擎采集贴吧帖子（绕过 US IP 封锁）"""

    def __init__(self):
        super().__init__("Tieba")

    def _search_google(self, query: str, max_results: int = 8) -> list[dict]:
        """Google News RSS 搜索"""
        results = []
        try:
            url = f"https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
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

    def _search_ddg(self, query: str, max_results: int = 5) -> list[dict]:
        """DDG 文本搜索（覆盖 Google News 不索引的贴吧页面）"""
        results = []
        ddgs = _get_ddgs()
        if ddgs is None:
            return results

        try:
            time.sleep(random.uniform(1.0, 2.5))  # DDG 限流保护
            entries = list(ddgs.text(query, region="cn-zh", max_results=max_results))
            for entry in entries:
                pub_date = None
                date_str = entry.get("date", "")
                if date_str:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_date = parsedate_to_datetime(date_str)
                    except Exception:
                        pass

                results.append({
                    "title": entry.get("title", ""),
                    "url": entry.get("href", ""),
                    "summary": entry.get("body", "")[:300],
                    "source_name": "DDG",
                    "published_at": pub_date,
                })
        except Exception as e:
            err = str(e)[:60]
            console.log(f"[dim]贴吧DDG失败 [{query[:20]}]: {err}[/dim]")
        return results

    def _fetch_board(self, board_name: str) -> list[dict]:
        """通过搜索引擎搜索贴吧帖子"""
        results = []
        seen = set()

        # 两个查询角度：site: + 吧名
        queries = [
            f"site:tieba.baidu.com {board_name}",
            f"{board_name} 吧 site:tieba.baidu.com",
        ]

        for query in queries:
            g_results = self._search_google(query)
            d_results = self._search_ddg(query)
            for r in g_results + d_results:
                url = r.get("url", "")
                if not url or "tieba.baidu.com" not in url:
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
