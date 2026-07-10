"""中文站浏览器采集器：知乎 + 什么值得买

用 Playwright 无头浏览器直接搜索 知乎/什么值得买，抓取掌机相关评测和资讯。
比 Google News RSS 中转方案更直接，能获取 RSS 遗漏的内容。

适用场景：本地开发（中国 IP）
CI 环境默认关闭，由 ZHIHU_BROWSER=true 环境变量开启。
"""

import os
import random
import re
import time
from urllib.parse import quote

from rich.console import Console

from config import CATEGORIES
from .browser_base import BrowserBaseCollector

console = Console()

# 每个分类的关键词，用于知乎和什么值得买搜索
ZHIHU_SEARCH_KEYWORDS = {
    "steam_deck": [
        "Steam Deck 掌机 评测",
        "Steam Deck OLED",
        "SteamOS 掌机",
    ],
    "windows_handheld": [
        "ROG Ally 掌机",
        "AYANEO 掌机",
        "GPD Win 掌机",
        "Windows 掌机 推荐",
        "壹号本 OneXPlayer",
        "MSI Claw 掌机",
        "Legion Go 掌机",
    ],
    "android_handheld": [
        "安卓掌机 推荐",
        "Retroid Pocket 掌机",
        "Odin 掌机 安卓",
        "盖世小鸡 手柄",
    ],
    "linux_handheld": [
        "开源掌机 推荐",
        "Anbernic 掌机",
        "Miyoo 掌机",
        "TrimUI 掌机",
        "周哥 掌机",
    ],
    "console": [
        "Switch 2 评测",
        "PS5 Pro",
        "任天堂 新主机",
        "Xbox 掌机",
    ],
    "emulator": [
        "模拟器 推荐 安卓",
        "Switch 模拟器",
        "Winlator 安卓",
        "Yuzu 模拟器",
    ],
}

SMZDM_SEARCH_KEYWORDS = {
    "steam_deck": [
        "Steam Deck 掌机",
        "Steam Deck 评测",
    ],
    "windows_handheld": [
        "ROG Ally 掌机",
        "AYANEO 掌机",
        "Windows 掌机",
        "GPD 掌机",
    ],
    "android_handheld": [
        "安卓掌机",
        "Retroid 掌机",
        "Odin 掌机",
    ],
    "linux_handheld": [
        "开源掌机",
        "Anbernic 掌机",
        "Miyoo 掌机",
    ],
    "console": [
        "Switch 2",
        "PS5 Pro",
        "Xbox Series",
    ],
    "emulator": [
        "模拟器 掌机",
        "Switch 模拟器",
    ],
}

MAX_PER_KEYWORD = 5
SEARCH_DELAY_MIN = 2
SEARCH_DELAY_MAX = 4


def _extract_zhihu_date(text: str):
    """从知乎时间文本提取 datetime"""
    if not text:
        return None
    from datetime import datetime, timezone, timedelta

    now = datetime.now(tz=timezone.utc)
    text = text.strip()

    # "发布于 2024-01-15"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass

    # "昨天" / "前天"
    if "昨天" in text:
        return now - timedelta(days=1)
    if "前天" in text:
        return now - timedelta(days=2)
    # "X 天前"
    m = re.search(r"(\d+)\s*天前", text)
    if m:
        return now - timedelta(days=int(m.group(1)))
    # "X 小时前"
    m = re.search(r"(\d+)\s*小时前", text)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    # "X 分钟前"
    m = re.search(r"(\d+)\s*分钟前", text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))

    return None


