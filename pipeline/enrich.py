"""交叉来源信息补全：短摘要 RSS → B站 搜索补充

问题：Retro Dodo 等 RSS 源摘要简短（500 字内），缺少关键细节（CPU/价格等）
方案：对短摘要条目，用产品名搜索 B站，取高播放量结果补全
"""

import os
import random
import re
import time
from urllib.parse import quote

import requests
from rich.console import Console

from pipeline.device_os_map import DEVICE_CATEGORY_MAP

console = Console()

ENRICH_THRESHOLD = 300   # 摘要少于此字数触发补全
MAX_ENRICH_ITEMS = 30    # 每次最多补全条数
SEARCH_DELAY = (2, 4)    # B站 搜索间隔


def _extract_product_name(title: str) -> str | None:
    """从标题提取最长的已知产品名"""
    lower = title.lower()
    sorted_devices = sorted([d for d in DEVICE_CATEGORY_MAP if len(d) > 4],
                            key=len, reverse=True)
    for device in sorted_devices:
        if re.search(r'\b' + re.escape(device) + r'\b', lower):
            return device
    return None


def _search_bilibili(query: str, sessdata: str = "") -> str | None:
    """B站 搜索 API，返回第一条结果的描述"""
    if not sessdata:
        sessdata = os.getenv("BILIBILI_SESSDATA", "").strip()
    url = (
        f"https://api.bilibili.com/x/web-interface/search/type"
        f"?search_type=video&keyword={quote(query)}&page=1&order=pubdate"
    )
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com"}
        if sessdata:
            headers["Cookie"] = f"SESSDATA={sessdata}"
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        if data.get("code") == 0 and data.get("data", {}).get("result"):
            results = data["data"]["result"]
            if results:
                top = results[0]
                title = top.get("title", "")
                desc = top.get("description", "") or ""
                author = top.get("author", "")
                play = top.get("play", 0)
                parts = []
                if title:
                    parts.append(title)
                if desc and len(desc) > 20:
                    parts.append(desc[:200])
                if author:
                    parts.append(f"UP: {author}")
                if play > 0:
                    parts.append(f"播放:{play}")
                return " | ".join(parts)
    except Exception:
        pass
    return None


# 搜索词中的事件关键词 — 从动态短文本中提取，防止搜出旧信息
_EVENT_KWS = re.compile(
    r"(补货|第二轮|第.\s*轮|开售|预售|官宣|正式发布|现已|开订|"
    r"上架|新品|限定|联名|新色|降价|调价|售罄|断货|现货)"
)


def _extract_event_kw(text: str) -> str:
    """从动态文本中提取事件关键词，用于精确搜索"""
    m = _EVENT_KWS.search(text)
    return m.group(0) if m else ""


def enrich_thin_items(items: list[dict]) -> int:
    """为摘要过短的条目搜索 B站 补全信息

    返回补全成功的条数
    """
    candidates = []
    for it in items:
        summary = it.get("summary", "")
        if len(summary) >= ENRICH_THRESHOLD:
            continue
        title = it.get("title", "")
        product = _extract_product_name(title)
        if not product:
            continue
        # RSS + B站官方动态/厂商内容
        src = it.get("source_type", "")
        if src not in ("rss", "rss_cn", "chinese_web",
                       "bilibili_manufacturer", "bilibili_dynamic"):
            continue
        candidates.append((it, product, src))

    if not candidates:
        return 0

    capped = candidates[:MAX_ENRICH_ITEMS]
    console.log(f"[dim]  交叉补全 {len(capped)} 条短摘要 (B站 搜索)...[/dim]")
    enriched = 0
    from difflib import SequenceMatcher
    for it, product, src in capped:
        # 拼搜索词: 产品名 + 事件关键词 → 防止张冠李戴
        event_kw = _extract_event_kw(
            (it.get("title") or "") + " " + (it.get("summary") or "")
        )
        query = f"{product} {event_kw}" if event_kw else product

        extra = _search_bilibili(query)
        if extra:
            # 防跑偏：B站 结果标题与原标题需有一定相似度
            extra_title = extra.split(" | ")[0] if " | " in extra else extra[:60]
            sim = SequenceMatcher(None,
                it.get("title", "")[:80].lower(),
                extra_title.lower()
            ).ratio()
            if sim < 0.15:
                continue
            prefix = "[识图补全]" if src in ("bilibili_manufacturer", "bilibili_dynamic") else "[B站补全]"
            it["summary"] = f"{it['summary']} | {prefix} {extra}"
            enriched += 1
        time.sleep(random.uniform(*SEARCH_DELAY))

    if enriched:
        console.log(f"[green]  补全 {enriched}/{len(capped)} 条信息[/green]")
    return enriched
