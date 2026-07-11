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
from pipeline.checkpoint import (
    save_raw_checkpoint, load_raw_checkpoint,
    save_selected_checkpoint, load_selected_checkpoint,
    clear_checkpoints,
)
from storage.db import init_db, insert_news_item, save_weekly_output, get_stats
from collectors.rss_collector import collect_all_rss
from collectors.web_search import WebSearchCollector
from collectors.chinese_web import ChineseWebCollector
from collectors.chinese_browser_collector import ChineseBrowserCollector
from collectors.tieba_collector import TiebaCollector
from collectors.tieba_browser_collector import TiebaBrowserCollector
from collectors.bilibili_collector import BilibiliCollector
from collectors.bilibili_account_collector import BilibiliAccountCollector
from collectors.bilibili_browser_collector import BilibiliBrowserCollector
from collectors.bilibili_article_collector import BilibiliArticleCollector
from pipeline.dedup import deduplicate
from pipeline.filter import filter_by_date, get_week_label, get_week_range
from pipeline.ranker import select_top_items
from pipeline.validator import validate
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

    # 知乎/什么值得买（浏览器直接搜索，覆盖面更全）
    console.print("\n[yellow]知乎/什么值得买 (浏览器):[/yellow]")
    cn_browser = ChineseBrowserCollector()
    all_items.extend(cn_browser.fetch())

    # B站搜索采集
    console.print("\n[yellow]B站搜索采集:[/yellow]")
    bilibili = BilibiliCollector()
    all_items.extend(bilibili.fetch())

    # B站厂商官号监控
    console.print("\n[yellow]B站厂商官号:[/yellow]")
    bilibili_acct = BilibiliAccountCollector()
    all_items.extend(bilibili_acct.fetch())

    # B站（浏览器视频+文章，共享浏览器实例）
    if os.getenv("BILIBILI_BROWSER", "").lower() in ("1", "true", "yes"):
        try:
            from playwright.sync_api import sync_playwright
            console.print("\n[yellow]B站 (浏览器 — 视频+文章):[/yellow]")
            bilibili_browser = BilibiliBrowserCollector()
            bilibili_article = BilibiliArticleCollector()

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                    ],
                )
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/130.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1920, "height": 1080},
                    locale="zh-CN",
                )
                page = context.new_page()
                page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => false });
                """)

                # 预热
                try:
                    page.goto("https://www.bilibili.com", wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(2000)
                except Exception:
                    pass

                # 视频采集
                console.print("[dim]  — 视频搜索 + 字幕提取 —[/dim]")
                bilibili_browser.set_page(page)
                all_items.extend(bilibili_browser.fetch())

                # 文章采集
                console.print("[dim]  — 专栏文章采集 —[/dim]")
                bilibili_article.set_page(page)
                all_items.extend(bilibili_article.fetch())

                browser.close()
                console.print("[green]B站浏览器采集完成 (视频+文章共享实例)[/green]")
        except ImportError:
            console.log("[red]playwright 未安装，跳过 B站浏览器采集[/red]")
    else:
        # 无浏览器时仍然走 Google News RSS 中转
        console.print("\n[yellow]B站搜索采集:[/yellow]")
        bilibili = BilibiliCollector()
        all_items.extend(bilibili.fetch())
        console.print("\n[yellow]B站厂商官号:[/yellow]")
        bilibili_acct = BilibiliAccountCollector()
        all_items.extend(bilibili_acct.fetch())

    # 贴吧（Google News RSS 中转）
    console.print("\n[yellow]贴吧 (Google News):[/yellow]")
    tieba = TiebaCollector()
    all_items.extend(tieba.fetch())

    # 贴吧（浏览器直接抓取，覆盖面更全）
    console.print("\n[yellow]贴吧 (浏览器):[/yellow]")
    tieba_browser = TiebaBrowserCollector()
    all_items.extend(tieba_browser.fetch())

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


def print_audit_report(selected: dict[str, list[dict]]):
    """输出时效性审计报告：日期置信度分布、来源质量"""
    console.print("\n[bold]📊 时效性审计报告[/bold]")

    table = Table(title="按分类 — 日期置信度分布")
    table.add_column("分类", style="cyan")
    table.add_column("总数")
    table.add_column("有日期")
    table.add_column("无日期(low)")
    table.add_column("llm verified")
    table.add_column("llm suspicious")

    for cat_key, items in selected.items():
        cat_name = CATEGORIES.get(cat_key, {}).get("name", cat_key)
        total = len(items)
        has_date = sum(1 for it in items if it.get("published_at"))
        low_date = sum(1 for it in items
                       if it.get("raw_data", {}).get("date_confidence") == "low")
        verified = sum(1 for it in items
                       if it.get("raw_data", {}).get("llm_date_confidence") == "verified")
        suspicious = sum(1 for it in items
                         if it.get("raw_data", {}).get("llm_date_confidence") == "suspicious")
        table.add_row(cat_name, str(total), str(has_date),
                      f"[red]{low_date}[/red]" if low_date else "0",
                      f"[green]{verified}[/green]" if verified else "0",
                      f"[yellow]{suspicious}[/yellow]" if suspicious else "0")

    console.print(table)

    # 来源质量统计
    source_stats: dict[str, dict] = {}
    for items in selected.values():
        for it in items:
            src = it.get("source_type", "unknown")
            if src not in source_stats:
                source_stats[src] = {"total": 0, "dated": 0, "low": 0}
            source_stats[src]["total"] += 1
            if it.get("published_at"):
                source_stats[src]["dated"] += 1
            if it.get("raw_data", {}).get("date_confidence") == "low":
                source_stats[src]["low"] += 1

    if source_stats:
        console.print()
        src_table = Table(title="按来源类型 — 日期质量")
        src_table.add_column("来源类型", style="cyan")
        src_table.add_column("总数")
        src_table.add_column("有日期")
        src_table.add_column("无日期")
        src_table.add_column("日期覆盖率")

        for src, stats in sorted(source_stats.items(), key=lambda x: -x[1]["dated"] / max(x[1]["total"], 1)):
            total = stats["total"]
            dated = stats["dated"]
            rate = f"{dated / total * 100:.0f}%" if total > 0 else "N/A"
            low = stats["low"]
            style = "[green]" if (total > 0 and dated / total >= 0.7) else "[red]"
            src_table.add_row(src, str(total), str(dated),
                            f"[red]{low}[/red]" if low else "0",
                            f"{style}{rate}[/style]")

        console.print(src_table)


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

    # 保存原始采集 checkpoint（防中途崩溃）
    save_raw_checkpoint(all_items)
    console.print(f"[dim]已保存采集 checkpoint: {len(all_items)} 条[/dim]")

    # 保存到数据库
    saved = 0
    for item in all_items:
        if insert_news_item(item):
            saved += 1
    console.print(f"[dim]新入库: {saved} 条[/dim]")

    # 阶段 2：处理
    selected = process(all_items)

    # 保存精选 checkpoint
    save_selected_checkpoint(selected)
    console.print(f"[dim]已保存精选 checkpoint: {sum(len(v) for v in selected.values())} 条[/dim]")

    # 阶段 2.5：时效性验证（页面日期提取 + LLM 交叉校验）
    selected = validate(selected)

    # 时效性审计报告
    print_audit_report(selected)

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

    # 清理 checkpoint（管道完整运行成功）
    clear_checkpoints()


if __name__ == "__main__":
    run()