def _extract_smzdm_date(text: str):
    """从什么值得买时间文本提取 datetime"""
    if not text:
        return None
    from datetime import datetime, timezone, timedelta

    now = datetime.now(tz=timezone.utc)
    text = text.strip()

    # "2024-01-15 10:30"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", text)
    if m:
        try:
            return datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)), tzinfo=timezone.utc,
            )
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
    """用 Playwright 浏览器采集 知乎 + 什么值得买"""

    env_var = "ZHIHU_BROWSER"
    warmup_url = "https://www.zhihu.com"

    def __init__(self):
        super().__init__("ChineseBrowser")
        self._seen_urls: set[str] = set()

    def _search_zhihu(self, page, keyword: str, cat_hint: str) -> list[dict]:
        """搜索知乎，提取问答和文章"""

        # 先预热知乎域名（建立 cookie）
        if self._seen_urls == set():  # 首次搜知乎时预热
            pass  # warmup 已经在基类中调用过了

        url = f"https://www.zhihu.com/search?type=content&q={quote(keyword)}&time_interval=a_month"
        try:
            page.goto(url, wait_until="networkidle", timeout=25000)
            page.wait_for_timeout(3000)
        except Exception:
            # 超时时也尝试提取（页面可能已部分加载）
            try:
                page.wait_for_timeout(2000)
            except Exception:
                return []

        # 方案1: 多种选择器尝试匹配搜索结果卡片
        # 方案2: 降级为提取页面中所有知乎内容链接
        results = page.evaluate("""
            () => {
                const items = [];
                const seen = new Set();

                // 尝试多种卡片选择器
                const selectors = [
                    '.List-item',
                    '.SearchResult-Card',
                    '[class*="SearchResult"]',
                    '[class*="search-result"]',
                    '.Card',
                    '[class*="card"]',
                    '[data-za-detail-view-element_name="search-result"]',
                ];

                for (const sel of selectors) {
                    const cards = document.querySelectorAll(sel);
                    if (cards.length > 0) {
                        cards.forEach(card => {
                            try {
                                const links = card.querySelectorAll('a[href*="zhihu.com"]');
                                if (!links.length) return;

                                // 取第一个有意义的链接（跳过用户头像链接）
                                let bestLink = null;
                                for (const l of links) {
                                    const href = l.href;
                                    if (/\\/(question|answer|p|zanda)\\/\\d+/.test(href) && l.textContent.trim().length > 4) {
                                        bestLink = l;
                                        break;
                                    }
                                }
                                if (!bestLink) bestLink = links[0];

                                const title = bestLink.textContent.trim();
                                const url = bestLink.href;
                                if (!title || title.length < 4 || seen.has(url)) return;
                                seen.add(url);

                                const excerpt = card.textContent.trim().substring(title.length, 250).trim();
                                const text = card.textContent.trim();

                                items.push({ title, url, excerpt, text, source: 'card' });
                            } catch(e) {}
                        });
                        if (items.length > 0) break;  // 找到了就不用换选择器
                    }
                }

                // 方案2: 如果卡片选择器没找到，提取所有知乎内容链接
                if (!items.length) {
                    document.querySelectorAll('a[href*="zhihu.com"]').forEach(link => {
                        const href = link.href;
                        if (seen.has(href)) return;

                        // 只提取内容页链接（问题/回答/文章），过滤导航链接
                        const isContent = /\\/(question|answer|p|zanda)\\/\\d+/.test(href);
                        if (!isContent) return;

                        const title = link.textContent.trim();
                        if (title.length < 6) return;
                        seen.add(href);

                        const parent = link.closest('div');
                        const excerpt = parent ? parent.textContent.trim().substring(title.length, 250).trim() : '';

                        items.push({ title, url: href, excerpt, text: '', source: 'link' });
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

            # 从页面文本中提取时间线索
            text = r.get("text", "") or r.get("excerpt", "")
            pub_date = _extract_zhihu_date(text)

            output.append({
                "title": title, "url": article_url, "published_at": pub_date,
                "summary": r.get("excerpt", "")[:200],
                "raw": {"author": "", "keyword": keyword, "source": "zhihu"},
                "category_hint": cat_hint,
            })
        return output

    def _search_smzdm(self, page, keyword: str, cat_hint: str) -> list[dict]:
        """搜索什么值得买，提取评测和好价文章"""
        url = f"https://search.smzdm.com/?c=home&s={quote(keyword)}&order=time"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)
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
        """采集知乎 + 什么值得买"""
        all_items = []
        total_zh = sum(len(kws) for kws in ZHIHU_SEARCH_KEYWORDS.values())
        total_sm = sum(len(kws) for kws in SMZDM_SEARCH_KEYWORDS.values())
        console.print(f"  知乎 {total_zh} 关键词 + 什么值得买 {total_sm} 关键词")

        # 知乎
        console.log("[yellow]  搜索知乎:[/yellow]")
        for cat_key, keywords in ZHIHU_SEARCH_KEYWORDS.items():
            for kw in keywords:
                try:
                    articles = self._search_zhihu(page, kw, cat_key)
                    for a in articles:
                        item = self.normalize_item(
                            title=a["title"], url=a["url"],
                            source_name=f"知乎(via {kw})", source_type="zhihu_browser",
                            published_at=a.get("published_at"), summary=a.get("summary", ""),
                            raw_data=a.get("raw", {}),
                        )
                        item["category"] = a["category_hint"]
                        all_items.append(item)
                    if articles:
                        console.log(f"[dim]  知乎 '{kw[:30]}': {len(articles)} 条[/dim]")
                except Exception as e:
                    console.log(f"[red]  知乎 '{kw}' 失败: {e}[/red]")
                time.sleep(random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX))

        # 什么值得买
        console.log("[yellow]  搜索什么值得买:[/yellow]")
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
