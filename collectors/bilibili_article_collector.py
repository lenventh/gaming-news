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

# SESSDATA 过期标记文件路径（CI 检测后自动创建 Issue 提醒）
_SESSDATA_EXPIRED_FLAG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "sessdata_expired.flag")


def _mark_sessdata_expired(reason: str = ""):
    """写标记文件，CI 检测到后会创建 GitHub Issue 提醒更新"""
    try:
        with open(_SESSDATA_EXPIRED_FLAG, "w", encoding="utf-8") as f:
            f.write(f"SESSDATA 已过期\n原因: {reason}\n时间: {datetime.now(timezone.utc).isoformat()}\n")
        console.log(f"[yellow]  已写入过期标记: {_SESSDATA_EXPIRED_FLAG}[/yellow]")
    except Exception as e:
        console.log(f"[yellow]  写入过期标记失败: {e}[/yellow]")

MAX_PER_ACCOUNT = 30          # 每个 UP 主最多拉几条（二柄/游民 ~10条/天）
MAX_ARTICLE_CONTENT_LENGTH = 2000
MAX_RECOGNITION_IMAGES = 3    # 每条 DRAW 动态最多识别的图片数
FETCH_DELAY_MIN = 2
FETCH_DELAY_MAX = 4


def _set_bilibili_cookies(context, page=None) -> bool:
    """把 BILIBILI_SESSDATA 等 Cookie 注入浏览器上下文，返回是否有效。

    B站 polymer 动态 API 需要登录态，否则返回登录页 HTML。
    从环境变量读取 SESSDATA（.env 中配置）。

    如果提供 page，注入后调用 nav API 验证 isLogin。
    返回 True=SESSDATA有效, False=无效或未设置。
    """
    sessdata = os.getenv("BILIBILI_SESSDATA", "").strip()
    if not sessdata:
        console.log("[dim]未检测到 BILIBILI_SESSDATA，专栏/动态 API 可能无法访问[/dim]")
        return False

    context.add_cookies([
        {
            "name": "SESSDATA",
            "value": sessdata,
            "domain": ".bilibili.com",
            "path": "/",
        },
    ])
    console.log("[dim]B站 Cookie 已注入 (SESSDATA)[/dim]")

    # 验证 SESSDATA 是否有效
    if page is not None:
        try:
            import json as _json
            page.goto("https://api.bilibili.com/x/web-interface/nav",
                      wait_until="domcontentloaded", timeout=10000)
            raw = page.evaluate("() => document.body.textContent")
            data = _json.loads(raw)
            is_login = data.get("data", {}).get("isLogin", False)
            uname = data.get("data", {}).get("uname", "?")
            if is_login:
                console.log(f"[green]  ✓ SESSDATA 有效 (已登录: {uname})[/green]")
            else:
                console.log(
                    f"[yellow]  ⚠ SESSDATA 可能已过期 (nav isLogin={is_login}), "
                    "请重新提取: python extract_sessdata.py[/yellow]"
                )
                _mark_sessdata_expired(f"nav API isLogin={is_login}")
            return is_login
        except Exception as e:
            console.log(f"[yellow]  ⚠ SESSDATA 验证失败: {e}[/yellow]")
    return True  # 无法验证时默认为有效

# 合并所有目标账号
ALL_TARGET_ACCOUNTS = {}
ALL_TARGET_ACCOUNTS.update(MANUFACTURER_ACCOUNTS)
ALL_TARGET_ACCOUNTS.update(NEWS_UP_ACCOUNTS)


