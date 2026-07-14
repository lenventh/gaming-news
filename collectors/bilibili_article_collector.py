"""B站专栏文章 + 动态采集器

通过 UP主空间 API 直接拉取最近的文章和动态（非关键词搜索），
按发布时间倒序排列，只保留 7 天内的内容。

相比关键词搜索方案：API 调用从 383 次降到 ~32 次，时间从 ~25min 降到 ~2min，
且 API 返回精确时间戳，解决了旧方案 461 条日期不明的问题。

适用场景：本地开发（中国 IP 无障碍访问 B站）
CI 环境默认关闭，由 BILIBILI_BROWSER=true 环境变量开启。
"""

import json
import os
import random
import re
import time
from datetime import datetime, timezone, timedelta

from rich.console import Console

from config import CATEGORIES, CUTOFF_DATE
from .base import BaseCollector
from .bilibili_browser_collector import MANUFACTURER_ACCOUNTS, NEWS_UP_ACCOUNTS

console = Console()

MAX_PER_ACCOUNT = 10          # 每个 UP 主最多拉几条
MAX_ARTICLE_CONTENT_LENGTH = 2000
FETCH_DELAY_MIN = 2
FETCH_DELAY_MAX = 4

# 合并所有目标账号
ALL_TARGET_ACCOUNTS = {}
ALL_TARGET_ACCOUNTS.update(MANUFACTURER_ACCOUNTS)
ALL_TARGET_ACCOUNTS.update(NEWS_UP_ACCOUNTS)


def _parse_unix_timestamp(ts: int) -> datetime | None:
    """Unix 时间戳 → datetime"""
    if ts > 0:
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass
    return None


