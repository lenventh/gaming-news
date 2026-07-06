"""日期过滤：只保留近 7 天内的新闻"""

from datetime import datetime, timezone, timedelta
from rich.console import Console

console = Console()


def filter_by_date(items: list[dict], cutoff_date: datetime) -> list[dict]:
    """过滤出 cutoff_date 之后发布的新闻。

    无法解析日期的条目会被保留但标记为 low_confidence。
    """
    filtered = []
    unknown_date = []

    for item in items:
        pub_str = item.get("published_at")
        if not pub_str:
            unknown_date.append(item)
            continue

        try:
            pub_date = datetime.fromisoformat(pub_str)
        except (ValueError, TypeError):
            unknown_date.append(item)
            continue

        if pub_date >= cutoff_date:
            filtered.append(item)

    # 无法确定日期的条目放在后面（降权），但不丢弃
    for item in unknown_date:
        item["raw_data"]["date_confidence"] = "low"
        filtered.append(item)

    console.log(
        f"[cyan]日期过滤: {len(items)} 条 → {len(filtered)} 条"
        f" ({len(unknown_date)} 条日期不明，保留但降权)[/cyan]"
    )
    return filtered


def get_week_label() -> str:
    """返回当前周的标签，如 '2026-W28'"""
    now = datetime.now(timezone.utc)
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def get_week_range(cutoff_date: datetime) -> str:
    """返回这周日期范围的显示字符串，如 '7.6 - 7.12'"""
    now = datetime.now(timezone.utc)
    start = cutoff_date
    return f"{start.month}.{start.day} - {now.month}.{now.day}"
