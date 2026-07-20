"""RSS 通用采集器"""

import time
import random
from datetime import datetime, timezone
from typing import Optional

import feedparser
import requests
from rich.console import Console

from .base import BaseCollector

console = Console()

# 延迟范围（秒），避免被限流
# Reddit RSS 源之间的延迟（增加以应对 429）
REDDIT_SOURCE_DELAY_MIN = 12
REDDIT_SOURCE_DELAY_MAX = 18
# 单个 Reddit RSS 请求前的延迟
REDDIT_REQUEST_DELAY_MIN = 3
REDDIT_REQUEST_DELAY_MAX = 7
# 所有 RSS 源之间的通用延迟（礼貌爬取）
GENERAL_DELAY_MIN = 1
GENERAL_DELAY_MAX = 3


class RSSCollector(BaseCollector):
    def __init__(self, name: str, feed_url: str, category_hint: Optional[str] = None, filter_keywords: Optional[list[str]] = None):
        super().__init__(name)
        self.feed_url = feed_url
        self.category_hint = category_hint
        self.filter_keywords = [kw.lower() for kw in (filter_keywords or [])]

    def _parse_date(self, entry) -> Optional[datetime]:
        """尝试从 feed 条目中解析日期"""
        for attr in ("published_parsed", "updated_parsed"):
            tp = getattr(entry, attr, None)
            if tp:
                try:
                    return datetime(*tp[:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    continue

        for attr in ("published", "updated"):
            date_str = getattr(entry, attr, None)
            if date_str:
                try:
                    from email.utils import parsedate_to_datetime
                    return parsedate_to_datetime(date_str)
                except Exception:
                    pass
        return None

    def _extract_image(self, entry) -> list[str]:
        """提取条目中的图片链接作为素材"""
        images = []

        if hasattr(entry, "media_content") and entry.media_content:
            for media in entry.media_content:
                url = media.get("url", "")
                if url and any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                    images.append(url)

        if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
            for thumb in entry.media_thumbnail:
                url = thumb.get("url", "")
                if url:
                    images.append(url)

        if hasattr(entry, "links"):
            for link in entry.links:
                if link.get("type", "").startswith("image/"):
                    images.append(link.get("href", ""))

        return images[:3]

    def fetch(self, retry_on_429: bool = True) -> list[dict]:
        items = []
        is_reddit = "reddit" in self.feed_url.lower()
        max_retries = 4 if (is_reddit and retry_on_429) else 1

        for attempt in range(max_retries):
            try:
                if is_reddit and attempt == 0:
                    pre_delay = random.uniform(REDDIT_REQUEST_DELAY_MIN, REDDIT_REQUEST_DELAY_MAX)
                    time.sleep(pre_delay)

                resp = requests.get(self.feed_url, timeout=30, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; GamingNewsBot/1.0)"
                })
                if resp.status_code == 429 and attempt < max_retries - 1:
                    # 连续 429 后加额外冷却
                    cooldown = 30 if attempt >= 2 else 0
                    wait = (2 ** attempt) * 10 + random.uniform(0, 5) + cooldown  # 10s, 20s, 70s, 110s
                    console.log(f"[yellow]Reddit 限流 [{self.name}], {wait:.0f}s 后重试({attempt+1}/{max_retries})...[/yellow]")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
                break
            except Exception as e:
                if attempt < max_retries - 1 and is_reddit:
                    time.sleep(10 * (attempt + 1))
                    continue
                console.log(f"[red]RSS 采集失败 [{self.name}]: {e}[/red]")
                return items

        for entry in feed.entries:
            published_at = self._parse_date(entry)

            title = getattr(entry, "title", "")
            link = getattr(entry, "link", "")
            if not title or not link:
                continue

            summary = ""
            if hasattr(entry, "summary"):
                from bs4 import BeautifulSoup
                summary = BeautifulSoup(entry.summary, "html.parser").get_text()[:800]
            elif hasattr(entry, "description"):
                from bs4 import BeautifulSoup
                summary = BeautifulSoup(entry.description, "html.parser").get_text()[:800]

            # 清理 Reddit 原始格式标记
            if "reddit" in self.feed_url.lower():
                import re
                summary = re.sub(r"submitted by\s+/u/\S+\s*\[link\]\s*\[comments\]", "", summary)
                summary = re.sub(r"\s{2,}", " ", summary).strip()

            images = self._extract_image(entry)

            # 关键词过滤：如果配置了filter_keywords，只保留匹配的条目
            if self.filter_keywords:
                text = (title + " " + summary).lower()
                if not any(kw in text for kw in self.filter_keywords):
                    continue

            item = self.normalize_item(
                title=title,
                url=link,
                source_name=self.name,
                source_type="rss",
                published_at=published_at,
                summary=summary,
                raw_data={"feed_title": feed.feed.get("title", "")},
                material_links=images,
            )

            if self.category_hint:
                item["category"] = self.category_hint

            items.append(item)

        console.log(f"[green]RSS [{self.name}]: {len(items)} 条[/green]")
        return items


def collect_all_rss(sources: list[dict]) -> list[dict]:
    """从所有 RSS 源采集新闻。每个源之间加延迟避免限流"""
    all_items = []
    for src in sources:
        collector = RSSCollector(
            name=src["name"],
            feed_url=src["url"],
            category_hint=src.get("category_hint"),
            filter_keywords=src.get("filter_keywords"),
        )
        all_items.extend(collector.fetch())

        # 源之间加延迟：Reddit 源用较长延迟，其他源用通用礼貌延迟
        is_reddit = "reddit" in src.get("url", "").lower()
        if is_reddit:
            delay = random.uniform(REDDIT_SOURCE_DELAY_MIN, REDDIT_SOURCE_DELAY_MAX)
        else:
            delay = random.uniform(GENERAL_DELAY_MIN, GENERAL_DELAY_MAX)
        time.sleep(delay)

    return all_items
