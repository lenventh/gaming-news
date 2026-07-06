"""引文格式化：生成论文格式的参考资料列表"""

from datetime import datetime, timezone


def format_citation(index: int, item: dict) -> str:
    """格式化单条引文为 GB/T 7714 类似格式：

    [序号] 作者/平台. "标题". 来源, 日期. URL: <链接>
    """
    title = item.get("title", "未知标题")
    source = item.get("source_name", "未知来源")
    url = item.get("url", "")

    # 尝试提取作者
    author = _extract_author(item)

    # 日期
    pub_str = item.get("published_at", "")
    date_display = ""
    if pub_str:
        try:
            dt = datetime.fromisoformat(pub_str)
            date_display = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass

    # 构建引文
    parts = []
    parts.append(f"[{index}]")

    if author:
        parts.append(f'{author}.')
    else:
        # 用来源平台名代替作者
        parts.append(f'{source}.')

    parts.append(f'"{title}".')

    if not author:
        parts.append(f"{source},")  # 来源名在作者处已写时跳过
    else:
        parts.append(f"{source},")

    if date_display:
        parts.append(f"{date_display}.")

    if url:
        parts.append(f"URL: {url}")

    return " ".join(parts)


def _extract_author(item: dict) -> str:
    """尝试从条目中提取作者/创作者信息"""
    raw = item.get("raw_data", {})
    if "author" in raw and raw["author"] and raw["author"] != "[deleted]":
        return raw["author"]
    return ""


def generate_citations(items: list[dict]) -> list[str]:
    """为所有条目生成引文列表，按分类-序号排序"""
    citations = []

    # 按分类分组
    grouped: dict[str, list[dict]] = {}
    for item in items:
        cat = item.get("category", "other")
        grouped.setdefault(cat, []).append(item)

    index = 1
    for cat, cat_items in grouped.items():
        for item in cat_items:
            citations.append(format_citation(index, item))
            index += 1

    return citations


def generate_citations_block(items: list[dict]) -> str:
    """生成完整的参考资料区块 Markdown"""
    citations = generate_citations(items)
    if not citations:
        return "## 参考资料\n\n（暂无来源）\n"

    lines = ["## 参考资料\n"]
    for c in citations:
        lines.append(c)
        lines.append("")
    return "\n".join(lines)
