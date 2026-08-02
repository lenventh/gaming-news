"""日期过滤 + 泄漏条目回捞 + 过滤日志与误踢回捞"""

import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from rich.console import Console

from config import OUTPUT_DIR

console = Console()

_FILTERED_LOG_PATH = os.path.join(OUTPUT_DIR, ".filtered_items.json")
_MAX_RUNS = 10  # 最多保留最近 N 次运行的过滤记录


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
        # Human-reviewed recover items bypass quality filter
        if item.get("raw_data", {}).get("_recovered_from"):
            kept.append(item)
            continue

        title = (item.get("title") or "").strip()
        summary = (item.get("summary") or "").strip()
        source_type = (item.get("source_type") or "").lower()
        combined = (title + " " + summary).strip()

        # 0. URL 编码标题 — Google News RSS 未解码的贴吧/社区条目
        if _is_url_encoded_garbage(title, combined):
            item["raw_data"]["_filter_reason"] = "url_encoded_garbage"
            removed.append(item)
            continue

        # 1. 微博/社交媒体占位符 — 标题即无意义标签
        if title in ("微博", "微博正文", "LISA", "百度贴吧", "贴吧排行榜"):
            item["raw_data"]["_filter_reason"] = "social_placeholder"
            removed.append(item)
            continue

        # 1.5 贴吧用户动态（"XXX的关注"/"XXX的粉丝"）— 非新闻内容
        if re.search(r"的(?:关注|粉丝|动态|主页|个人中心)$", title) and len(title) < 20:
            item["raw_data"]["_filter_reason"] = "tieba_user_page"
            removed.append(item)
            continue

        # 2. "(原文未完整...)" / "(内容待补充)" — 截断或无内容
        if "原文未完整" in summary or "内容待补充" in summary or "内容待补充" in title:
            item["raw_data"]["_filter_reason"] = "incomplete_content"
            removed.append(item)
            continue

        # 3. 完整内容过短的空条目，分源设置阈值
        #    英文 RSS（Reddit）标题天然短，阈值 20 字符
        #    中文源（微博/RSS/浏览器直抓）保持 30 字符
        if source_type == "rss":
            if len(combined) < 20 and not _has_hardware_signal(combined):
                item["raw_data"]["_filter_reason"] = "too_short"
                removed.append(item)
                continue
        elif source_type in ("weibo", "rss_cn", "chinese_web"):
            if len(combined) < 30:
                item["raw_data"]["_filter_reason"] = "too_short"
                removed.append(item)
                continue

        # 4. 仅有图片无实质文字 (B站/B站动态图片帖无描述)
        if title.startswith("[图片动态]") and len(summary) < 15:
            item["raw_data"]["_filter_reason"] = "image_only_no_text"
            removed.append(item)
            continue

        # 5. summary 为空且标题不含实质产品/品牌名
        if not summary and len(title) < 10 and source_type in ("weibo", "tieba", "tieba_browser"):
            item["raw_data"]["_filter_reason"] = "no_summary_short_title"
            removed.append(item)
            continue

        kept.append(item)

    if removed:
        console.log(
            f"[yellow]内容质量过滤: {len(items)} 条 → {len(kept)} 条"
            f" (剔除 {len(removed)}: {', '.join((it.get('title', '') or '无标题')[:30] for it in removed[:5])})[/yellow]"
        )
        _save_filtered_items(removed, "content_quality")
    return kept, removed


# 游戏硬件品牌/关键词 — 短标题命中时跳过长度过滤
_HARDWARE_BRANDS = re.compile(
    r"ayn\b|odin|retroid|pocket\b.*(?:handheld|gaming|console|fit|air|flip|mini|evo|dmc|s\b)|"
    r"anbernic|miyoo|trimui|powkiddy|gpd|ayaneo|onexplayer|legion\s*go|rog\s*ally|"
    r"msi\s*claw|steam\s*deck|switch\s*2|ps5|ps6|xbox|"
    r"rg\d{2,4}|rg\b|rk\d{4}|snapdragon\s*g\d|"
    r"handheld|掌机|开源机|寨机|"
    r"emulator|模拟器|proton\b|wine\b|batocera|garlicos|onionos|"
    r"hall\s*(?:effect|sensor|joystick)|joystick|dpad|d-pad",
    re.IGNORECASE,
)


def _has_hardware_signal(text: str) -> bool:
    """检测文本是否含游戏硬件品牌/关键词，用于短标题白名单放行。"""
    return bool(_HARDWARE_BRANDS.search(text))