def _parse_unix_timestamp(ts) -> datetime | None:
    """Unix 时间戳 → datetime（兼容 int 和 str 类型）"""
    if ts:
        try:
            if isinstance(ts, str):
                ts = int(ts)
            if ts > 0:
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
        """通过 B站空间 API 获取用户的专栏文章列表（用 page.goto 避免 fetch 风控）"""
        api_url = (
            f"https://api.bilibili.com/x/space/article"
            f"?mid={mid}&pn=1&ps={MAX_PER_ACCOUNT}"
        )

        try:
            self._page.goto(api_url, wait_until="domcontentloaded", timeout=10000)
            raw_body = self._page.evaluate("() => document.body.textContent")
            import json as _json
            data = _json.loads(raw_body)
            if data.get("code") != 0 or not data.get("data", {}).get("articles"):
                return []
            result = data["data"]["articles"]
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

            stats = a.get("stats", {}) or {}
            articles.append({
                "title": a.get("title", "").strip(),
                "url": f"https://www.bilibili.com/read/cv{cvid}",
                "cv_id": cvid,
                "author": account_name,
                "summary": a.get("summary", ""),
                "published_at": published_at,
                "view_count": stats.get("view", 0),
                "like_count": stats.get("like", 0),
                "category_hint": cat_hint,
                "source_type": "bilibili_article",
                "source_label": f"B站专栏@{account_name}",
            })

        return articles

    # ========== 动态 API ==========

    def _fetch_user_dynamics(self, mid: int, account_name: str, cat_hint: str) -> list[dict]:
        """通过 B站动态 API 获取用户最近的图文/视频动态

        直接 navigate 到 API URL 读取 JSON（比 page.evaluate(fetch()) 更可靠，
        fetch 跨域调用时可能因 cookie/origin 检查返回登录页 HTML）。

        处理类型: OPUS(文字帖) / ARTICLE(文章分享) / DRAW(图片帖) / ARCHIVE(视频分享)
        """
        api_url = (
            f"https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
            f"?host_mid={mid}"
        )

        try:
            self._page.goto(api_url, wait_until="domcontentloaded", timeout=10000)
            raw_body = self._page.evaluate("() => document.body.textContent")
            import json
            data = json.loads(raw_body)
            if data.get("code") != 0 or not data.get("data", {}).get("items"):
                return []
            items = data["data"]["items"]
        except Exception:
            return []

        dynamics = []
        for item in items[:MAX_PER_ACCOUNT]:
            mod = item.get("modules") or {}
            author = mod.get("module_author") or {}
            dyn = mod.get("module_dynamic") or {}
            major = dyn.get("major") or {}
            mtype = major.get("type") or ""

            text = ""
            title = ""
            images = []

            if mtype == "MAJOR_TYPE_OPUS" and major.get("opus"):
                opus = major["opus"]
                title = (opus.get("title") or "").strip()[:100]
                text = (opus.get("summary", {}).get("text") or "").strip()[:800]
                pics = opus.get("pics") or []
                images = [p.get("url", "") for p in pics[:MAX_RECOGNITION_IMAGES] if p.get("url")]
            elif mtype == "MAJOR_TYPE_ARTICLE" and major.get("article"):
                article = major["article"]
                title = (article.get("title") or "").strip()[:100]
                text = (article.get("desc") or "").strip()[:800]
            elif mtype == "MAJOR_TYPE_DRAW" and major.get("draw"):
                draw = major["draw"]
                title = (draw.get("title") or "").strip()[:100]
                text = (draw.get("desc") or "").strip()[:800]
                draw_items = draw.get("items") or []
                images = [img.get("src", "") for img in draw_items[:MAX_RECOGNITION_IMAGES] if img.get("src")]
            elif mtype == "MAJOR_TYPE_ARCHIVE" and major.get("archive"):
                archive = major["archive"]
                title = (archive.get("title") or "").strip()[:100]
                text = (archive.get("desc") or archive.get("dynamic") or "").strip()[:800]
                cover = archive.get("cover") or ""
                if cover:
                    images = [cover]

            # DRAW 即使没文字，有图就保留
            if not text and not title and not images:
                continue

            id_str = item.get("id_str", "")
            if not id_str:
                continue
            if id_str in self._seen_ids:
                continue
            self._seen_ids.add(id_str)

            published_at = _parse_unix_timestamp(author.get("pub_ts", 0))

            # 7 天窗口过滤
            if published_at and published_at < CUTOFF_DATE:
                continue

            # 多模态识图：DRAW 无文字 / 任何类型文字极短(<30字)且带图时触发
            needs_vision = (
                (mtype == "MAJOR_TYPE_DRAW" and images and not text and not title)
                or (images and len((text or "") + (title or "")) < 30)
            )
            if needs_vision:
                recognized = self._recognize_images(images, account_name)
                if recognized:
                    if text:
                        text = text + "\n" + recognized
                    else:
                        text = recognized
                    if not title or len(title) < 10:
                        title = recognized[:80] + "..." if len(recognized) > 80 else recognized
                elif not text and not title:
                    title = f"[图片动态] {account_name} ({len(images)}张图)"

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
                "images": images,
            })

        return dynamics

    # ========== 多模态识图 ==========

    def _recognize_images(self, image_urls: list[str], account_name: str) -> str:
        """用多模态 LLM 识别图片内容，返回文字描述。

        需要支持 vision 的模型（如 gpt-4o, claude, qwen-vl 等）。
        deepseek-chat 不支持图片，需通过 OPENAI_VISION_MODEL 环境变量指定。
        """
        if os.getenv("BILIBILI_IMAGE_RECOGNITION", "").lower() not in ("1", "true", "yes"):
            return ""

        try:
            from config import OPENAI_API_KEY, OPENAI_BASE_URL
            from openai import OpenAI

            if not OPENAI_API_KEY or OPENAI_API_KEY == "sk-xxx":
                return ""

            vision_model = os.getenv("OPENAI_VISION_MODEL", "").strip()
            if not vision_model:
                console.log(
                    "[dim]    未设置 OPENAI_VISION_MODEL，跳过识图 "
                    "(当前文本模型不支持图片)[/dim]"
                )
                return ""

            vision_base_url = os.getenv("OPENAI_VISION_BASE_URL", "").strip() or OPENAI_BASE_URL
            vision_api_key = os.getenv("OPENAI_VISION_API_KEY", "").strip() or OPENAI_API_KEY

            client = OpenAI(api_key=vision_api_key, base_url=vision_base_url)

            # 最多识别 3 张图
            image_contents = []
            for url in image_urls[:MAX_RECOGNITION_IMAGES]:
                if not url:
                    continue
                if url.startswith("//"):
                    url = "https:" + url
                image_contents.append({
                    "type": "image_url",
                    "image_url": {"url": url},
                })

            if not image_contents:
                return ""

            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是游戏设备资讯识别助手。识别图片中的内容，提取与游戏掌机、"
                        "游戏主机、游戏设备相关的信息。重点关注：产品发布/预告、规格参数、"
                        "发售日期、价格、新功能、限量版/联名款。"
                        "用中文简洁描述，不超过200字。直接描述内容，不要加'图中显示'等前缀。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"识别以下来自 {account_name} 的图片内容："},
                        *image_contents,
                    ],
                },
            ]

            resp = client.chat.completions.create(
                model=vision_model,
                messages=messages,
                max_tokens=300,
                temperature=0.3,
            )
            result = resp.choices[0].message.content
            if result:
                console.log(
                    f"[dim]    识图 {account_name} ({len(image_contents)}张): "
                    f"{result[:60]}...[/dim]"
                )
                return result.strip()
        except Exception as e:
            err_msg = str(e).lower()
            if "file size is too large" in err_msg or "too large" in err_msg:
                console.log(f"[dim]    识图跳过 {account_name}: 图片文件过大[/dim]")
            else:
                console.log(f"[dim]    识图失败 {account_name}: {e}[/dim]")

        return ""

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
            _set_bilibili_cookies(self._page.context, self._page)
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

            # 先 warmup 获取游客 Cookie，再注入 SESSDATA（避免 warmup 覆盖登录态）
            try:
                page.goto("https://www.bilibili.com", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
            except Exception:
                pass
            _set_bilibili_cookies(context, page)

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

        # SESSDATA 有效性检测
        if len(all_entries) == 0:
            sessdata = os.getenv("BILIBILI_SESSDATA", "").strip()
            if sessdata:
                console.log(
                    "[yellow]  ⚠ 专栏+动态 0 条 — BILIBILI_SESSDATA 可能已过期, "
                    "请重新提取: python extract_sessdata.py[/yellow]"
                )
                _mark_sessdata_expired("专栏+动态采集量为 0")
            else:
                console.log(
                    "[yellow]  ⚠ 专栏+动态 0 条 — 未设置 BILIBILI_SESSDATA, "
                    "仅公开账号可用。配置后效果提升 5x+[/yellow]"
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
            if entry.get("images"):
                raw_data["images"] = entry["images"]

            item = self.normalize_item(
                title=entry["title"],
                url=entry["url"],
                source_name=entry.get("source_label", f"B站@{entry['author']}"),
                source_type=entry.get("source_type", "bilibili_article"),
                published_at=entry.get("published_at"),
                summary=" | ".join(summary_parts),
                raw_data=raw_data,
            )
            # 动态图片带入 image_url 供视频工作流使用
            if entry.get("images") and not item.get("image_url"):
                item["image_url"] = entry["images"][0]
            item["category"] = entry["category_hint"]
            items.append(item)

        console.log(f"[green]B站文章+动态总计: {len(items)} 条[/green]")
        return items
