"""配图抓取：从来源页面提取 og:image，并发请求避免阻塞"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from rich.console import Console

console = Console()

FETCH_TIMEOUT = 5
MAX_WORKERS = 5


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
    """并发抓取所有条目的配图，写入 item['image_url']"""
    # 收集所有需要抓图的条目
    tasks: list[tuple[str, int, str]] = []  # (cat_key, index, url)
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

    fetched = 0

    def _fetch_one(cat_key: str, idx: int, url: str):
        img = _extract_image_from_page(url)
        return cat_key, idx, img

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, c, i, u): (c, i, u) for c, i, u in tasks}
        for future in as_completed(futures):
            try:
                cat_key, idx, img = future.result()
                if img:
                    selected[cat_key][idx]["image_url"] = img
                    fetched += 1
                    console.log(f"[dim]      图片: {img[:70]}... ← {selected[cat_key][idx]['title'][:30]}[/dim]")
            except Exception:
                pass

    console.print(f"  抓取成功: {fetched}/{len(tasks)} ([green]{fetched / max(len(tasks), 1) * 100:.0f}%[/green])")
    return selected
