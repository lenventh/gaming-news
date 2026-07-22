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


# ===== 非游戏硬件信号词 — 用于 filter_topic_relevance =====
_NON_HARDWARE_SIGNALS: list[str] = [
    # 自动驾驶/机器人（非游戏）
    "自动驾驶", "autonomous driving", "self-driving",
    "世界人工智能大会", "WAIC",
    # 通用AI/世界模型（非游戏专用）
    "世界模型.*自动驾驶", "VLA.*自动驾驶",
    "omnidreams", "omni dreams",
    # 半导体/芯片行业法律/商业新闻（非游戏设备）
    "tsmc", "国家安全法.*起诉", "chipmaking.*china",
    # 模拟经营游戏（标题以"XX模拟器"结尾且前面是纯中文游戏名，非Emulator软件）
    # 正例（保留）：RPCS3模拟器、Eden模拟器、Yuzu模拟器 — 英文缩写开头
    # 反例（过滤）：咖啡店主理人模拟器、圣旨模拟器 — 纯中文游戏描述
    r"(?<![\w])[一-鿿]{2,8}模拟器[：:\\s]",  # 前2-8个纯中文 + "模拟器"结尾 → 游戏
]


def filter_topic_relevance(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """过滤与游戏硬件无关的内容（机器人/自动驾驶/通用AI等误入条目）

    LLM 分类器的 irrelevant 规则有时被忽略，此函数用关键词做兜底过滤。
    仅在标题+摘要中明确出现非硬件信号时才剔除。
    """
    import re

    # 游戏硬件正面信号 — 命中时不变，跳过过滤
    _HW_SIGNALS = re.compile(
        r"steam\s*(deck|machine|controller|os)|显卡|rtx\s*50|gpu|掌机|handheld|"
        r"手柄|controller|主机|console|switch\s*2|ps5|模拟器|emulator|"
        r"摇杆|joy.?con|vr.*头显|quest|头显",
        re.IGNORECASE,
    )

    kept = []
    removed = []
    for item in items:
        title = (item.get("title") or "")
        summary = (item.get("summary") or "")
        combined = (title + " " + summary).lower()

        # 含有游戏硬件正面信号 → 放行（B站杂谈视频可能同时提 AWS/芯片等）
        if _HW_SIGNALS.search(combined):
            kept.append(item)
            continue

        matched = False
        for pattern in _NON_HARDWARE_SIGNALS:
            if re.search(pattern, combined, re.IGNORECASE):
                matched = True
                break
        if matched:
            removed.append(item)
        else:
            kept.append(item)
    if removed:
        console.log(
            f"[yellow]话题相关性过滤: 剔除 {len(removed)} 条"
            f" ({', '.join((it.get('title', '') or '无标题')[:40] for it in removed[:5])})[/yellow]"
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
