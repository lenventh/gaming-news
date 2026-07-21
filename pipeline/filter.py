"""日期过滤 + 泄漏条目回捞"""

from datetime import datetime, timezone, timedelta
from rich.console import Console

console = Console()


def filter_by_date(items: list[dict], cutoff_date: datetime,
                   leak_cutoff_date: datetime | None = None) -> list[dict]:
    """过滤出 cutoff_date 之后发布的新闻。

    如果提供了 leak_cutoff_date（更早的截止线），对于 cutoff_date
    之前但 leak_cutoff_date 之后的条目，标记为 _expanded_window 保留。
    后续分类阶段确认 sub_type=leak 后才真正保留，非 leak 的会被剔除。

    无法解析日期的条目会被保留但标记为 low_confidence。
    """
    filtered = []
    unknown_date = []
    expanded = []  # cutoff_date 之前但 leak_cutoff_date 之后

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
        elif leak_cutoff_date and pub_date >= leak_cutoff_date:
            item["raw_data"]["_expanded_window"] = True
            expanded.append(item)

    for item in unknown_date:
        item["raw_data"]["date_confidence"] = "low"
        filtered.append(item)

    leak_hint = f" (+{len(expanded)} 条待确认)" if expanded else ""
    console.log(
        f"[cyan]日期过滤: {len(items)} 条 → {len(filtered)} 条"
        f" ({len(unknown_date)} 条日期不明，保留但降权){leak_hint}[/cyan]"
    )
    return filtered, expanded


def prune_expanded(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """去除 _expanded_window 中非 leak 的条目，保留 leak 条目作为跟进信号。

    Returns:
        (保留的条目, 剔除的条目)
    """
    kept = []
    pruned = []
    for item in items:
        if item.get("raw_data", {}).get("_expanded_window"):
            if item.get("sub_type") == "leak":
                kept.append(item)
            else:
                pruned.append(item)
        else:
            kept.append(item)

    if pruned:
        console.log(
            f"[dim]扩展窗口回收: 剔除 {len(pruned)} 条非leak，"
            f"保留 {len(kept) - len(items) + len(pruned)} 条leak信号[/dim]"
        )
    return kept, pruned


def filter_content_quality(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """过滤空内容/截断/占位符等低质量条目

    Returns:
        (保留的条目, 剔除的条目)
    """
    kept = []
    removed = []
    for item in items:
        title = (item.get("title") or "").strip()
        summary = (item.get("summary") or "").strip()
        source_type = (item.get("source_type") or "").lower()
        combined = (title + " " + summary).strip()

        # 1. 微博/社交媒体占位符 — 标题即无意义标签
        if title in ("微博", "微博正文", "LISA", "百度贴吧", "贴吧排行榜"):
            removed.append(item)
            continue

        # 2. "(原文未完整...)" / "(内容待补充)" — 截断或无内容
        if "原文未完整" in summary or "内容待补充" in summary or "内容待补充" in title:
            removed.append(item)
            continue

        # 3. 完整内容 < 30 字符（标题+摘要），从微博/RSS 抓取的空条目
        if len(combined) < 30 and source_type in ("weibo", "rss_cn", "chinese_web", "rss"):
            removed.append(item)
            continue

        # 4. 仅有图片无实质文字 (B站/B站动态图片帖无描述)
        if title.startswith("[图片动态]") and len(summary) < 15:
            removed.append(item)
            continue

        # 5. summary 为空且标题不含实质产品/品牌名
        if not summary and len(title) < 10 and source_type in ("weibo", "tieba", "tieba_browser"):
            removed.append(item)
            continue

        kept.append(item)

    if removed:
        console.log(
            f"[yellow]内容质量过滤: {len(items)} 条 → {len(kept)} 条"
            f" (剔除 {len(removed)}: {', '.join((it.get('title', '') or '无标题')[:30] for it in removed[:5])})[/yellow]"
        )
    return kept, removed


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