def _extract_article_text(page) -> str:
    """从 B站专栏页面提取正文文字"""
    try:
        return page.evaluate("""
            () => {
                const selectors = [
                    '.article-content', '.cv-content', '#read-article-holder',
                    '.article-holder', '.read-content', 'article',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.textContent.trim().length > 100) {
                        return el.textContent.trim();
                    }
                }
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


class BilibiliArticleCollector(BaseCollector):
    """通过 UP主空间 API 采集最近文章 + 动态（7 天内）"""

    def __init__(self):
        super().__init__("BilibiliArticle")
        self._seen_ids: set[str] = set()
        self._page = None
        self._external_page = False

    def set_page(self, page):
        """注入外部 Playwright page（共享浏览器实例）"""
        self._page = page
        self._external_page = True

    # ========== 专栏文章 API ==========

    def _fetch_user_articles(self, mid: int, account_name: str, cat_hint: str) -> list[dict]:
        """通过 B站空间 API 获取用户的专栏文章列表"""
        api_url = (
            f"https://api.bilibili.com/x/space/article"
            f"?mid={mid}&pn=1&ps={MAX_PER_ACCOUNT}"
        )

        try:
            raw = self._page.evaluate(f"""
                async () => {{
                    try {{
                        const r = await fetch({json.dumps(api_url)});
                        const j = await r.json();
                        if (j.code !== 0 || !j.data?.articles) return [];
                        return j.data.articles.map(a => ({{
                            id: a.id || 0,
                            title: a.title || '',
                            summary: (a.summary || '').substring(0, 300),
                            publish_time: a.publish_time || 0,
                            view: a.stats?.view || a.view || 0,
                            like: a.stats?.like || a.like || 0,
                            category: a.category?.name || '',
                        }}));
                    }} catch(e) {{ return []; }}
                }}
            """)
            result = raw if isinstance(raw, list) else []
        except Exception:
            return []

        articles = []
        for a in result:
            cvid = a.get("id", 0)
            if not cvid:
                continue
            dedup_key = f"cv{cvid}"
            if dedup_key in self._seen_ids:
                continue
            self._seen_ids.add(dedup_key)

            published_at = _parse_unix_timestamp(a.get("publish_time", 0))

            # 7 天窗口过滤
            if published_at and published_at < CUTOFF_DATE:
                continue

            articles.append({
                "title": a.get("title", "").strip(),
                "url": f"https://www.bilibili.com/read/cv{cvid}",
                "cv_id": cvid,
                "author": account_name,
                "summary": a.get("summary", ""),
                "published_at": published_at,
                "view_count": a.get("view", 0),
                "like_count": a.get("like", 0),
                "category_hint": cat_hint,
                "source_type": "bilibili_article",
                "source_label": f"B站专栏@{account_name}",
            })

        return articles

    # ========== 动态 API ==========

    def _fetch_user_dynamics(self, mid: int, account_name: str, cat_hint: str) -> list[dict]:
        """通过 B站动态 API 获取用户最近的文字动态（MAJOR_TYPE_OPUS）"""
        api_url = (
            f"https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
            f"?host_mid={mid}"
        )

        try:
            raw = self._page.evaluate(f"""
                async () => {{
                    try {{
                        const r = await fetch({json.dumps(api_url)});
                        const j = await r.json();
                        if (j.code !== 0 || !j.data?.items) return [];
                        return j.data.items.slice(0, {MAX_PER_ACCOUNT}).map(item => {{
                            const mod = item.modules || {{}};
                            const author = mod.module_author || {{}};
                            const dyn = mod.module_dynamic || {{}};
                            const major = dyn.major || {{}};
                            const mtype = major.type || '';

                            let text = '';
                            let title = '';

                            if (mtype === 'MAJOR_TYPE_OPUS' && major.opus) {{
                                title = (major.opus.title || '').substring(0, 100);
                                text = (major.opus.summary?.text || '').substring(0, 500);
                            }} else if (mtype === 'MAJOR_TYPE_ARTICLE' && major.article) {{
                                title = (major.article.title || '').substring(0, 100);
                                text = (major.article.desc || '').substring(0, 500);
                            }}

                            if (!text && !title) return null;

                            return {{
                                id_str: item.id_str || '',
                                title: title,
                                text: text,
                                type: mtype,
                                pub_ts: author.pub_ts || 0,
                                name: author.name || '',
                            }};
                        }}).filter(Boolean);
                    }} catch(e) {{ return []; }}
                }}
            """)
            result = raw if isinstance(raw, list) else []
        except Exception:
            return []

        dynamics = []
        for d in result:
            if not d:
                continue
            id_str = d.get("id_str", "")
            if not id_str:
                continue
            if id_str in self._seen_ids:
                continue
            self._seen_ids.add(id_str)

            published_at = _parse_unix_timestamp(d.get("pub_ts", 0))

            # 7 天窗口过滤
            if published_at and published_at < CUTOFF_DATE:
                continue

            title = d.get("title", "")
            text = d.get("text", "")
            display_title = title if title else (text[:80] + "..." if len(text) > 80 else text)

            dynamics.append({
                "title": display_title,
                "url": f"https://t.bilibili.com/{id_str}",
                "author": account_name,
                "summary": text[:300],
                "published_at": published_at,
                "view_count": 0,
                "like_count": 0,
                "category_hint": cat_hint,
                "source_type": "bilibili_dynamic",
                "source_label": f"B站动态@{account_name}",
            })

        return dynamics

    # ========== 全文抓取 ==========

    def _fetch_article_content(self, cv_id: int) -> str:
        """抓取专栏全文 — 先尝试 API，失败则页面抓取"""
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
                            let text = '';
                            if (d.content) {{
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

    # ========== 主流程 ==========

    def fetch(self) -> list[dict]:
        if os.getenv("BILIBILI_BROWSER", "").lower() not in ("1", "true", "yes"):
            console.log("[dim]B站文章采集已跳过 (设置 BILIBILI_BROWSER=true 启用)[/dim]")
            return []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            console.log("[red]playwright 未安装，跳过 B站文章采集[/red]")
            return []

        total_accounts = len(ALL_TARGET_ACCOUNTS)
        console.print(
            f"\n[yellow]B站文章+动态采集: {total_accounts} 个 UP 主 "
            f"({len(MANUFACTURER_ACCOUNTS)} 厂商 + {len(NEWS_UP_ACCOUNTS)} 资讯UP)[/yellow]"
        )

        if self._page is not None:
            return self._do_fetch()

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

            try:
                page.goto("https://www.bilibili.com", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
            except Exception:
                pass

            result = self._do_fetch()
            browser.close()
            if not self._external_page:
                self._page = None
            return result

    def _do_fetch(self) -> list[dict]:
        """对每个 UP 主拉取专栏 + 动态，按时间倒序，7 天内过滤"""
        all_entries = []

        for acct_name, acct_info in ALL_TARGET_ACCOUNTS.items():
            mid = acct_info["mid"]
            cat_hint = acct_info["category"]

            try:
                articles = self._fetch_user_articles(mid, acct_name, cat_hint)
                all_entries.extend(articles)
                if articles:
                    console.log(
                        f"[dim]  {acct_name} 专栏: {len(articles)} 篇[/dim]"
                    )
            except Exception as e:
                console.log(f"[red]  专栏获取失败 '{acct_name}': {e}[/red]")

            time.sleep(random.uniform(FETCH_DELAY_MIN, FETCH_DELAY_MAX))

            try:
                dynamics = self._fetch_user_dynamics(mid, acct_name, cat_hint)
                all_entries.extend(dynamics)
                if dynamics:
                    console.log(
                        f"[dim]  {acct_name} 动态: {len(dynamics)} 条[/dim]"
                    )
            except Exception as e:
                console.log(f"[red]  动态获取失败 '{acct_name}': {e}[/red]")

            time.sleep(random.uniform(FETCH_DELAY_MIN, FETCH_DELAY_MAX))

        # 按发布时间倒序
        all_entries.sort(
            key=lambda x: x.get("published_at") or datetime(2000, 1, 1, tzinfo=timezone.utc),
            reverse=True,
        )

        # 统计
        with_date = sum(1 for e in all_entries if e.get("published_at"))
        console.log(
            f"[dim]  共 {len(all_entries)} 条 (专栏+动态)，"
            f"其中 {with_date} 条有日期[/dim]"
        )

        # 抓取专栏全文（仅前 50 篇专栏，动态不需要）
        articles_only = [e for e in all_entries if e.get("source_type") == "bilibili_article"]
        to_fetch = articles_only[:50]
        if to_fetch:
            console.log(f"\n[yellow]  抓取专栏全文: {len(to_fetch)} 篇[/yellow]")
            for entry in to_fetch:
                try:
                    content = self._fetch_article_content(entry["cv_id"])
                    entry["content"] = content
                    if content:
                        console.log(
                            f"[dim]    全文 {len(content)} 字: {entry['title'][:40]}[/dim]"
                        )
                except Exception as e:
                    entry["content"] = ""
                    console.log(f"[red]    抓取失败 cv{entry.get('cv_id')}: {e}[/red]")
                time.sleep(random.uniform(1, 2))

        # 标准化
        items = []
        for entry in all_entries:
            content = entry.get("content", "")
            summary_parts = [f"UP主: {entry['author']}"]
            if entry.get("view_count"):
                label = "阅读" if entry.get("source_type") == "bilibili_article" else ""
                if label:
                    summary_parts.append(f"{label}: {entry['view_count']}")
            if content:
                summary_parts.append(f"正文({len(content)}字): {content[:300]}")
            elif entry.get("summary"):
                summary_parts.append(entry["summary"][:300])

            raw_data = {
                "author": entry["author"],
                "view_count": entry.get("view_count", 0),
                "like_count": entry.get("like_count", 0),
                "content_length": len(content),
                "source_type": entry.get("source_type", "bilibili_article"),
            }
            if entry.get("cv_id"):
                raw_data["cv_id"] = entry["cv_id"]

            item = self.normalize_item(
                title=entry["title"],
                url=entry["url"],
                source_name=entry.get("source_label", f"B站@{entry['author']}"),
                source_type=entry.get("source_type", "bilibili_article"),
                published_at=entry.get("published_at"),
                summary=" | ".join(summary_parts),
                raw_data=raw_data,
            )
            item["category"] = entry["category_hint"]
            items.append(item)

        console.log(f"[green]B站文章+动态总计: {len(items)} 条[/green]")
        return items
