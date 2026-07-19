"""预告→跟进闭环：从 leak 条目提取产品名，驱动针对性补充搜索

集成 leak_db：存储未来泄漏信号，下次采集时自动检索并补充搜索。
"""

import re
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from rich.console import Console

from config import PRODUCT_NAME_PATTERNS, CUTOFF_DATE

console = Console()


def get_stored_product_names() -> list[str]:
    """从泄漏信号数据库获取当前应关注的 pending 产品名"""
    try:
        from storage.leak_db import get_pending_signals, expire_old
        expire_old()
        signals = get_pending_signals()
        if signals:
            names = [s["product_name"] for s in signals]
            console.log(f"[dim]leak_db: {len(signals)} 个待跟进信号"
                        f" (最旧: {signals[-1]['first_seen_at'][:10]})[/dim]")
            return names
    except Exception as e:
        console.log(f"[yellow]leak_db 读取失败: {e}[/yellow]")
    return []


def store_leak_signals(leak_items: list[dict]) -> int:
    """将 leak 条目的产品名存入数据库，供后续跟进"""
    try:
        from storage.leak_db import upsert_signals
        from datetime import datetime as dt
        names = extract_product_names(leak_items)
        if not names:
            return 0
        now = dt.now(timezone.utc).isoformat()
        signals = [
            {
                "product_name": name,
                "source_title": leak_items[0].get("display_title",
                                                   leak_items[0].get("title", ""))[:200],
                "source_url": leak_items[0].get("url", ""),
                "first_seen_at": now,
            }
            for name in names
        ]
        return upsert_signals(signals)
    except Exception as e:
        console.log(f"[yellow]leak_db 写入失败: {e}[/yellow]")
        return 0


def mark_signals_found(found_items: list[dict]) -> int:
    """标记补充搜索结果的对应信号为 found"""
    try:
        from storage.leak_db import mark_found, get_stats
        from pipeline.leak_followup import extract_product_names
        names = extract_product_names(found_items)
        if names:
            count = mark_found(names)
            if count:
                stats = get_stats()
                console.log(f"[dim]leak_db: {count} 个信号→found "
                            f"(待跟进: {stats.get('pending', 0)}, 已找到: {stats.get('found', 0)})[/dim]")
            return count
    except Exception as e:
        console.log(f"[yellow]leak_db 标记失败: {e}[/yellow]")
    return 0


def extract_product_names(items: list[dict]) -> list[str]:
    """从 leak 条目中提取产品名，去重，按原文出现频率排序"""
    seen = {}
    for item in items:
        title = item.get("display_title", item.get("title", ""))
        summary = item.get("summary", "")
        text = f"{title} {summary}"
        for pattern in PRODUCT_NAME_PATTERNS:
            for match in pattern.finditer(text):
                name = match.group(0).strip()
                # 过滤太短或纯数字
                if len(name) < 3 or name.isdigit():
                    continue
                # 过滤过于通用的词
                if name.lower() in ("windows", "android", "linux", "switch", "playstation",
                                   "xbox", "nintendo", "sony", "steam", "掌机", "主机", "新品",
                                   "pro", "lite", "oled", "mini", "plus"):
                    continue
                seen[name] = seen.get(name, 0) + 1

    return sorted(seen, key=seen.get, reverse=True)


def search_google_news_rss(query: str, max_results: int = 5) -> list[dict]:
    """Google News RSS 补充搜索"""
    results = []
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    try:
        import feedparser
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; GamingNewsBot/1.0)"
        })
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        for entry in feed.entries[:max_results]:
            title = getattr(entry, "title", "").strip()
            title = title.split(" - ")[0]
            link = getattr(entry, "link", "")
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(link)
            real_url = parse_qs(parsed.query).get("url", [link])[0]

            pub_date = None
            tp = getattr(entry, "published_parsed", None)
            if tp:
                try:
                    pub_date = datetime(*tp[:6], tzinfo=timezone.utc)
                except Exception:
                    pass

            results.append({
                "title": title,
                "url": real_url,
                "summary": "",
                "source_name": f"Google News (via {query[:20]})",
                "published_at": pub_date.isoformat() if pub_date else None,
            })
    except Exception as e:
        console.log(f"[dim]泄漏跟进 Google搜索失败 [{query[:30]}]: {e}[/dim]")
    return results


