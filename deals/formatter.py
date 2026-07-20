"""折扣数据 → Markdown 表格（供周刊和视频工作流使用）"""


def format_deals_table(deals: list[dict], title: str = "本周游戏折扣") -> str:
    """将折扣列表格式化为 Markdown 表格

    Args:
        deals: [{"game": "艾尔登法环", "platform": "Steam国区", "discount": "-40%",
                 "original": "¥298", "price": "¥179", "until": "7/25"}, ...]
        title: 表格标题

    返回 Markdown 字符串
    """
    if not deals:
        return ""

    lines = [
        f"## {title}\n",
        "| 游戏 | 平台 | 折扣 | 原价 | 现价 | 截止 |",
        "|------|------|------|------|------|------|",
    ]

    for d in deals:
        game = d.get("game", "")[:40]
        platform = d.get("platform", "")
        discount = d.get("discount", "")
        original = d.get("original", "")
        price = d.get("price", "")
        until = d.get("until", "")
        lines.append(
            f"| {game} | {platform} | {discount} | {original} | {price} | {until} |"
        )

    return "\n".join(lines)


def format_free_games(games: list[dict], title: str = "本周限免游戏") -> str:
    """限免游戏列表

    Args:
        games: [{"game": "...", "platform": "Epic", "until": "7/25", "url": "..."}, ...]
    """
    if not games:
        return ""

    lines = [f"## {title}\n"]
    for g in games:
        game = g.get("game", "")[:50]
        platform = g.get("platform", "")
        until = g.get("until", "")
        url = g.get("url", "")
        line = f"- **{game}** ({platform})"
        if until:
            line += f" — 截止 {until}"
        if url:
            line += f"  [领取]({url})"
        lines.append(line)

    return "\n".join(lines)
