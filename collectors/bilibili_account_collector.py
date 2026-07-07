"""B站厂商官号监控采集器

通过 B站搜索 API 查找厂商官方号 → 空间 API 拉取最新视频。
抓取发布会、新品预告等第一手消息，解决传统 RSS 覆盖不到的问题。
"""

from datetime import datetime, timezone
from urllib.parse import quote

import requests
from rich.console import Console

from config import CATEGORIES, CUTOFF_DATE
from .base import BaseCollector

console = Console()

# 各分类需监控的 B站厂商/UP 搜索关键词
MANUFACTURER_QUERIES = {
    "steam_deck": [
        "Steam Deck 掌机",
    ],
    "windows_handheld": [
        "AYANEO掌机",
        "GPD掌机",
        "壹号本科技",
        "ROG玩家国度",
        "微星笔记本",
        "联想拯救者",
        "AOKZOE",
    ],
    "android_handheld": [
        "Retroid",
        "AYN Odin",
        "Abxylute",
    ],
    "linux_handheld": [
        "ANBERNIC",
        "Anbernic安博尼克",
        "Miyoo掌机",
        "霸王小子",
        "TrimUI",
        "PowKiddy",
    ],
    "console": [
        "PlayStation中国",
        "任天堂Switch",
        "Xbox中国",
    ],
    "handheld_rumors": [
        "掌机 新品 发布会",
    ],
    "emulator": [
        "模拟器",
    ],
}

SPACE_API = "https://api.bilibili.com/x/space/arc/search"
USER_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"


class BilibiliAccountCollector(BaseCollector):
    """监控 B站厂商官号最新视频"""

    def __init__(self):
        super().__init__("BilibiliAccount")
        self._uid_cache: dict[str, int] = {}

    def _search_uid(self, keyword: str) -> int | None:
        """搜索厂商 B站 UID（带缓存）"""
        if keyword in self._uid_cache:
            return self._uid_cache[keyword]

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com",
        }
        try:
            params = {"search_type": "bili_user", "keyword": keyword, "page": 1}
            resp = requests.get(USER_SEARCH_API, params=params, headers=headers, timeout=10)
            data = resp.json()
            if data.get("code") == 0:
                users = data.get("data", {}).get("result", [])
                if users:
                    uid = users[0]["mid"]
                    uname = users[0].get("uname", "")
                    console.log(f"[dim]B站官号: {keyword} → {uname} (UID:{uid})[/dim]")
                    self._uid_cache[keyword] = uid
                    return uid
        except Exception as e:
            console.log(f"[dim]B站用户搜索失败 [{keyword[:15]}]: {e}[/dim]")

        return None

    def _fetch_account_videos(self, keyword: str, max_results: int = 5) -> list[dict]:
        """获取厂商官号最新视频"""
        uid = self._search_uid(keyword)
        if not uid:
            return []

        results = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://space.bilibili.com",
        }
        try:
            params = {"mid": uid, "ps": max_results, "tid": 0, "order": "pubdate"}
            resp = requests.get(SPACE_API, params=params, headers=headers, timeout=10)
            data = resp.json()
            if data.get("code") != 0:
                return results

            videos = data.get("data", {}).get("list", {}).get("vlist", [])
            for v in videos:
                bvid = v.get("bvid", "")
                url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
                title = v.get("title", "")
                description = v.get("description", "")[:200]
                pub_ts = v.get("created", 0)
                pub_date = None
                if pub_ts:
                    try:
                        pub_date = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                    except Exception:
                        pass

                results.append({
                    "title": title,
                    "url": url,
                    "summary": description,
                    "source_name": f"B站@{v.get('author', keyword)}",
                    "published_at": pub_date,
                    "raw_data": {
                        "bvid": bvid,
                        "play": v.get("play", 0),
                        "comment": v.get("comment", 0),
                        "uid": uid,
                        "keyword": keyword,
                    },
                })

        except Exception as e:
            console.log(f"[dim]B站空间抓取失败 [{keyword[:15]}]: {e}[/dim]")

        return results

    def fetch_by_category(self, cat_key: str) -> list[dict]:
        queries = MANUFACTURER_QUERIES.get(cat_key, [])
        items = []
        seen_urls = set()

        for query in queries:
            results = self._fetch_account_videos(query)
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
                        source_type="bilibili_account",
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
                console.log(f"[dim]B站官号 [{CATEGORIES[cat_key]['name']}]: {len(items)} 条[/dim]")

        console.log(f"[green]B站官号总计: {len(all_items)} 条[/green]")
        return all_items
