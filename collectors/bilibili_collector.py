"""B站搜索采集器

通过 B站网页搜索 + Google 搜索补充（B站 API 反爬较严）。
"""

from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from rich.console import Console

from config import CATEGORIES, CUTOFF_DATE
from .base import BaseCollector

console = Console()

BILIBILI_QUERIES = {
    "steam_deck": ["Steam Deck 掌机"],
    "windows_handheld": ["ROG Ally 掌机", "AYANEO 掌机"],
    "linux_handheld": ["开源掌机 新品", "Anbernic"],
    "console": ["Switch 2 评测"],
    "android_handheld": ["安卓掌机"],
    "emulator": ["模拟器 更新"],
    "handheld_rumors": ["掌机 爆料 新品"],
}


class BilibiliCollector(BaseCollector):
    def __init__(self):
        super().__init__("Bilibili")

    def _search(self, keyword: str, max_results: int = 10) -> list[dict]:
        """通过 B站搜索页 + Google fallback"""
        results = []

        # 方案1：B站 Web 搜索
        try:
            url = f"https://search.bilibili.com/all?keyword={quote(keyword)}&order=pubdate"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
            resp = requests.get(url, headers=headers, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            # B站搜索结果通常在 .video-list 或 script 标签中
            cards = soup.select(".video-list-item") or soup.select(".bili-video-card")
            if not cards:
                cards = soup.select("[class*=video]")

            for card in cards[:max_results]:
                title_el = card.select_one("a[title]") or card.select_one(".title") or card.select_one("h3 a")
                if not title_el:
                    continue
                title = title_el.get("title", "") or title_el.get_text(strip=True)
                href = title_el.get("href", "")
                if href.startswith("//"):
                    href = f"https:{href}"

                # 从 URL 提取 BV 号
                bvid = ""
                if "/video/" in href:
                    bvid = href.split("/video/")[-1].split("/")[0].split("?")[0]

                if title and href:
                    results.append({
                        "title": title,
                        "url": href,
                        "summary": "",
                        "source_name": f"B站",
                        "raw_data": {"bvid": bvid, "keyword": keyword},
                    })
        except Exception as e:
            console.log(f"[dim]B站网页搜索失败 [{keyword}]: {e}[/dim]")

        # 方案2：如果 B站没结果，用 Google site 搜索补充
        if not results:
            results = self._google_site_search(keyword)

        return results

    def _google_site_search(self, keyword: str) -> list[dict]:
        """用 Google 搜索 site:bilibili.com 找 B站视频"""
        results = []
        try:
            import feedparser
            query = f"site:bilibili.com {keyword}"
            url = f"https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN"
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            feed = feedparser.parse(resp.content)

            for entry in feed.entries[:5]:
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "")

                # 提取真实 URL
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

                results.append({
                    "title": title.replace(" - 哔哩哔哩", ""),
                    "url": real_url,
                    "summary": "",
                    "source_name": "B站(via Google)",
                    "raw_data": {"keyword": keyword},
                    "published_at": pub_date,
                })
        except Exception:
            pass

        return results

    def fetch(self) -> list[dict]:
        all_items = []
        for cat_key, queries in BILIBILI_QUERIES.items():
            for query in queries:
                results = self._search(query)
                for r in results:
                    item = self.normalize_item(
                        title=r["title"],
                        url=r["url"],
                        source_name=r["source_name"],
                        source_type="bilibili",
                        published_at=r.get("published_at"),
                        summary=r.get("summary", ""),
                        raw_data=r.get("raw_data", {}),
                    )
                    item["category"] = cat_key
                    all_items.append(item)

        console.log(f"[green]B站搜索: {len(all_items)} 条[/green]")
        return all_items
