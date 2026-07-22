"""中文站浏览器采集器：什么值得买

用 Playwright 无头浏览器直接搜索什么值得买，抓取掌机相关评测和资讯。
比 Google News RSS 中转方案更直接，能获取 RSS 遗漏的内容。

注意：知乎需要登录才能搜索（API 返回 ZERR_NOT_LOGIN），无法浏览器直抓。
知乎内容通过 ChineseWebCollector (Google News RSS site:zhihu.com) 覆盖。

适用场景：本地开发（中国 IP）
CI 环境默认关闭，由 ZHIHU_BROWSER=true 环境变量开启。
"""

import random
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from rich.console import Console

from .browser_base import BrowserBaseCollector
from .keyword_library import get_event_keywords

console = Console()

SMZDM_SEARCH_KEYWORDS = {
    "steam_deck": [
        "Steam Deck 掌机",
        "Steam Deck 评测",
        # 事件（来自关键词库）
        *get_event_keywords("steam_deck"),
    ],
    "windows_handheld": [
        "ROG Ally 掌机",
        "AYANEO 掌机",
        "Windows 掌机",
        "GPD 掌机",
        # 事件（来自关键词库）
        *get_event_keywords("windows_handheld"),
    ],
    "android_handheld": [
        "安卓掌机",
        "Retroid 掌机",
        "Odin 掌机",
        # 事件（来自关键词库）
        *get_event_keywords("android_handheld"),
    ],
    "linux_handheld": [
        "开源掌机",
        "Anbernic 掌机",
        "Miyoo 掌机",
        # 事件（来自关键词库）
        *get_event_keywords("linux_handheld"),
    ],
    "playstation": [
        "PS5 Pro", "PS6", "PlayStation Portal",
        *get_event_keywords("playstation"),
    ],
    "xbox": [
        "Xbox Series", "Xbox 掌机",
        *get_event_keywords("xbox"),
    ],
    "nintendo": [
        "Switch 2", "Switch 2 爆料",
        *get_event_keywords("nintendo"),
    ],
    "emulator": [
        "模拟器 掌机",
        "Switch 模拟器",
        # 事件（来自关键词库）
        *get_event_keywords("emulator"),
    ],
}

MAX_PER_KEYWORD = 5
SEARCH_DELAY_MIN = 2
SEARCH_DELAY_MAX = 4


def _extract_smzdm_date(text: str):
    """从什么值得买时间文本提取 datetime"""
    if not text:
        return None
    now = datetime.now(tz=timezone.utc)
    text = text.strip()

    # "2024-01-15 10:30"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                          int(m.group(4)), int(m.group(5)), tzinfo=timezone.utc)
        except ValueError:
            pass

    # "X小时前", "X分钟前", "昨天"
    m = re.search(r"(\d+)\s*小时前", text)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    m = re.search(r"(\d+)\s*分钟前", text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    if "昨天" in text:
        return now - timedelta(days=1)
    return None


class ChineseBrowserCollector(BrowserBaseCollector):
    """用 Playwright 浏览器采集什么值得买"""

    env_var = "ZHIHU_BROWSER"
    warmup_url = "https://www.smzdm.com"

    def __init__(self):
        super().__init__("ChineseBrowser")
        self._seen_urls: set[str] = set()

    def _search_smzdm(self, page, keyword: str, cat_hint: str) -> list[dict]:
        """搜索什么值得买，提取评测和好价文章"""
        url = f"https://search.smzdm.com/?c=home&s={quote(keyword)}&order=time"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2500)
        except Exception:
            return []

        results = page.evaluate("""
            () => {
                const items = [];
                document.querySelectorAll('.feed-row-wide, .feed-row-a, .list-content li, [class*="search-result"]').forEach(card => {
                    try {
                        const titleEl = card.querySelector('h5, .feed-block-title, [class*="title"] a');
                        const title = titleEl ? titleEl.textContent.trim() : '';
                        if (!title || title.length < 4) return;
                        const linkEl = card.querySelector('a[href*="smzdm.com"]');
                        const url = linkEl ? linkEl.href : '';
                        const excerptEl = card.querySelector('.feed-block-descripe, [class*="desc"], [class*="content"]');
                        const excerpt = excerptEl ? excerptEl.textContent.trim().substring(0, 200) : '';
                        const timeEl = card.querySelector('.feed-block-extras, [class*="time"], [class*="date"]');
                        const timeText = timeEl ? timeEl.textContent.trim() : '';
                        const authorEl = card.querySelector('.feed-block-author, [class*="author"]');
                        const author = authorEl ? authorEl.textContent.trim() : '';
                        const statsEl = card.querySelector('.feed-block-verdict, [class*="stats"]');
                        const stats = statsEl ? statsEl.textContent.trim() : '';
                        items.push({ title, url, excerpt, time: timeText, author, stats });
                    } catch(e) {}
                });
                if (!items.length) {
                    document.querySelectorAll('a[href*="smzdm.com/p/"]').forEach(link => {
                        const title = link.textContent.trim();
                        if (title.length < 6) return;
                        const parent = link.closest('li, .feed-row-wide, div');
                        items.push({ title, url: link.href, excerpt: parent ? parent.textContent.trim().substring(title.length, 250) : '', time: '', author: '', stats: '' });
                    });
                }
                return items.slice(0, 10);
            }
        """)

        output = []
        for r in results[:MAX_PER_KEYWORD]:
            title = r.get("title", "").strip()
            article_url = r.get("url", "").strip()
            if not title or not article_url:
                continue
            if article_url in self._seen_urls:
                continue
            self._seen_urls.add(article_url)
            article_url = re.sub(r"\?.*$", "", article_url)
            pub_date = _extract_smzdm_date(r.get("time", ""))
            output.append({
                "title": title, "url": article_url, "published_at": pub_date,
                "summary": r.get("excerpt", ""),
                "raw": {"author": r.get("author", ""), "stats": r.get("stats", ""), "keyword": keyword, "source": "smzdm"},
                "category_hint": cat_hint,
            })
        return output

    def _scrape(self, page) -> list[dict]:
        """采集什么值得买"""
        all_items = []
        total = sum(len(kws) for kws in SMZDM_SEARCH_KEYWORDS.values())
        console.print(f"  什么值得买 {total} 个关键词")

        for cat_key, keywords in SMZDM_SEARCH_KEYWORDS.items():
            for kw in keywords:
                try:
                    articles = self._search_smzdm(page, kw, cat_key)
                    for a in articles:
                        item = self.normalize_item(
                            title=a["title"], url=a["url"],
                            source_name=f"什么值得买(via {kw})", source_type="smzdm_browser",
                            published_at=a.get("published_at"), summary=a.get("summary", ""),
                            raw_data=a.get("raw", {}),
                        )
                        item["category"] = a["category_hint"]
                        all_items.append(item)
                    if articles:
                        console.log(f"[dim]  SMZDM '{kw[:30]}': {len(articles)} 条[/dim]")
                except Exception as e:
                    console.log(f"[red]  SMZDM '{kw}' 失败: {e}[/red]")
                time.sleep(random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX))

        return all_items
