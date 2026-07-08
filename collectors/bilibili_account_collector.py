"""B站厂商官号监控采集器

通过 Google News RSS，以厂商名 + site:bilibili.com
查询厂商官方 B站账号发布的视频。不直接调用 B站 API（从 US IP 被封锁）。
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

# 厂商搜索名（用于搜索引擎查询 site:bilibili.com）
MANUFACTURER_SEARCH_NAMES = {
    "steam_deck": [],
    "windows_handheld": [
        "AYANEO掌机",
        "GPD掌机官方",
        "壹号本科技 OneXPlayer",
        "ROG玩家国度官方",
        "AOKZOE掌机",
    ],
    "android_handheld": [
        "AYN掌机 Odin",
        "Retroid掌机",
        "沙雕掌机",
    ],
    "linux_handheld": [
        "ANBERNIC安伯尼克官方",
        "Miyoo掌机",
        "霸王小子",
        "TrimUI掌机",
        "PowKiddy掌机",
    ],
    "console": [
        "PlayStation中国官方",
        "任天堂Switch官方",
        "Xbox中国官方",
    ],
    "handheld_rumors": [],
    "emulator": [],
}

class BilibiliAccountCollector(BaseCollector):
    """通过 Google News RSS 监控 B站厂商官号（绕过 US IP 封锁）"""

    def __init__(self):
        super().__init__("BilibiliAccount")

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
            console.log(f"[dim]B站官号Google搜索失败 [{query[:20]}]: {e}[/dim]")
        return results

    def _search_account(self, account_name: str) -> list[dict]:
        """通过 Google News RSS 查找厂商 B站视频"""
        results = []
        seen = set()

        queries = [
            f"{account_name} site:bilibili.com",
            f"{account_name} 发布 site:bilibili.com",
        ]

        for query in queries:
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
        names = MANUFACTURER_SEARCH_NAMES.get(cat_key, [])
        items = []
        seen_urls = set()

        for name in names:
            results = self._search_account(name)
            for r in results:
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    item = self.normalize_item(
                        title=r["title"],
                        url=r["url"],
                        source_name=f"B站@{name}",
                        source_type="bilibili_account",
                        published_at=r.get("published_at"),
                        summary=r.get("summary", ""),
                        raw_data={"account": name},
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
                console.log(f"[dim]B站官号 [{CATEGORIES[cat_key]['name']}]: {len(items)} 条[/dim]")

        console.log(f"[green]B站官号总计: {len(all_items)} 条[/green]")
        return all_items
