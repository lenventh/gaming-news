"""B站搜索采集器

通过 Google News RSS 采集 B站视频。
不直接调用 B站 API（从 US IP 被封锁），走 Google News 索引中转。
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

# 每个分类的关键词，通过搜索引擎 site:bilibili.com 查询
BILIBILI_SEARCH_QUERIES = {
    "steam_deck": [
        "Steam Deck site:bilibili.com",
        "Steam Deck 掌机 评测 site:bilibili.com",
        "Steam Deck 新消息 site:bilibili.com",
    ],
    "windows_handheld": [
        "ROG Ally 掌机 site:bilibili.com",
        "AYANEO 掌机 site:bilibili.com",
        "Windows 掌机 site:bilibili.com",
        "GPD Win site:bilibili.com",
        "Legion Go 掌机 site:bilibili.com",
        "Windows 掌机 发布会 site:bilibili.com",
        "掌机 新品 发布 site:bilibili.com",
    ],
    "android_handheld": [
        "安卓掌机 site:bilibili.com",
        "Retroid 掌机 site:bilibili.com",
        "Odin 掌机 site:bilibili.com",
        "沙雕掌机 site:bilibili.com",
        "安卓掌机 新品 site:bilibili.com",
    ],
    "linux_handheld": [
        "开源掌机 site:bilibili.com",
        "Anbernic 掌机 site:bilibili.com",
        "Miyoo 掌机 site:bilibili.com",
        "周哥 掌机 site:bilibili.com",
        "开源掌机 新品 site:bilibili.com",
    ],
    "console": [
        "Switch 2 site:bilibili.com",
        "PS5 Pro site:bilibili.com",
        "任天堂 新机 site:bilibili.com",
        "Switch 2 新消息 site:bilibili.com",
        "掌机 发布会 直播 site:bilibili.com",
    ],
    "handheld_rumors": [
        "掌机 爆料 site:bilibili.com",
        "掌机 新品 发布 site:bilibili.com",
        "Switch 2 传闻 site:bilibili.com",
        "掌机 发布会 site:bilibili.com",
        "掌机 新机 预告 site:bilibili.com",
        "新掌机 官宣 site:bilibili.com",
    ],
    "emulator": [
        "模拟器 更新 site:bilibili.com",
        "Switch 模拟器 site:bilibili.com",
        "Yuzu 模拟器 site:bilibili.com",
    ],
}

class BilibiliCollector(BaseCollector):
    """通过 Google News RSS 采集 B站视频（绕过 US IP 封锁）"""

    def __init__(self):
        super().__init__("Bilibili")

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
                title = title.split(" - ")[0]
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
                source_name = source.get("title", "B站") if isinstance(source, dict) else "B站"

                results.append({
                    "title": title,
                    "url": real_url,
                    "summary": "",
                    "source_name": source_name,
                    "published_at": pub_date,
                })
        except Exception as e:
            console.log(f"[dim]B站Google搜索失败 [{query[:30]}]: {e}[/dim]")
        return results

    def _search(self, query: str) -> list[dict]:
        """Google News RSS 搜索，只保留 bilibili.com 域名结果"""
        results = []
        seen = set()

        for r in self._search_google(query):
            url = r.get("url", "")
            if not url or "bilibili.com" not in url:
                continue
            if url in seen:
                continue
            seen.add(url)

            pub = r.get("published_at")
            if pub and pub < CUTOFF_DATE:
                continue

            results.append(r)

        return results

    def fetch_by_category(self, cat_key: str) -> list[dict]:
        queries = BILIBILI_SEARCH_QUERIES.get(cat_key, [])
        items = []
        seen_urls = set()

        for query in queries:
            results = self._search(query)
            for r in results:
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    item = self.normalize_item(
                        title=r["title"],
                        url=r["url"],
                        source_name="B站",
                        source_type="bilibili",
                        published_at=r.get("published_at"),
                        summary=r.get("summary", ""),
                        raw_data={"keyword": query},
                    )
                    item["category"] = cat_key
                    items.append(item)

        return items

    def fetch(self) -> list[dict]:
        all_items = []
        for cat_key in CATEGORIES:
            items = self.fetch_by_category(cat_key)
            all_items.extend(items)
            if items:
                console.log(f"[dim]B站 [{CATEGORIES[cat_key]['name']}]: {len(items)} 条[/dim]")

        console.log(f"[green]B站总计: {len(all_items)} 条[/green]")
        return all_items
