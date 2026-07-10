"""排序精选：按 sub_type 优先级 + 热度排序，每分类取 Top N"""

from rich.console import Console

console = Console()

TOP_PER_CATEGORY = 5

# sub_type 优先级：数字越小越靠前
SUBTYPE_PRIORITY = {"leak": 0, "release": 1, "system": 2, "general": 3}


def _sort_key(item: dict) -> tuple:
    """排序键：爆料 > 发售 > 系统更新 > 其他，同类内按热度+日期排"""
    sub_priority = SUBTYPE_PRIORITY.get(item.get("sub_type", "general"), 3)
    score = item.get("raw_data", {}).get("score", 0)
    has_date = 1 if item.get("published_at") else 0
    date_str = item.get("published_at") or ""
    return (sub_priority, -score, -has_date, date_str)


def select_top_items(items: list[dict], top_n: int = TOP_PER_CATEGORY) -> dict[str, list[dict]]:
    """按分类分组，每类按 sub_type 优先级 + 热度选取 top_n 条"""
    grouped: dict[str, list[dict]] = {}
    for item in items:
        cat = item.get("category", "other")
        grouped.setdefault(cat, []).append(item)

    selected: dict[str, list[dict]] = {}

    for cat, cat_items in grouped.items():
        cat_items.sort(key=_sort_key)
        selected[cat] = cat_items[:top_n]
        sub_counts = {}
        for it in selected[cat]:
            st = it.get("sub_type", "?")
            sub_counts[st] = sub_counts.get(st, 0) + 1
        breakdown = " ".join(f"{k}:{v}" for k, v in sorted(sub_counts.items()))
        console.log(f"[blue]  {cat}: {len(cat_items)} 条 → 精选 {len(selected[cat])} 条 ({breakdown})[/blue]")

    return selected
