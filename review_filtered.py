#!/usr/bin/env python3
"""过滤条目审核工具 — 生成 Markdown 清单，方便可视化审核误踢条目。

用法:
    python review_filtered.py              # 生成审核清单
    python review_filtered.py --open       # 生成并用默认编辑器打开
    python review_filtered.py --stats      # 仅显示过滤统计

审核流程:
    1. python review_filtered.py --open
    2. 在打开的 Markdown 中，将误踢条目的 - [ ] 改为 - [x]
    3. 保存文件
    4. python main.py --recover-reviewed
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR
from pipeline.filter import show_filter_stats, _FILTERED_LOG_PATH

REVIEW_FILE = os.path.join(OUTPUT_DIR, ".filtered_review.md")


def _reason_label(reason: str) -> str:
    labels = {
        "too_short": "内容过短 (<阈值字符)",
        "social_placeholder": "社交媒体占位符",
        "tieba_user_page": "贴吧用户动态页",
        "url_encoded_garbage": "URL编码垃圾标题",
        "incomplete_content": "截断/内容待补充",
        "image_only_no_text": "仅图片无文字",
        "no_summary_short_title": "无摘要+短标题",
        "topic_irrelevant": "话题不相关",
    }
    return labels.get(reason, reason)


def generate_review():
    """生成审核 Markdown 清单"""
    try:
        with open(_FILTERED_LOG_PATH, "r", encoding="utf-8") as f:
            log = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("没有找到过滤日志。请先运行一次管道。")
        return None

    runs = log.get("runs", [])
    if not runs:
        print("过滤日志为空。")
        return None

    # 收集所有条目并按原因分组
    by_reason: dict[str, list[dict]] = {}
    for run in runs:
        for it in run.get("items", []):
            reason = it.get("filter_reason", "unknown")
            it["_filtered_at"] = run.get("filtered_at", "")
            it["_filter_name"] = run.get("filter_name", "")
            by_reason.setdefault(reason, []).append(it)

    # 生成 Markdown
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = sum(len(v) for v in by_reason.values())

    lines = [
        f"# 过滤条目审核清单",
        f"",
        f"> 生成时间: {now}  |  共 {total} 条被过滤  |  {len(by_reason)} 种原因",
        f"",
        f"## 操作说明",
        f"",
        f"1. 浏览下方按原因分组的被过滤条目",
        f"2. 将**误踢条目**前的 `- [ ]` 改为 `- [x]`",
        f"3. 保存此文件",
        f"4. 运行 `python main.py --recover-reviewed` 回捞并重新生成周刊",
        f"",
        f"---",
        f"",
        f"## 过滤统计",
        f"",
    ]

    for reason, count in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        label = _reason_label(reason)
        lines.append(f"- **{label}** (`{reason}`): {count} 条")

    lines.append("")
    lines.append("---")
    lines.append("")

    for reason in sorted(by_reason.keys(), key=lambda r: -len(by_reason[r])):
        items = by_reason[reason]
        label = _reason_label(reason)
        lines.append(f"## {label} ({len(items)} 条)")
        lines.append("")
        lines.append(f"| # | 勾选 | 标题 | 来源 | 字符数 | 摘要 |")
        lines.append(f"|---|---|---|---|---|---|")

        for idx, it in enumerate(items, 1):
            title = (it.get("title") or "无标题").replace("|", "\\|")
            source = it.get("source_type", "?")
            summary = (it.get("summary") or "")[:80].replace("|", "\\|")
            combined_len = len((title + " " + summary).strip())

            # 用 URL 的最后部分做唯一标识
            url = it.get("url", f"idx-{idx}")
            url_hash = abs(hash(url)) % 100000

            lines.append(
                f"| {idx} | - [ ] | {title} | {source} | {combined_len} | {summary} |"
            )

        lines.append("")

    content = "\n".join(lines)

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    with open(REVIEW_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    return REVIEW_FILE


def open_file(filepath: str):
    """用系统默认程序打开文件"""
    if sys.platform == "win32":
        os.startfile(filepath)
    elif sys.platform == "darwin":
        subprocess.run(["open", filepath])
    else:
        subprocess.run(["xdg-open", filepath])


def load_checked_items() -> list[str]:
    """读取审核清单中 - [x] 勾选的条目 URL，返回需回捞的 URL 列表."""
    if not os.path.exists(REVIEW_FILE):
        print(f"审核清单不存在: {REVIEW_FILE}")
        print("请先运行 python review_filtered.py 生成清单")
        return []

    with open(REVIEW_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # 解析表格行，找到 - [x] 的行
    # 格式: | # | - [x] | title | source | chars | summary |
    # split 后: ['', ' # ', ' - [x] ', ' title ', ' source ', ' chars ', ' summary ', '']
    checked_titles = []
    for line in content.split("\n"):
        if "- [x]" in line and "|" in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 5:
                checkbox = parts[2]  # 第 2 列是勾选框
                title = parts[3]     # 第 3 列是标题
                if "x" in checkbox and title and title != "勾选":
                    checked_titles.append(title)

    return checked_titles


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--stats":
        stats = show_filter_stats()
        if not stats:
            print("没有过滤日志。")
        else:
            print("过滤统计:")
            for reason, count in sorted(stats.items(), key=lambda x: -x[1]):
                print(f"  {_reason_label(reason)} ({reason}): {count}")
            print(f"  合计: {sum(stats.values())} 条")
    else:
        path = generate_review()
        if path:
            print(f"审核清单已生成: {path}")
            if "--open" in sys.argv:
                open_file(path)
                print("已打开审核清单，审核完成后运行:")
                print("  python main.py --recover-reviewed")
