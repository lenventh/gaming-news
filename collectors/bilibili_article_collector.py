"""B站专栏文章采集器

通过 B站搜索 API 发现游戏设备相关专栏文章，抓取全文内容。
B站专栏是文字为主的深度内容，比视频简介信息量大，适合提取规格参数、
产品分析、行业观点等结构化信息。

适用场景：本地开发（中国 IP 无障碍访问 B站）
CI 环境默认关闭，由 BILIBILI_BROWSER=true 环境变量开启。
"""

import json
import os
import random
import re
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from rich.console import Console

from config import CATEGORIES
from .base import BaseCollector

console = Console()

# 复用 B站浏览器采集器的关键词配置
from .bilibili_browser_collector import BILIBILI_SEARCH_KEYWORDS

MAX_ARTICLE_PER_KEYWORD = 5
MAX_ARTICLE_CONTENT_LENGTH = 2000
ARTICLE_SEARCH_DELAY_MIN = 2
ARTICLE_SEARCH_DELAY_MAX = 4


def _extract_article_text(page) -> str:
    """从 B站专栏页面提取正文文字"""
    try:
        return page.evaluate("""
            () => {
                // B站专栏正文容器
                const selectors = [
                    '.article-content',
                    '.cv-content',
                    '#read-article-holder',
                    '.article-holder',
                    '.read-content',
                    'article',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.textContent.trim().length > 100) {
                        return el.textContent.trim();
                    }
                }
                // 兜底：取 body 中文字最多的区域
                const all = document.querySelectorAll('p, div.article-content p, .cv-content p');
                const parts = [];
                all.forEach(p => {
                    const t = p.textContent.trim();
                    if (t.length > 10) parts.push(t);
                });
                return parts.join('\\n');
            }
        """) or ""
    except Exception:
        return ""


def _parse_article_date(timestamp: int) -> datetime | None:
    """Unix 时间戳 → datetime"""
    if timestamp > 0:
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except Exception:
            pass
    return None


