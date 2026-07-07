"""B站厂商官号监控采集器

通过 Google 搜索找到厂商 B站空间 UID → 直接抓取空间视频列表页。
不依赖 B站 API，从 GitHub Actions US IP 可用。
"""

import re
import json
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from rich.console import Console

from config import CATEGORIES, CUTOFF_DATE
from .base import BaseCollector

console = Console()

# 厂商搜索名（自动通过 Google 解析 B站 UID）
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

GOOGLE_SEARCH_URL = "https://www.google.com/search"
BILIBILI_SPACE_VIDEO = "https://space.bilibili.com/{uid}/video"

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})


class BilibiliAccountCollector(BaseCollector):
    """监控 B站厂商官号最新视频（Google 解 UID + 页面抓取）"""

    def __init__(self):
        super().__init__("BilibiliAccount")
        self._uid_cache: dict[str, int] = {}

    def _resolve_uid(self, keyword: str) -> int | None:
        """通过 Google 搜索找到 B站空间 UID"""
        if keyword in self._uid_cache:
            return self._uid_cache[keyword]

        try:
            query = f'site:space.bilibili.com "{keyword}"'
            params = {"q": query, "num": 5, "hl": "zh-CN"}
            resp = _session.get(GOOGLE_SEARCH_URL, params=params, timeout=10)

            # 从搜索结果中提取 UID
            uids = re.findall(r'space\.bilibili\.com/(\d+)', resp.text)
            if uids:
                uid = int(uids[0])
                console.log(f"[dim]Google -> B站UID: {keyword} = {uid}[/dim]")
                self._uid_cache[keyword] = uid
                return uid
        except Exception as e:
            console.log(f"[dim]Google搜UID失败 [{keyword[:15]}]: {e}[/dim]")

        return None

    def _fetch_account_videos(self, uid: int, account_name: str, max_results: int = 5) -> list[dict]:
        """抓取 B站空间视频列表页"""
        results = []
        url = BILIBILI_SPACE_VIDEO.format(uid=uid)

        try:
            resp = _session.get(url, timeout=15)
            # B站空间页会在 <script> 中嵌入 __INITIAL_STATE__ JSON
            match = re.search(
                r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*\(function\(\)',
                resp.text, re.DOTALL
            )
            if not match:
                return results

            data = json.loads(match.group(1))
            vlist = []
            # 尝试多个可能的 JSON 路径
            try:
                vlist = data["video"]["list"]["vlist"]
            except (KeyError, TypeError):
                try:
                    vlist = data["video"]["vlist"]
                except (KeyError, TypeError):
                    try:
                        vlist = data["vlist"]
                    except (KeyError, TypeError):
                        return results

            for v in vlist[:max_results]:
                bvid = v.get("bvid", "")
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
                    "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
                    "summary": description,
                    "source_name": f"B站@{account_name}",
                    "published_at": pub_date,
                    "raw_data": {
                        "bvid": bvid, "play": v.get("play", 0),
                        "comment": v.get("comment", 0), "uid": uid,
                    },
                })

        except Exception as e:
            console.log(f"[dim]B站空间抓取失败 [{account_name}]: {e}[/dim]")

        return results

    def fetch_by_category(self, cat_key: str) -> list[dict]:
        names = MANUFACTURER_SEARCH_NAMES.get(cat_key, [])
        items = []
        seen_urls = set()

        for name in names:
            uid = self._resolve_uid(name)
            if not uid:
                continue
            results = self._fetch_account_videos(uid, name)
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