def _is_url_encoded_garbage(title: str, combined: str) -> bool:
    """检测 URL 编码的垃圾标题（Google News RSS 未解码的贴吧用户动态等）

    特征：
    - 标题含 URL 编码（%XX 模式）
    - 解码后是"XXX的关注"/"XXX的粉丝"等非新闻内容
    - 标题长度异常短但 URL 编码占比高
    """
    from urllib.parse import unquote

    # 标题含 URL 编码（至少 2 个 %XX 模式）
    pct_matches = re.findall(r"%[0-9A-Fa-f]{2}", title)
    if len(pct_matches) < 2:
        return False

    # URL 编码字符占比 > 30%
    pct_chars = len(pct_matches) * 3  # 每个 %XX 占 3 字符
    if pct_chars / max(len(title), 1) < 0.3:
        return False

    # 尝试解码
    try:
        decoded = unquote(title)
    except Exception:
        return True

    # 解码后仍含大量非 ASCII 乱码特征的过滤
    if re.search(r"的(?:关注|粉丝|动态|主页)$", decoded) and len(decoded) < 20:
        return True

    # 解码后标题仍以 % 开头或含不可打印字符
    if decoded.startswith("%") or any(ord(c) < 32 for c in decoded):
        return True

    # 解码前后长度比异常（URL 编码的另一个信号）
    if len(title) > 20 and len(decoded) < len(title) * 0.4:
        return True

    return False


# ===== 非游戏硬件信号词 — 用于 filter_topic_relevance =====
_NON_HARDWARE_SIGNALS: list[str] = [
    # 自动驾驶/机器人（非游戏）
    "自动驾驶", "智能驾驶", "autonomous driving", "self-driving",
    "世界人工智能大会", "WAIC",
    # 驾考/驾驶员（非游戏设备）
    "驾考", "驾驶员(?!.*(?:模拟器|Sim))", "科目一", "科目二",
    # 通用AI/世界模型（非游戏专用）
    "世界模型.*自动驾驶", "世界模型.*社会", "VLA.*自动驾驶",
    "omnidreams", "omni dreams",
    "境瞳", "境瞳科技",
    # AI 大模型产品/市场新闻（非游戏AI）
    r"\bKimi\b.*(?:爆单|停售|半价|降价|涨价|上线|发布)",
    r"Claude.*Opus.*(?:半价|降价|屠榜|发布|上线)",
    r"GPT.*(?:模型|发布|上线|降价)",
    r"DeepSeek.*(?:模型|发布|上线)",
    r"大模型.*(?:爆单|停售|半价|降价|融资|上线)",
    # 考研/教育考试（非游戏）
    "考研", "备考.*题库", "专业课真题", "备考资料.*课件", "考试题库",
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
        r"手柄|controller|主机|console|playstation|xbox|nintendo|switch\s*2|ps5|模拟器|emulator|"
        r"摇杆|joy.?con|vr.*头显|quest|头显",
        re.IGNORECASE,
    )

    kept = []
    removed = []
    for item in items:
        # Human-reviewed recover items bypass topic filter
        if item.get("raw_data", {}).get("_recovered_from"):
            kept.append(item)
            continue

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
            item["raw_data"]["_filter_reason"] = "topic_irrelevant"
            removed.append(item)
        else:
            kept.append(item)
    if removed:
        console.log(
            f"[yellow]话题相关性过滤: 剔除 {len(removed)} 条"
            f" ({', '.join((it.get('title', '') or '无标题')[:40] for it in removed[:5])})[/yellow]"
        )
        _save_filtered_items(removed, "topic_relevance")
    return kept, removed


def get_week_label() -> str:
    """返回半周标签，如 '2026-W28-上' / '2026-W28-下'

    上：报道本周一-三/四（Thu-Fri UTC 运行）
    下：报道上周/本周四-日（Sat-Wed UTC 运行）
    workflow_dispatch 时根据当天星期自动判断。
    """
    now = datetime.now(timezone.utc)
    wd = now.weekday()  # 0=Mon ... 6=Sun
    if wd <= 2:
        # Mon-Wed: 下（覆盖上周四-日）
        half = "下"
        report_week = (now - timedelta(days=3)).isocalendar()
    elif wd <= 4:
        # Thu-Fri: 上（覆盖本周一-三/四）
        half = "上"
        report_week = now.isocalendar()
    else:
        # Sat-Sun: 下（覆盖本周四-日）
        half = "下"
        report_week = (now - timedelta(days=3)).isocalendar()
    return f"{report_week[0]}-W{report_week[1]:02d}-{half}"


def get_week_range(cutoff_date: datetime) -> str:
    """返回半周日期范围的显示字符串，如 '7.11 - 7.14'"""
    now = datetime.now(timezone.utc)
    start = cutoff_date
    return f"{start.month}.{start.day} - {now.month}.{now.day}"


# ===== 过滤日志与误踢回捞 =====

