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
    """返回半周标签，如 '2026-W28-上' / '2026-W28-下'

    周一运行 → 下（覆盖上周四-日，ISO 周号取上周）
    周四运行 → 上（覆盖本周一-三，ISO 周号取本周）
    workflow_dispatch 时根据当天星期自动判断。
    """
    now = datetime.now(timezone.utc)
    wd = now.weekday()  # 0=Mon ... 6=Sun
    if wd <= 2:
        half = "下"
        # 报道的是上周四-日 → 取上周的 ISO 周号
        report_week = (now - timedelta(days=3)).isocalendar()
    else:
        half = "上"
        report_week = now.isocalendar()
    return f"{report_week[0]}-W{report_week[1]:02d}-{half}"


def get_week_range(cutoff_date: datetime) -> str:
    """返回半周日期范围的显示字符串，如 '7.11 - 7.14'"""
    now = datetime.now(timezone.utc)
    start = cutoff_date
    return f"{start.month}.{start.day} - {now.month}.{now.day}"
