#!/usr/bin/env python3
"""游戏设备资讯周刊 - 主入口

手动运行：python main.py
定时运行：python scheduler.py
"""

import os
import sys
from datetime import datetime, timezone

# 修复 Windows 控制台 emoji 编码问题
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console
from rich.table import Table

from config import (
    RSS_SOURCES,
    NEWS_WINDOW_DAYS,
    CUTOFF_DATE,
    OUTPUT_DIR,
    CATEGORIES,
    OPENAI_API_KEY,
)
from storage.db import init_db, insert_news_item, save_weekly_output, get_stats
from collectors.rss_collector import collect_all_rss
from collectors.web_search import WebSearchCollector
from collectors.chinese_web import ChineseWebCollector
from collectors.tieba_collector import TiebaCollector
from pipeline.dedup import deduplicate
from pipeline.filter import filter_by_date, get_week_label, get_week_range
from pipeline.ranker import select_top_items
from generator.script_writer import ScriptWriter

console = Console()


def print_banner():
    console.print("[bold cyan]========================================[/bold cyan]")
    console.print("[bold cyan]   游戏设备资讯周刊 - Gaming News Weekly   [/bold cyan]")
    console.print("[bold cyan]========================================[/bold cyan]")
    console.print()


def print_stats(stats: dict):
    table = Table(title="数据库统计")
    table.add_column("指标", style="cyan")
    table.add_column("数值", style="green")
    for k, v in stats.items():
        table.add_row(k, str(v))
    console.print(table)
    console.print()


def collect_all() -> list[dict]:
    """采集所有来源的新闻"""
    console.print("[bold]📡 阶段 1：数据采集[/bold]")
    all_items = []

    # RSS 源（包含 Reddit RSS）
    console.print("\n[yellow]RSS 源:[/yellow]")
    rss_items = collect_all_rss(RSS_SOURCES)
    all_items.extend(rss_items)

    # AI 联网搜索补充
    console.print("\n[yellow]Google News 搜索:[/yellow]")
    searcher = WebSearchCollector()
    search_items = searcher.fetch()
    all_items.extend(search_items)

    # 中文源补充（B站/知乎/什么值得买 等 via Google）
    console.print("\n[yellow]中文源补充 (B站/知乎/SMZDM):[/yellow]")
    cn = ChineseWebCollector()
    all_items.extend(cn.fetch())


    # 贴吧
    console.print("\n[yellow]贴吧:[/yellow]")
    tieba = TiebaCollector()
    all_items.extend(tieba.fetch())

    console.print(f"\n[bold]共采集 {len(all_items)} 条原始新闻[/bold]")
    return all_items


def classify_by_keywords(items: list[dict]) -> list[dict]:
    """使用关键词匹配进行分类（无需 LLM）"""
    from config import CATEGORIES

    for item in items:
        if item.get("category"):
            continue

        text = (item.get("title", "") + " " + item.get("summary", "")).lower()
        best_cat = None
        best_score = 0

        for cat_key, cat_info in CATEGORIES.items():
            score = 0
            keywords = cat_info.get("keywords", [])
            for kw in keywords:
                if kw.lower() in text:
                    score += 1
            if score > best_score:
                best_score = score
                best_cat = cat_key

        if best_cat and best_score > 0:
            item["category"] = best_cat

    return items


