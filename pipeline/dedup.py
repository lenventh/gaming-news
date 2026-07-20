"""基于标题相似度的新闻去重"""

import re
from difflib import SequenceMatcher
from rich.console import Console

console = Console()

SIMILARITY_THRESHOLD = 0.75


def _normalize(text: str) -> str:
    """规范化文本用于比较"""
    text = text.lower().strip()
    # 去除非中英文数字的字符
    text = re.sub(r"[^\w一-鿿]", "", text)
    return text


def _similarity(a: str, b: str) -> float:
    """计算两个字符串的相似度 (0.0 ~ 1.0)"""
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def deduplicate(items: list[dict], threshold: float = SIMILARITY_THRESHOLD) -> list[dict]:
    """对新闻标题计算相似度，合并重复条目。

    策略：
    1. 先 URL 精确去重（不同源可能指向同一 URL）
    2. 再将标题规范化后计算两两相似度
    3. 相似度 > threshold 的条目视为同一事件，归入同一簇
    4. 保留发布时间最早的版本，合并来源和素材链接
    """
    if len(items) <= 1:
        return items

    # 阶段 1: URL 精确去重
    seen_urls = {}
    url_deduped = []
    url_dup_count = 0
    for item in items:
        url = item.get("url", "").strip()
        if url and url in seen_urls:
            seen_urls[url]["merged_sources"] = list(set(
                seen_urls[url].get("merged_sources", [seen_urls[url].get("source_name")]) +
                [item.get("source_name", "")]
            ))
            url_dup_count += 1
        else:
            if url:
                seen_urls[url] = item
            url_deduped.append(item)
    if url_dup_count > 0:
        console.log(f"[dim]  URL 去重: 移除 {url_dup_count} 条精确匹配[/dim]")
    items = url_deduped

    if len(items) <= 1:
        return items

    titles = [item.get("title", "") for item in items]
    if not all(titles):
        return items

    # 阶段 2: 同一天 + 同产品名 → 合并（跨语言去重）
    from pipeline.device_os_map import DEVICE_CATEGORY_MAP
    product_groups: dict[str, list[int]] = {}
    for idx, item in enumerate(items):
        pub = item.get("published_at")
        if not pub:
            continue
        date_key = pub.strftime("%Y-%m-%d") if hasattr(pub, 'strftime') else str(pub)[:10]
        title = item.get("title", "").lower()
        # 提取匹配的产品名（按长度倒序，优先长名匹配，如 gkd 350h ultra > gkd 350h）
        sorted_devices = sorted(DEVICE_CATEGORY_MAP.keys(), key=len, reverse=True)
        for device in sorted_devices:
            if len(device) <= 4:
                continue
            if re.search(r'\b' + re.escape(device) + r'\b', title):  # 忽略太短的（如 "rp5" 误匹配）
                key = f"{date_key}|{device}"
                if key not in product_groups:
                    product_groups[key] = []
                product_groups[key].append(idx)
                break  # 一个条目只记一次
    # 合并同组
    to_merge = set()
    for key, indices in product_groups.items():
        if len(indices) > 1:
            # 标记除第一个外的所有为待合并
            primary = min(indices)
            for i in indices[1:]:
                to_merge.add(i)
    if to_merge:
        product_deduped = []
        merged_count = 0
        for idx, item in enumerate(items):
            if idx in to_merge:
                merged_count += 1
            else:
                product_deduped.append(item)
        console.log(f"[dim]  产品名去重: 移除 {merged_count} 条(同天同产品)[/dim]")
        items = product_deduped

    if len(items) <= 1:
        return items

    # 阶段 3: 标题相似度聚类
    clusters = []
    visited = set()

    for i in range(len(items)):
        if i in visited:
            continue
        cluster = [i]
        visited.add(i)

        for j in range(i + 1, len(items)):
            if j in visited:
                continue
            # 检查 j 和当前簇中任一条是否相似
            for member in cluster:
                if _similarity(titles[member], titles[j]) > threshold:
                    cluster.append(j)
                    visited.add(j)
                    break

        clusters.append(cluster)

    # 合并每个簇
    merged = []
    for cluster in clusters:
        cluster_items = [items[idx] for idx in cluster]
        # 保留最早发布时间的
        from datetime import datetime, timezone
        def _sort_dt(item):
            v = item.get("published_at")
            if v is None:
                return datetime(2000, 1, 1, tzinfo=timezone.utc)
            if isinstance(v, str):
                try:
                    return datetime.fromisoformat(v)
                except (ValueError, TypeError):
                    return datetime(2000, 1, 1, tzinfo=timezone.utc)
            return v
        cluster_items.sort(key=_sort_dt)
        primary = cluster_items[0]

        # 合并来源名和素材链接
        all_sources = list(set(it.get("source_name", "") for it in cluster_items))
        all_materials = []
        for it in cluster_items:
            for link in it.get("material_links", []):
                if link not in all_materials:
                    all_materials.append(link)

        copied = dict(primary)
        copied["merged_sources"] = all_sources
        copied["material_links"] = all_materials[:10]
        if "raw_data" in copied:
            copied["raw_data"] = dict(copied["raw_data"])
            copied["raw_data"]["duplicate_count"] = len(cluster)

        merged.append(copied)

    removed = len(items) - len(merged)
    if removed > 0:
        console.log(f"[yellow]去重: {len(items)} → {len(merged)} (移除 {removed} 条重复)[/yellow]")

    return merged