class BilibiliArticleCollector(BaseCollector):
    """通过 B站 API 搜索专栏文章，抓取全文"""

    def __init__(self):
        super().__init__("BilibiliArticle")
        self._seen_cvids: set[int] = set()
        self._page = None

    def _search_articles(self, keyword: str, cat_hint: str) -> list[dict]:
        """通过 B站搜索 API 搜索专栏文章"""
        api_url = (
            "https://api.bilibili.com/x/web-interface/search/type"
            f"?search_type=article&keyword={quote(keyword)}&page=1&order=pubdate"
        )

        try:
            raw = self._page.evaluate(
                f"""async () => {{
                    try {{
                        const r = await fetch({json.dumps(api_url)});
                        const j = await r.json();
                        if (j.code !== 0 || !j.data?.result) return [];
                        return j.data.result.slice(0, {MAX_ARTICLE_PER_KEYWORD});
                    }} catch(e) {{ return []; }}
                }}"""
            )
            result = raw if isinstance(raw, list) else []
        except Exception:
            return []

        if not result:
            return []

        articles = []
        for a in result:
            raw_title = a.get("title", "")
            title = re.sub(r"<[^>]+>", "", raw_title).strip()
            cv_id = a.get("id", 0)
            if not title or not cv_id:
                continue

            if cv_id in self._seen_cvids:
                continue
            self._seen_cvids.add(cv_id)

            url = f"https://www.bilibili.com/read/cv{cv_id}"
            author = a.get("author", "")
            summary = a.get("summary", "").strip()[:300]
            published_at = _parse_article_date(a.get("publish_time", 0))

            view_count = a.get("view", 0)
            like_count = a.get("like", 0)

            articles.append({
                "title": title,
                "url": url,
                "cv_id": cv_id,
                "author": author,
                "summary": summary,
                "published_at": published_at,
                "view_count": view_count,
                "like_count": like_count,
                "keyword": keyword,
                "category_hint": cat_hint,
            })

        return articles

    def _fetch_article_content(self, cv_id: int) -> str:
        """抓取专栏全文内容 — 先尝试 API，失败则用页面抓取"""
        # 方案 1: 尝试移动端文章 API（可能返回内容）
        try:
            raw = self._page.evaluate(f"""
                async () => {{
                    try {{
                        const res = await fetch(
                            'https://api.bilibili.com/x/article/mobile/view?id={cv_id}'
                        );
                        const json = await res.json();
                        if (json.code === 0 && json.data) {{
                            const d = json.data;
                            // 返回正文 + 标题
                            let text = '';
                            if (d.content) {{
                                // content 可能是 HTML，提取纯文本
                                const div = document.createElement('div');
                                div.innerHTML = d.content;
                                text = div.textContent || '';
                            }}
                            if (!text && d.summary) text = d.summary;
                            return text.trim();
                        }}
                    }} catch(e) {{}}
                    return '';
                }}
            """)
            if raw and len(raw) > 100:
                return raw[:MAX_ARTICLE_CONTENT_LENGTH]
        except Exception:
            pass

        # 方案 2: 页面抓取兜底
        try:
            self._page.goto(
                f"https://www.bilibili.com/read/cv{cv_id}",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            self._page.wait_for_timeout(1500)
            return _extract_article_text(self._page)[:MAX_ARTICLE_CONTENT_LENGTH]
        except Exception:
            return ""

    def fetch(self) -> list[dict]:
        """采集 B站专栏文章"""
        if os.getenv("BILIBILI_BROWSER", "").lower() not in ("1", "true", "yes"):
            console.log("[dim]B站文章采集已跳过 (设置 BILIBILI_BROWSER=true 启用)[/dim]")
            return []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            console.log("[red]playwright 未安装，跳过 B站文章采集[/red]")
            return []

        total_kw = sum(len(kws) for kws in BILIBILI_SEARCH_KEYWORDS.values())
        console.print(f"\n[yellow]B站文章采集: {total_kw} 关键词[/yellow]")

        # 阶段 1：搜索文章
        all_articles = []

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )
            page = context.new_page()
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
            """)
            self._page = page

            # 预热
            try:
                page.goto("https://www.bilibili.com", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
            except Exception:
                pass

            for cat_key, keywords in BILIBILI_SEARCH_KEYWORDS.items():
                for kw in keywords:
                    try:
                        articles = self._search_articles(kw, cat_key)
                        all_articles.extend(articles)
                        if articles:
                            console.log(f"[dim]B站文章 '{kw}': {len(articles)} 篇[/dim]")
                    except Exception as e:
                        console.log(f"[red]B站文章 '{kw}' 失败: {e}[/red]")
                    time.sleep(random.uniform(ARTICLE_SEARCH_DELAY_MIN, ARTICLE_SEARCH_DELAY_MAX))

            # 阶段 2：抓取全文（去重后只抓前 50 篇）
            articles_to_fetch = all_articles[:50]
            console.log(f"\n[yellow]  抓取全文: {len(articles_to_fetch)} 篇[/yellow]")

            for article in articles_to_fetch:
                try:
                    content = self._fetch_article_content(article["cv_id"])
                    article["content"] = content
                    if content:
                        console.log(f"[dim]    全文 {len(content)} 字: {article['title'][:40]}[/dim]")
                except Exception as e:
                    article["content"] = ""
                    console.log(f"[red]    抓取失败 cv{article['cv_id']}: {e}[/red]")
                time.sleep(random.uniform(1, 2))

            browser.close()

        # 标准化输出
        items = []
        for article in all_articles:
            content = article.get("content", "")
            summary_parts = [f"UP主: {article['author']}"]
            if article.get("view_count"):
                summary_parts.append(f"阅读: {article['view_count']}")
            if content:
                # 摘要 = API summary + 正文前 300 字
                body_preview = f"正文({len(content)}字): {content[:300]}"
                summary_parts.append(body_preview)
            elif article.get("summary"):
                summary_parts.append(article["summary"])

            item = self.normalize_item(
                title=article["title"],
                url=article["url"],
                source_name=f"B站专栏(via {article['keyword']})",
                source_type="bilibili_article",
                published_at=article.get("published_at"),
                summary=" | ".join(summary_parts),
                raw_data={
                    "cv_id": article["cv_id"],
                    "author": article["author"],
                    "view_count": article.get("view_count", 0),
                    "like_count": article.get("like_count", 0),
                    "keyword": article["keyword"],
                    "content_length": len(content),
                },
            )
            item["category"] = article["category_hint"]
            items.append(item)

        console.log(f"[green]B站文章总计: {len(items)} 篇[/green]")
        return items
