"""什么值得买 采集器

搜索什么值得买网站的掌机相关优惠和评测。
"""

from datetime import datetime, timezone
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from rich.console import Console

from config import CATEGORIES, CUTOFF_DATE
from .base import BaseCollector
from .keyword_library import get_event_keywords

console = Console()

# 搜索关键词 → 分类
SMZDM_QUERIES = {
    "steam_deck": [
        "Steam Deck",
        # 事件（来自关键词库）
        *get_event_keywords("steam_deck"),
    ],
    "windows_handheld": [
        "ROG Ally", "AYANEO 掌机", "Windows 掌机",
        # 事件（来自关键词库）
        *get_event_keywords("windows_handheld"),
    ],
    "android_handheld": [
        "安卓掌机", "Retroid 掌机",
        # 事件（来自关键词库）
        *get_event_keywords("android_handheld"),
    ],
    "linux_handheld": [
        "开源掌机", "Anbernic", "Miyoo 掌机",
        # 事件（来自关键词库）
        *get_event_keywords("linux_handheld"),
    ],
    "console": [
        "PS5", "Switch 2", "Xbox Series",
        # 事件（来自关键词库）
        *get_event_keywords("console"),
    ],
    "emulator": [
        "模拟器 掌机", "Switch 模拟器",
        # 事件（来自关键词库）
        *get_event_keywords("emulator"),
    ],
}

SMZDM_SEARCH_URL = "https://search.smzdm.com/"


class SmzdmCollector(BaseCollector):
    def __init__(self):
        super().__init__("SMZDM")

    def _search(self, keyword: str, max_results: int = 10) -> list[dict]:
        """搜索什么值得买"""
        results = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        params = {"c": "faxian", "s": keyword, "v": "b", "order": "time"}

        try:
            resp = requests.get(SMZDM_SEARCH_URL, params=params, headers=headers, timeout=15)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            # 查找商品/文章卡片
            cards = soup.select(".feed-row-wide") or soup.select(".feed-card") or soup.select("li.feed-row")

            for card in cards[:max_results]:
                title_el = card.select_one("h5 a") or card.select_one(".feed-block-title a") or card.select_one("a[title]")
                if not title_el:
                    continue

                title = title_el.get("title", "") or title_el.get_text(strip=True)
                url = title_el.get("href", "")
                if url and not url.startswith("http"):
                    url = f"https:{url}" if url.startswith("//") else f"https://www.smzdm.com{url}"

                # 描述
                desc_el = card.select_one(".feed-block-desc") or card.select_one(".feed-desc")
                summary = desc_el.get_text(strip=True) if desc_el else ""

                # 时间
                time_el = card.select_one(".feed-block-extras span") or card.select_one(".feed-time")
                date_str = time_el.get_text(strip=True) if time_el else ""
                published_at = self._parse_smzdm_date(date_str)

                # 价格
                price_el = card.select_one(".feed-block-price") or card.select_one(".feed-price")
                price = price_el.get_text(strip=True) if price_el else ""

                results.append({
                    "title": title,
                    "url": url,
                    "summary": summary,
                    "published_at": published_at,
                    "source_name": "什么值得买",
                    "raw_data": {"price": price, "keyword": keyword},
                })

        except Exception as e:
            console.log(f"[dim]什么值得买搜索失败 [{keyword}]: {e}[/dim]")

        return results

    def _parse_smzdm_date(self, date_str: str):
        if not date_str:
            return None

        now = datetime.now(timezone.utc)
        date_str = date_str.strip()

        if "分钟前" in date_str:
            try:
                mins = int(date_str.replace("分钟前", ""))
                return now - __import__("datetime").timedelta(minutes=mins)
            except Exception:
                pass
        elif "小时前" in date_str:
            try:
                hours = int(date_str.replace("小时前", ""))
                return now - __import__("datetime").timedelta(hours=hours)
            except Exception:
                pass
        elif "-" in date_str:
            parts = date_str.split("-")
            try:
                if len(parts) == 2:
                    return datetime(now.year, int(parts[0]), int(parts[1]), tzinfo=timezone.utc)
                elif len(parts) == 3:
                    return datetime(int(parts[0]), int(parts[1]), int(parts[2]), tzinfo=timezone.utc)
            except Exception:
                pass

        return None

    def fetch(self) -> list[dict]:
        all_items = []
        for cat_key, queries in SMZDM_QUERIES.items():
            for query in queries:
                results = self._search(query)
                for r in results:
                    published_at = r.get("published_at")
                    if published_at and published_at < CUTOFF_DATE:
                        continue

                    item = self.normalize_item(
                        title=r["title"],
                        url=r["url"],
                        source_name=r["source_name"],
                        source_type="smzdm",
                        published_at=published_at,
                        summary=r["summary"],
                        raw_data=r.get("raw_data", {}),
                    )
                    item["category"] = cat_key
                    all_items.append(item)

        console.log(f"[green]什么值得买: {len(all_items)} 条[/green]")
        return all_items
