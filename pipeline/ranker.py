"""排序精选：按 sub_type 优先级 + 热度 + 来源多样性排序，每分类取 Top N"""

from rich.console import Console

console = Console()

TOP_PER_CATEGORY = 8

# sub_type 优先级：数字越小越靠前
SUBTYPE_PRIORITY = {"leak": 0, "release": 1, "system": 2, "general": 3}

# 中文浏览器采集来源 — 给予多样性加分，避免被英文 RSS/Reddit 淹没
CN_SOURCE_TYPES = {
    # B站 全系
    "bilibili_browser", "bilibili_manufacturer", "bilibili_space",
    "bilibili_article", "bilibili_dynamic",
    # 贴吧 / 知乎 / 什么值得买
    "tieba", "tieba_browser",
    "zhihu_browser", "smzdm_browser",
    # Google News 中文关键词搜索
    "chinese_web",
    # 中文 RSS (IT之家/机核/IGN中国等)
    "rss_cn",
}

# 来源多样性加分系数（加到 score 上，使其在同类中排名靠前）
DIVERSITY_BOOST = 50


def _sort_key(item: dict) -> tuple:
    """排序键：爆料 > 发售 > 系统更新 > 其他，同类内按时效置信度+热度+日期排"""
    sub_priority = SUBTYPE_PRIORITY.get(item.get("sub_type", "general"), 3)
    score = item.get("raw_data", {}).get("score", 0)
    # 中文浏览器来源加分
    source_type = item.get("source_type", "")
    if source_type in CN_SOURCE_TYPES:
        score += DIVERSITY_BOOST
    # date_confidence=low 条目严重降权，排在所有有日期条目之后
    date_low = item.get("raw_data", {}).get("date_confidence") == "low"
    has_date = 1 if item.get("published_at") else 0
    date_str = item.get("published_at") or ""
    return (sub_priority, date_low, -score, -has_date, date_str)


def select_top_items(items: list[dict], top_n: int = TOP_PER_CATEGORY) -> dict[str, list[dict]]:
    """按分类分组，每类按 sub_type 优先级 + 热度选取 top_n 条

    拆分国内/海外两套，各取 top_n 条，互不干扰。
    """
    grouped: dict[str, list[dict]] = {}
    for item in items:
        cat = item.get("category", "other")
        grouped.setdefault(cat, []).append(item)

    selected: dict[str, list[dict]] = {}
    cn_total = 0
    os_total = 0

    for cat, cat_items in grouped.items():
        # 按来源拆国内/海外
        domestic = [it for it in cat_items if it.get("source_type", "") in CN_SOURCE_TYPES]
        overseas = [it for it in cat_items if it.get("source_type", "") not in CN_SOURCE_TYPES]

        domestic.sort(key=_sort_key)
        overseas.sort(key=_sort_key)

        picked_d = domestic[:top_n]
        picked_o = overseas[:top_n]
        combined = picked_d + picked_o
        selected[cat] = combined

        cn_total += len(picked_d)
        os_total += len(picked_o)
        console.log(
            f"[blue]  {cat}: {len(cat_items)}条 → 国内{len(picked_d)} + 海外{len(picked_o)}"
            f" = {len(combined)}条[/blue]"
        )

    console.log(f"[green]  总计精选: 国内{cn_total}条 + 海外{os_total}条 = {cn_total+os_total}条[/green]")
    return selected
