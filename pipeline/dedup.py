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

    # 阶段 2: 标题相似度聚类
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
        cluster_items.sort(key=lambda x: x.get("published_at") or datetime(2000, 1, 1, tzinfo=timezone.utc))
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