def _save_filtered_items(removed: list[dict], filter_name: str) -> None:
    """将本轮被过滤的条目追加写入 output/.filtered_items.json。

    保留最近 _MAX_RUNS 次运行的记录，旧记录自动清理。
    """
    if not removed:
        return

    label = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    entry = {
        "filtered_at": label,
        "filter_name": filter_name,
        "count": len(removed),
        "items": [
            {
                "title": (it.get("title") or "").strip(),
                "url": (it.get("url") or "").strip(),
                "summary": (it.get("summary") or "")[:200],
                "source_type": it.get("source_type", ""),
                "filter_reason": it.get("raw_data", {}).get("_filter_reason", "unknown"),
            }
            for it in removed
        ],
    }

    # 读取现有日志
    log_data = _read_filter_log()

    # 追加新记录
    if "runs" not in log_data:
        log_data["runs"] = []
    log_data["runs"].append(entry)

    # 只保留最近 _MAX_RUNS 次
    if len(log_data["runs"]) > _MAX_RUNS:
        log_data["runs"] = log_data["runs"][-_MAX_RUNS:]

    _write_filter_log(log_data)


def _read_filter_log() -> dict:
    """读取过滤日志文件，文件不存在或损坏时返回空 dict。"""
    try:
        with open(_FILTERED_LOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_filter_log(data: dict) -> None:
    """写入过滤日志文件，确保输出目录存在。"""
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    with open(_FILTERED_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_filtered_items(
    filter_name: str | None = None,
    reason: str | None = None,
    filepath: str | None = None,
) -> list[dict]:
    """加载过滤日志中被剔除的条目，可按过滤器和原因筛选。

    Args:
        filter_name: 过滤器名筛选 ("content_quality" / "topic_relevance")，None=全部
        reason: 过滤原因筛选 (如 "too_short", "topic_irrelevant")，None=全部
        filepath: 自定义日志文件路径，None=默认 output/.filtered_items.json

    Returns:
        被过滤条目列表，每条含 title/url/summary/source_type/filter_reason/filtered_at/filter_name
    """
    path = filepath or _FILTERED_LOG_PATH
    log_data = _read_filter_log() if filepath is None else {}
    if filepath:
        try:
            with open(path, "r", encoding="utf-8") as f:
                log_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    result = []
    for run in log_data.get("runs", []):
        if filter_name and run.get("filter_name") != filter_name:
            continue
        for it in run.get("items", []):
            if reason and it.get("filter_reason") != reason:
                continue
            # 附加运行级元数据
            it["_filtered_at"] = run.get("filtered_at", "")
            it["_filter_name"] = run.get("filter_name", "")
            result.append(it)
    return result


def recover_filtered_items(
    reasons: list[str] | None = None,
    filter_names: list[str] | None = None,
    filepath: str | None = None,
) -> list[dict]:
    """回捞被误踢的条目，返回可重新注入管道的条目 dict 列表。

    典型用法:
        # 回捞所有因"内容太短"被过滤的条目，人工复查后重新并入
        recovered = recover_filtered_items(reasons=["too_short"])

        # 回捞内容质量过滤器的所有条目
        recovered = recover_filtered_items(filter_names=["content_quality"])

    Args:
        reasons: 要回捞的过滤原因列表，None=不限原因
        filter_names: 要回捞的过滤器列表，None=不限过滤器
        filepath: 自定义日志文件路径

    Returns:
        可重新注入管道的条目列表（保留了原始 title/url/summary/source_type）
    """
    path = filepath or _FILTERED_LOG_PATH
    all_filtered = load_filtered_items(filepath=path)

    recovered = []
    for it in all_filtered:
        if reasons and it.get("filter_reason") not in reasons:
            continue
        if filter_names and it.get("_filter_name") not in filter_names:
            continue
        # 构造成管道兼容的条目格式
        recovered.append({
            "title": it.get("title", ""),
            "url": it.get("url", ""),
            "summary": it.get("summary", ""),
            "source_type": it.get("source_type", ""),
            "raw_data": {
                "_recovered_from": it.get("_filter_name", ""),
                "_original_filter_reason": it.get("filter_reason", ""),
                "_filtered_at": it.get("_filtered_at", ""),
            },
        })
    return recovered


def show_filter_stats(filepath: str | None = None) -> dict[str, int]:
    """展示过滤统计：各类原因分别剔除了多少条。

    Returns:
        {"too_short": 12, "topic_irrelevant": 8, ...}
    """
    all_filtered = load_filtered_items(filepath=filepath)
    stats: dict[str, int] = {}
    for it in all_filtered:
        reason = it.get("filter_reason", "unknown")
        stats[reason] = stats.get(reason, 0) + 1
    return stats
