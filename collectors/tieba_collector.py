"""贴吧采集器

抓取百度贴吧掌机相关吧的帖子列表。
覆盖 30+ 贴吧，涵盖厂商、品牌、产品线级别的讨论。
"""

from datetime import datetime, timezone
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from rich.console import Console

from config import CATEGORIES, CUTOFF_DATE
from .base import BaseCollector

console = Console()

# 贴吧名 → 分类（覆盖厂商、品牌、产品线级别讨论）
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

TIEBA_URL = "https://tieba.baidu.com/f"


class TiebaCollector(BaseCollector):
    def __init__(self):
        super().__init__("Tieba")

    def _fetch_board(self, board_name: str, max_pages: int = 2) -> list[dict]:
        """抓取单个贴吧的帖子列表"""
        results = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        for page in range(max_pages):
            try:
                params = {"kw": board_name, "ie": "utf-8", "pn": page * 50}
                resp = requests.get(TIEBA_URL, params=params, headers=headers, timeout=15)
                resp.encoding = "utf-8"
                soup = BeautifulSoup(resp.text, "html.parser")

                # 查找帖子列表
                items = soup.select("li.j_thread_list")
                if not items:
                    items = soup.select(".threadlist_li")

                for li in items:
                    title_el = li.select_one("a.j_th_tit") or li.select_one(".threadlist_title a")
                    if not title_el:
                        continue

                    title = title_el.get("title", "") or title_el.get_text(strip=True)
                    href = title_el.get("href", "")
                    if href.startswith("/"):
                        href = f"https://tieba.baidu.com{href}"

                    # 摘要
                    abstract_el = li.select_one(".threadlist_abs")
                    summary = abstract_el.get_text(strip=True) if abstract_el else ""

                    # 作者
                    author_el = li.select_one(".frs-author-name") or li.select_one(".tb_icon_author")
                    author = author_el.get_text(strip=True) if author_el else ""

                    # 日期（贴吧的日期可能是"X分钟前"、"X小时前"、"X月X日"）
                    date_el = li.select_one(".threadlist_reply_date") or li.select_one(".pull_right")
                    date_str = date_el.get_text(strip=True) if date_el else ""
                    published_at = self._parse_tieba_date(date_str)

                    results.append({
                        "title": title,
                        "url": href,
                        "summary": summary,
                        "published_at": published_at,
                        "source_name": f"贴吧{board_name}吧",
                        "raw_data": {"author": author, "board": board_name},
                    })

                if len(items) < 30:
                    break  # 最后一页

            except Exception as e:
                console.log(f"[dim]贴吧 [{board_name}] 抓取失败: {e}[/dim]")
                break

        return results

    def _parse_tieba_date(self, date_str: str):
        """解析贴吧时间格式"""
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
            # "07-06" 或 "2025-07-06"
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
        for board_name, cat_hint in TIEBA_BOARDS.items():
            posts = self._fetch_board(board_name)
            for post in posts:
                published_at = post.get("published_at")
                if published_at and published_at < CUTOFF_DATE:
                    continue

                item = self.normalize_item(
                    title=post["title"],
                    url=post["url"],
                    source_name=post["source_name"],
                    source_type="tieba",
                    published_at=published_at,
                    summary=post["summary"],
                    raw_data=post.get("raw_data", {}),
                )
                if cat_hint:
                    item["category"] = cat_hint
                all_items.append(item)

            console.log(f"[dim]贴吧 [{board_name}]: {len(posts)} 帖[/dim]")

        console.log(f"[green]贴吧总计: {len(all_items)} 条[/green]")
        return all_items