def supplement_search(leak_items: list[dict], browser_page=None) -> list[dict]:
    """基于 leak 条目 + DB 历史信号的产品名执行补充搜索，返回新条目

    Args:
        leak_items: sub_type=leak 且在扩展窗口内的条目
        browser_page: Playwright page（可选，用于 B站搜索）

    Returns:
        补充搜索到的新条目列表
    """
    # 合并当前 leak 提取 + DB 历史信号
    current_names = extract_product_names(leak_items) if leak_items else []
    db_names = get_stored_product_names()

    # 去重，当前 leak 优先
    product_names = list(dict.fromkeys(current_names + db_names))

    if not product_names:
        console.log("[dim]无产品名（当前+DB），跳过补充搜索[/dim]")
        return []

    if current_names:
        console.log(f"[yellow]  当前 leak: {len(current_names)} 个产品名[/yellow]")
    if db_names:
        console.log(f"[yellow]  DB 历史: {len(db_names)} 个待跟进信号[/yellow]")
    console.log(f"  [dim]合并去重: {len(product_names)} 个[/dim]")

    all_new = []
    seen_urls = set()

    for name in product_names[:10]:  # 最多搜索 10 个产品名
        # Google News RSS
        for r in search_google_news_rss(name, max_results=3):
            url = r.get("url", "")
            if not url or url in seen_urls:
                continue
            pub = r.get("published_at")
            if pub:
                try:
                    if datetime.fromisoformat(pub) < CUTOFF_DATE:
                        continue
                except Exception:
                    pass
            seen_urls.add(url)
            r["_leak_followup"] = True
            all_new.append(r)

        # B站搜索（如果有浏览器实例）
        if browser_page:
            bilibili_items = _search_bilibili_product(browser_page, name)
            for item in bilibili_items:
                url = item.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                item["_leak_followup"] = True
                all_new.append(item)

    if all_new:
        console.log(f"[green]  补充搜索获得 {len(all_new)} 条新结果[/green]")
    else:
        console.log("[dim]  补充搜索无新结果[/dim]")

    return all_new


def _search_bilibili_product(page, product_name: str, max_results: int = 3) -> list[dict]:
    """通过 Playwright 浏览器搜索 B站 产品名"""
    import json as _json
    try:
        api_url = (
            "https://api.bilibili.com/x/web-interface/search/type"
            f"?search_type=video&keyword={quote(product_name)}&page=1&order=pubdate"
        )
        raw = page.evaluate(f"""async () => {{
            try {{
                const r = await fetch({_json.dumps(api_url)});
                const j = await r.json();
                if (j.code !== 0 || !j.data?.result) return [];
                return j.data.result.slice(0, {max_results});
            }} catch(e) {{ return []; }}
        }}""")
        result = raw if isinstance(raw, list) else []
    except Exception:
        return []

    items = []
    for v in result:
        title = v.get("title", "").strip()
        bvid = v.get("bvid", "")
        if not title or not bvid:
            continue
        url = f"https://www.bilibili.com/video/{bvid}"
        author = v.get("author", "")
        description = (v.get("description", "") or "").strip()[:200]
        pubdate = v.get("pubdate", 0)
        pub_dt = datetime.fromtimestamp(pubdate, tz=timezone.utc) if pubdate > 0 else None

        items.append({
            "title": title,
            "url": url,
            "summary": f"UP主: {author} | {description}",
            "source_name": f"B站补充搜索 (via {product_name})",
            "published_at": pub_dt.isoformat() if pub_dt else None,
            "raw_data": {
                "author": author,
                "bvid": bvid,
                "play": v.get("play", 0),
                "_leak_followup": True,
            },
        })

    return items