def process(all_items: list[dict]) -> dict[str, list[dict]]:
    """处理管道：去重 → 过滤 → 分类 → 排序"""
    console.print("\n[bold]🔧 阶段 2：处理管道[/bold]")

    # 1. 去重
    console.print("\n[yellow]去重:[/yellow]")
    deduped = deduplicate(all_items)

    # 2. 日期过滤
    console.print("\n[yellow]日期过滤 (近 {0} 天):[/yellow]".format(NEWS_WINDOW_DAYS))
    filtered = filter_by_date(deduped, CUTOFF_DATE)

    # 3. LLM 分类（可用时）或关键词兜底
    if OPENAI_API_KEY and OPENAI_API_KEY != "sk-xxx":
        console.print("\n[yellow]LLM 分类:[/yellow]")
        from pipeline.classifier import NewsClassifier, detect_sub_types, count_by_category
        classifier = NewsClassifier()
        classified = classifier.classify(filtered)
    else:
        console.print("\n[yellow]关键词分类:[/yellow]")
        classified = classify_by_keywords(filtered)

    # 过滤掉 LLM 标记为 irrelevant 的条目
    irrelevant = [it for it in classified if it.get("category") == "irrelevant"]
    if irrelevant:
        console.log(f"  丢弃无关条目: {len(irrelevant)} 条")
    classified = [it for it in classified if it.get("category") != "irrelevant"]

    # 子类型检测：新机爆料 / 新机发售
    console.print("\n[yellow]子类型检测 (爆料/发售):[/yellow]")
    classified = detect_sub_types(classified)
    leak_count = sum(1 for it in classified if it.get("sub_type") == "leak")
    release_count = sum(1 for it in classified if it.get("sub_type") == "release")
    system_count = sum(1 for it in classified if it.get("sub_type") == "system")
    general_count = sum(1 for it in classified if it.get("sub_type") == "general")
    console.print(f"  🔮 爆料: {leak_count}  |  🆕 发售: {release_count}  |  📱 系统: {system_count}  |  📋 其他: {general_count}")

    # 统计
    cat_counts = {}
    for item in classified:
        cat = item.get("category", "未分类")
        cat_name = CATEGORIES.get(cat, {}).get("name", cat)
        cat_counts[cat_name] = cat_counts.get(cat_name, 0) + 1
    for cat_name, count in sorted(cat_counts.items(), key=lambda x: str(x[0])):
        console.print(f"  {cat_name}: {count} 条")

    # 4. 排序精选
    console.print("\n[yellow]精选 Top 5:[/yellow]")
    selected = select_top_items(classified)

    return selected


def generate(selected: dict[str, list[dict]], week_label: str, week_range: str) -> str:
    """生成文稿（模板拼接，无需 LLM）"""
    console.print("\n[bold]✍️  阶段 3：文稿生成[/bold]")
    writer = ScriptWriter()
    markdown = writer.write(selected, week_label, week_range)
    return markdown


def save_output(markdown: str, week_label: str, selected: dict[str, list[dict]]):
    """保存文稿到文件"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, f"{week_label}.md")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    console.print(f"\n[bold green]✅ 文稿已保存: {output_path}[/bold green]")

    # 保存到数据库
    total_items = sum(len(v) for v in selected.values())
    stats = {cat: len(items) for cat, items in selected.items()}
    save_weekly_output(week_label, markdown, total_items, stats)


def run():
    """完整运行一次管道"""
    print_banner()

    # 初始化数据库
    init_db()
    print_stats(get_stats())

    week_label = get_week_label()
    week_range = get_week_range(CUTOFF_DATE)
    console.print(f"[bold]本周标签: {week_label} ({week_range})[/bold]\n")

    # 阶段 1：采集
    all_items = collect_all()

    if not all_items:
        console.print("[red]未采集到任何新闻，退出[/red]")
        return

    # 保存到数据库
    saved = 0
    for item in all_items:
        if insert_news_item(item):
            saved += 1
    console.print(f"[dim]新入库: {saved} 条[/dim]")

    # 阶段 2：处理
    selected = process(all_items)

    # 阶段 3：生成
    markdown = generate(selected, week_label, week_range)

    if markdown:
        # 保存
        save_output(markdown, week_label, selected)

        # 更新数据库中的分类信息
        from storage.db import DB_PATH
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        for items in selected.values():
            for item in items:
                if item.get("category"):
                    conn.execute(
                        "UPDATE news_items SET category = ? WHERE url = ?",
                        (item["category"], item["url"]),
                    )
        conn.commit()
        conn.close()

    console.print(f"\n[bold cyan]🎮 完成！共精选 {sum(len(v) for v in selected.values())} 条资讯[/bold cyan]")


if __name__ == "__main__":
    run()
