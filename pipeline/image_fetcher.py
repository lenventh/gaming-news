"""配图抓取：从来源页面提取 og:image，并发请求避免阻塞"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from rich.console import Console

console = Console()

FETCH_TIMEOUT = 5
MAX_WORKERS = 5

# 已知占位图特征
PLACEHOLDER_PATTERNS = [
    # IT之家 透明跟踪像素
    re.compile(r"img\.ithome\.com/images/v2/t\.png", re.IGNORECASE),
    # 常见占位图路径
    re.compile(r"/placeholder", re.IGNORECASE),
    re.compile(r"/transparent", re.IGNORECASE),
    re.compile(r"1x1\.(png|gif)", re.IGNORECASE),
    re.compile(r"/spacer\.(png|gif)", re.IGNORECASE),
    # WordPress 默认灰色占位图
    re.compile(r"wp-includes/images/blank", re.IGNORECASE),
]

# 同一图片 URL 出现在 ≥N 个不同来源时视为占位图
PLACEHOLDER_DEDUP_THRESHOLD = 3


def _is_placeholder(url: str) -> bool:
    """检测已知占位图域名/路径"""
    for pat in PLACEHOLDER_PATTERNS:
        if pat.search(url):
            return True
    return False


def _extract_image_from_page(url: str) -> str | None:
    """从目标页面提取主图 URL（og:image → twitter:image → schema.org → 首张大图）"""
    try:
        resp = requests.get(url, timeout=FETCH_TIMEOUT, headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; GamingNewsBot/1.0; "
                "+https://github.com/lenventh/gaming-news)"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }, allow_redirects=True)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # 1. og:image
        meta = soup.find("meta", property="og:image")
        if meta and meta.get("content"):
            return meta["content"]

        # 2. twitter:image
        meta = soup.find("meta", attrs={"name": "twitter:image"})
        if meta and meta.get("content"):
            return meta["content"]

        # 3. schema.org JSON-LD image
        for tag in soup.find_all("script", type="application/ld+json"):
            if not tag.string:
                continue
            try:
                data = json.loads(tag.string)
                if isinstance(data, dict) and "image" in data:
                    img = data["image"]
                    if isinstance(img, list) and img:
                        return img[0]
                    elif isinstance(img, str):
                        return img
                elif isinstance(data, list):
                    for d in data:
                        if isinstance(d, dict) and "image" in d:
                            img = d["image"]
                            if isinstance(img, list) and img:
                                return img[0]
                            elif isinstance(img, str):
                                return img
            except (json.JSONDecodeError, AttributeError):
                pass

        # 4. 首张 > 200px 的图片
        for img_tag in soup.find_all("img", src=True):
            src = img_tag["src"]
            if any(skip in src.lower() for skip in ("avatar", "icon", "logo", "pixel", "tracking")):
                continue
            width = img_tag.get("width")
            if width and (isinstance(width, str) and width.isdigit()):
                if int(width) >= 200:
                    return src
            return src  # 没有 width 属性也先返回第一个

        return None
    except requests.Timeout:
        return None
    except Exception:
        return None


def fetch_images(selected: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """并发抓取所有条目的配图，经过占位图过滤后写入 item['image_url']"""
    # 收集所有需要抓图的条目
    tasks: list[tuple[str, int, str]] = []  # (cat_key, index, source_url)
    for cat_key, items in selected.items():
        for i, it in enumerate(items):
            if it.get("image_url"):
                continue
            url = it.get("url", "")
            if url:
                tasks.append((cat_key, i, url))

    if not tasks:
        console.print("[dim]没有需要抓图的条目[/dim]")
        return selected

    console.print(f"\n[yellow]  🖼  配图抓取: {len(tasks)} 条，并发 {MAX_WORKERS} 线程...[/yellow]")

    # 记录：(cat_key, idx, 图片url, 来源url)
    results: list[tuple[str, int, str, str]] = []

    def _fetch_one(cat_key: str, idx: int, source_url: str):
        img = _extract_image_from_page(source_url)
        return cat_key, idx, img, source_url

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, c, i, u): (c, i, u) for c, i, u in tasks}
        for future in as_completed(futures):
            try:
                cat_key, idx, img, src_url = future.result()
                if img:
                    results.append((cat_key, idx, img, src_url))
            except Exception:
                pass

    # ===== 过滤阶段 =====
    raw_count = len(results)

    # 过滤 1：已知占位图模式
    pattern_rejected = 0
    valid: list[tuple[str, int, str, str]] = []
    for cat_key, idx, img, src_url in results:
        if _is_placeholder(img):
            pattern_rejected += 1
            console.log(f"[yellow]    ✗ 占位图: {img[:60]}[/yellow]")
        else:
            valid.append((cat_key, idx, img, src_url))

    # 过滤 2：同一图片 URL 出现 ≥N 次（不同来源）视为通用占位图
    dedup_rejected = 0
    img_source_count: dict[str, set] = {}
    for _, _, img, src_url in valid:
        img_source_count.setdefault(img, set()).add(src_url)

    final: list[tuple[str, int, str]] = []
    for cat_key, idx, img, src_url in valid:
        if len(img_source_count[img]) >= PLACEHOLDER_DEDUP_THRESHOLD:
            dedup_rejected += 1
            console.log(f"[yellow]    ✗ 重复占位 [{len(img_source_count[img])}×]: {img[:60]}[/yellow]")
        else:
            final.append((cat_key, idx, img))

    # 写回
    fetched = 0
    for cat_key, idx, img in final:
        selected[cat_key][idx]["image_url"] = img
        fetched += 1
        console.log(f"[dim]      图片: {img[:70]}... ← {selected[cat_key][idx]['title'][:30]}[/dim]")

    console.print(
        f"  原始: {raw_count} | 占位图: {pattern_rejected} | "
        f"重复占位: {dedup_rejected} | 最终: [green]{fetched}[/green]"
    )
    return selected
