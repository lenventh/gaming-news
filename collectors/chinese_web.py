"""中文源补充采集器

统一用 Google News/Web 搜索 + site: 限定来获取 B站、贴吧、什么值得买等中文源内容。
不直接抓取页面（反爬太强），走搜索引擎中转，稳定可靠。
"""

from datetime import datetime, timezone
from urllib.parse import quote

import feedparser
import requests
from bs4 import BeautifulSoup
from rich.console import Console

from config import CATEGORIES, CUTOFF_DATE
from .base import BaseCollector

console = Console()

# 每个板块的搜索关键词，site: 限定在中文常用平台
SITE_QUERIES = {
    "steam_deck": [
        "Steam Deck site:bilibili.com",
        "Steam Deck 掌机 site:zhihu.com",
    ],
    "windows_handheld": [
        "ROG Ally 掌机 site:bilibili.com",
        "AYANEO 掌机 site:smzdm.com",
    ],
    "android_handheld": [
        "安卓掌机 site:bilibili.com",
        "Retroid 掌机 site:zhihu.com",
    ],
    "linux_handheld": [
        "开源掌机 site:bilibili.com",
        "Anbernic 掌机 site:smzdm.com",
        "Miyoo 掌机 site:bilibili.com",
    ],
    "console": [
        "Switch 2 评测 site:bilibili.com",
        "PS5 Pro 新闻 site:zhihu.com",
    ],
    "handheld_rumors": [
        "掌机 新品 爆料 site:bilibili.com",
    ],
    "emulator": [
        "模拟器 更新 site:bilibili.com",
        "Yuzu 模拟器 site:zhihu.com",
    ],
}


class ChineseWebCollector(BaseCollector):
    def __init__(self):
        super().__init__("ChineseWeb")

    def _search_google(self, query: str, max_results: int = 8) -> list[dict]:
        """用 Google News RSS 搜索"""
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
                title = title.split(" - ")[0]  # 去掉来源后缀

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

                # 来源名称
                source = getattr(entry, "source", {})
                source_name = source.get("title", "Web") if isinstance(source, dict) else str(source)

                results.append({
                    "title": title,
                    "url": real_url,
                    "summary": "",
                    "source_name": source_name,
                    "published_at": pub_date,
                })

        except Exception as e:
            console.log(f"[dim]搜索失败 [{query[:40]}]: {e}[/dim]")

        return results

    def fetch_by_category(self, cat_key: str) -> list[dict]:
        queries = SITE_QUERIES.get(cat_key, [])
        items = []
        seen_urls = set()

        for query in queries:
            results = self._search_google(query)
            for r in results:
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    pub = r.get("published_at")
                    if pub and pub < CUTOFF_DATE:
                        continue
                    item = self.normalize_item(
                        title=r["title"],
                        url=r["url"],
                        source_name=r["source_name"],
                        source_type="chinese_web",
                        published_at=pub,
                        summary=r.get("summary", ""),
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
                console.log(f"[dim]中文源 [{CATEGORIES[cat_key]['name']}]: {len(items)} 条[/dim]")

        console.log(f"[green]中文源总计: {len(all_items)} 条[/green]")
        return all_items
