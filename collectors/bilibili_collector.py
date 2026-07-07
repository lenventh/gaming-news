"""B站搜索采集器

通过 B站公开搜索 API 直接采集视频内容，时效性优于 Google 中转。
"""

from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from rich.console import Console

from config import CATEGORIES, CUTOFF_DATE
from .base import BaseCollector
from .bilibili_wbi import sign_params

console = Console()

# 每个分类的关键词，直接搜 B站 API
BILIBILI_SEARCH_QUERIES = {
    "steam_deck": [
        "Steam Deck",
        "Steam Deck 掌机 评测",
        "Steam Deck 新消息",
    ],
    "windows_handheld": [
        "ROG Ally 掌机",
        "AYANEO 掌机",
        "Windows 掌机",
        "GPD Win",
        "Legion Go 掌机",
        "Windows 掌机 发布会",
        "掌机 新品 发布",
    ],
    "android_handheld": [
        "安卓掌机",
        "Retroid 掌机",
        "Odin 掌机",
        "沙雕掌机",
        "安卓掌机 新品",
    ],
    "linux_handheld": [
        "开源掌机",
        "Anbernic 掌机",
        "Miyoo 掌机",
        "周哥 掌机",
        "开源掌机 新品",
    ],
    "console": [
        "Switch 2",
        "PS5 Pro",
        "任天堂 新机",
        "Switch 2 新消息",
        "掌机 发布会 直播",
    ],
    "handheld_rumors": [
        "掌机 爆料",
        "掌机 新品 发布",
        "Switch 2 传闻",
        "掌机 发布会",
        "掌机 新机 预告",
        "掌机 新品 发布会",
        "新掌机 官宣",
    ],
    "emulator": [
        "模拟器 更新",
        "Switch 模拟器",
        "Yuzu 模拟器",
    ],
}

BILIBILI_API = "https://api.bilibili.com/x/web-interface/search/type"


class BilibiliCollector(BaseCollector):
    def __init__(self):
        super().__init__("Bilibili")

    def _search(self, keyword: str, max_results: int = 5) -> list[dict]:
        """通过 B站 API 搜索视频（Wbi 签名 + web scrape 兜底）"""
        results = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com",
        }

        # 方式 1：Wbi 签名 API
        try:
            params = sign_params({
                "search_type": "video",
                "keyword": keyword,
                "page": 1,
                "order": "pubdate",
            })
            resp = requests.get(BILIBILI_API, params=params, headers=headers, timeout=15)
            data = resp.json()

            if data.get("code") == 0:
                video_list = data.get("data", {}).get("result", [])
                for v in video_list[:max_results]:
                    title = v.get("title", "").replace('<em class="keyword">', "").replace("</em>", "")
                    bvid = v.get("bvid", "")
                    url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
                    description = v.get("description", "")[:200]
                    pub_ts = v.get("pubdate", 0)
                    pub_date = None
                    if pub_ts:
                        try:
                            pub_date = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                        except Exception:
                            pass
                    play = v.get("play", 0)
                    results.append({
                        "title": title, "url": url, "summary": description,
                        "source_name": "B站", "published_at": pub_date,
                        "raw_data": {"bvid": bvid, "play": play, "keyword": keyword},
                    })
                if results:
                    return results
        except Exception:
            pass

        # 方式 2：Web scrape 搜索页兜底
        try:
            from urllib.parse import quote
            search_url = f"https://search.bilibili.com/all?keyword={quote(keyword)}&order=pubdate"
            resp = requests.get(search_url, headers=headers, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            for card in soup.select(".bili-video-card")[:max_results]:
                link = card.select_one("a[href*='/video/']")
                if not link:
                    continue
                title = link.get("title", "") or link.get_text(strip=True)
                href = link.get("href", "")
                if href.startswith("//"):
                    href = f"https:{href}"
                elif href.startswith("/"):
                    href = f"https://www.bilibili.com{href}"

                bvid = ""
                if "/video/" in href:
                    bvid = href.split("/video/")[-1].split("/")[0].split("?")[0]

                if title and href:
                    results.append({
                        "title": title.replace('<em class="keyword">', "").replace("</em>", ""),
                        "url": href, "summary": "",
                        "source_name": "B站", "published_at": None,
                        "raw_data": {"bvid": bvid, "play": 0, "keyword": keyword},
                    })
        except Exception as e:
            console.log(f"[dim]B站搜索失败 [{keyword[:20]}]: {e}[/dim]")

        return results

    def fetch_by_category(self, cat_key: str) -> list[dict]:
        queries = BILIBILI_SEARCH_QUERIES.get(cat_key, [])
        items = []
        seen_urls = set()

        for query in queries:
            results = self._search(query)
            for r in results:
                if r["url"] and r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    pub = r.get("published_at")
                    if pub and pub < CUTOFF_DATE:
                        continue
                    item = self.normalize_item(
                        title=r["title"],
                        url=r["url"],
                        source_name=r["source_name"],
                        source_type="bilibili",
                        published_at=pub,
                        summary=r["summary"],
                        raw_data=r.get("raw_data", {}),
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
