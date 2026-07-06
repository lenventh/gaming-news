"""排序精选：每分类取 Top 5"""

from rich.console import Console

console = Console()

TOP_PER_CATEGORY = 5


def select_top_items(items: list[dict], top_n: int = TOP_PER_CATEGORY) -> dict[str, list[dict]]:
    """按分类分组，每类选取 top_n 条。

    排序依据：
    1. Reddit score（热度）
    2. 发布日期越新越靠前
    3. 日期明确的优先于日期不明的
    """
    grouped: dict[str, list[dict]] = {}
    for item in items:
        cat = item.get("category", "other")
        grouped.setdefault(cat, []).append(item)

    selected: dict[str, list[dict]] = {}

    for cat, cat_items in grouped.items():
        cat_items.sort(
            key=lambda x: (
                # Reddit 热度（非 Reddit 条目用 0）
                x.get("raw_data", {}).get("score", 0),
                # 发布日期：越新越靠前
                x.get("published_at") or "",
                # 日期明确的优先
                0 if x.get("published_at") else 1,
            ),
            reverse=True,
        )
        selected[cat] = cat_items[:top_n]
        console.log(f"[blue]  {cat}: {len(cat_items)} 条 → 精选 {len(selected[cat])} 条[/blue]")

    return selected
