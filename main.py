#!/usr/bin/env python3
"""游戏设备资讯周刊 - 主入口

手动运行：python main.py
定时运行：python scheduler.py
"""

import os
import sys
import argparse
from datetime import datetime, timezone

# 修复 Windows 控制台 emoji 编码问题
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console
from rich.table import Table

from config import (
    LEAK_CUTOFF_DATE,
    LEAK_WINDOW_DAYS,
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
# ChineseBrowserCollector 已移除：SMZDM 贡献 0%，详见注释
# from collectors.chinese_browser_collector import ChineseBrowserCollector
from collectors.tieba_collector import TiebaCollector
from collectors.tieba_browser_collector import TiebaBrowserCollector
from collectors.bilibili_collector import BilibiliCollector
from collectors.bilibili_browser_collector import BilibiliBrowserCollector
from collectors.bilibili_article_collector import BilibiliArticleCollector
from pipeline.dedup import deduplicate
from pipeline.filter import filter_by_date, filter_content_quality, filter_topic_relevance, prune_expanded, get_week_label, get_week_range, recover_filtered_items, show_filter_stats, load_filtered_items
from pipeline.ranker import select_top_items
from pipeline.validator import validate
from pipeline.image_fetcher import fetch_images
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


def _print_source_stats(all_items: list[dict]):
    """打印来源统计面板"""
    from collections import Counter
    src_counter = Counter()
    for it in all_items:
        st = it.get("source_type", "unknown")
        if st.startswith("bilibili_"):
            src_counter["bilibili_*"] += 1
        elif st.startswith("tieba_"):
            src_counter["tieba_*"] += 1
        elif "reddit" in st.lower():
            src_counter["reddit_rss"] += 1
        elif st in ("rss", "web_search", "chinese_web", "zhihu_browser", "smzdm_browser"):
            src_counter[st] += 1
        else:
            src_counter[st] += 1

    console.print(f"\n[bold]共采集 {len(all_items)} 条原始新闻[/bold]")
    if src_counter:
        parts = []
        for src, cnt in src_counter.most_common(8):
            parts.append(f"{src}:{cnt}")
        console.print(f"[dim]  {' | '.join(parts)}[/dim]")


def _save_ci_raw_items(items: list[dict]):
    """保存 CI 原始采集数据，供本地 --from-ci 模式复用"""
    import json
    ci_path = os.path.join(OUTPUT_DIR, ".ci_raw_items.json")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(ci_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _load_ci_raw_items() -> list[dict]:
    """加载 CI 原始采集数据"""
    import json
    ci_path = os.path.join(OUTPUT_DIR, ".ci_raw_items.json")
    if not os.path.exists(ci_path):
        console.print(f"[red]未找到 CI 数据文件: {ci_path}[/red]")
        console.print("[dim]请先 git pull 拉取 CI 最新产出[/dim]")
        return []
    with open(ci_path, "r", encoding="utf-8") as f:
        return json.load(f)


def collect_ci() -> list[dict]:
    """CI 专属采集：RSS + Google News + B站搜索 + 贴吧RSS（无需浏览器）"""
    items = []

    console.print("\n[yellow]RSS 源:[/yellow]")
    items.extend(collect_all_rss(RSS_SOURCES))

    console.print("\n[yellow]Google News 搜索:[/yellow]")
    searcher = WebSearchCollector()
    items.extend(searcher.fetch())

    console.print("\n[yellow]中文源补充 (B站/知乎/SMZDM):[/yellow]")
    cn = ChineseWebCollector()
    items.extend(cn.fetch())

    console.print("\n[yellow]B站搜索采集:[/yellow]")
    bilibili = BilibiliCollector()
    items.extend(bilibili.fetch())

    console.print("\n[yellow]贴吧 (Google News):[/yellow]")
    tieba = TiebaCollector()
    items.extend(tieba.fetch())

    return items


def collect_browsers() -> list[dict]:
    """本地专属采集：B站浏览器 + 贴吧浏览器（需要真实浏览器环境）"""
    items = []

    # B站（浏览器视频+文章，共享浏览器实例）
    if os.getenv("BILIBILI_BROWSER", "").lower() in ("1", "true", "yes"):
        browser_ok = False
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

                try:
                    page.goto("https://www.bilibili.com", wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(2000)
                except Exception:
                    pass

                sessdata = os.getenv("BILIBILI_SESSDATA", "").strip()
                if sessdata:
                    context.add_cookies([{
                        "name": "SESSDATA",
                        "value": sessdata,
                        "domain": ".bilibili.com",
                        "path": "/",
                    }])

                console.print("[dim]  — 视频搜索 + 字幕提取 —[/dim]")
                try:
                    bilibili_browser.set_page(page)
                    items.extend(bilibili_browser.fetch())
                except Exception as e:
                    console.log(f"[yellow]⚠ B站浏览器视频采集失败: {e}, 降级跳过[/yellow]")

                console.print("[dim]  — 专栏文章采集 —[/dim]")
                try:
                    bilibili_article.set_page(page)
                    items.extend(bilibili_article.fetch())
                except Exception as e:
                    console.log(f"[yellow]⚠ B站浏览器文章采集失败: {e}, 降级跳过[/yellow]")

                browser.close()
                browser_ok = True
                console.print("[green]B站浏览器采集完成 (视频+文章共享实例)[/green]")
        except ImportError:
            console.log("[red]playwright 未安装，跳过 B站浏览器采集[/red]")
        except Exception as e:
            console.log(f"[yellow]⚠ B站浏览器采集不可用 ({e}), 自动降级[/yellow]")

        if not browser_ok:
            console.print("[dim]  B站浏览器降级: Google News RSS 基线数据已就位[/dim]")
    else:
        console.print("[dim]  BILIBILI_BROWSER 未开启，跳过 B站浏览器采集[/dim]")

    # 贴吧（浏览器直接抓取，覆盖面更全）
    console.print("\n[yellow]贴吧 (浏览器):[/yellow]")
    tieba_browser = TiebaBrowserCollector()
    items.extend(tieba_browser.fetch())

    return items


def collect_all() -> list[dict]:
    """采集所有来源的新闻（CI + 本地浏览器）"""
    console.print("[bold]📡 阶段 1：数据采集[/bold]")
    all_items = collect_ci()
    all_items.extend(collect_browsers())
    _print_source_stats(all_items)
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

    # 1.5. 内容质量过滤（微博空条目/截断/占位符）
    deduped, _quality_removed = filter_content_quality(deduped)

    # 2. 日期过滤
    console.print("\n[yellow]日期过滤 (近 {0} 天, leak宽限 {1} 天):[/yellow]".format(NEWS_WINDOW_DAYS, LEAK_WINDOW_DAYS))
    filtered, leak_candidates = filter_by_date(deduped, CUTOFF_DATE, LEAK_CUTOFF_DATE)

    # 3. LLM 分类（可用时）或关键词兜底
    if OPENAI_API_KEY and OPENAI_API_KEY != "sk-xxx":
        console.print("\n[yellow]LLM 分类:[/yellow]")
        from pipeline.classifier import NewsClassifier, detect_sub_types, count_by_category
        classifier = NewsClassifier()
        classified = classifier.classify(filtered)
    else:
        console.print("\n[yellow]关键词分类:[/yellow]")
        classified = classify_by_keywords(filtered)

    # 3.5 设备映射校正 — 用已知设备型号修正跨品牌分类错误
    from pipeline.device_os_map import reclassify_items
    corrected, dev_stats = reclassify_items(classified)
    if corrected > 0:
        console.print(f"[dim]  设备映射校正: {corrected} 条 ({dev_stats})[/dim]")

    # 过滤掉 LLM 标记为 irrelevant 的条目
    irrelevant = [it for it in classified if it.get("category") == "irrelevant"]
    if irrelevant:
        console.log(f"  丢弃无关条目: {len(irrelevant)} 条")
    classified = [it for it in classified if it.get("category") != "irrelevant"]

    # 4.0. 话题相关性兜底过滤（LLM 标记 irrelevant 之外的漏网之鱼）
    classified, _topic_removed = filter_topic_relevance(classified)

    # 子类型检测：新机爆料 / 新机发售
    console.print("\n[yellow]子类型检测 (爆料/发售):[/yellow]")
    classified = detect_sub_types(classified)
    leak_count = sum(1 for it in classified if it.get("sub_type") == "leak")
    release_count = sum(1 for it in classified if it.get("sub_type") == "release")
    system_count = sum(1 for it in classified if it.get("sub_type") == "system")
    general_count = sum(1 for it in classified if it.get("sub_type") == "general")
    console.print(f"  🔮 爆料: {leak_count}  |  🆕 发售: {release_count}  |  📱 系统: {system_count}  |  📋 其他: {general_count}")

    # 扩展窗口回收：只保留 sub_type=leak 的候选，剔除其余
    leak_signals = []
    if leak_candidates:
        leak_signals = [it for it in classified if it.get("raw_data", {}).get("_expanded_window")
                        and it.get("sub_type") == "leak"]
        classified = [it for it in classified if not it.get("raw_data", {}).get("_expanded_window")
                      or it.get("sub_type") == "leak"]
        console.print(f"  [dim]扩展窗口: {len(leak_candidates)} 条候选 → {len(leak_signals)} 条leak保留"
                      f" (剔除 {len(leak_candidates) - len(leak_signals)} 条非leak)[/dim]")

    # 3.5 预告→跟进闭环
    #   a) 当前 leak 信号 → 存 DB
    #   b) DB 历史信号 + 当前 leak → 补充搜索
    #   c) 补充结果 → 标记 DB 信号 found
    from pipeline.leak_followup import supplement_search, extract_product_names, store_leak_signals, mark_signals_found

    # 存当前 leak 产品名到 DB（含窗口内和扩展窗口的所有 leak）
    all_leaks = [it for it in classified if it.get("sub_type") == "leak"]
    if all_leaks:
        store_leak_signals(all_leaks)

    # 补充搜索（当前 + DB 历史）
    new_items = supplement_search(all_leaks if all_leaks else None)
    if new_items:
        unique_new = []
        seen = set()
        for item in new_items:
            nid = (item.get("url", ""), item.get("title", ""))
            if nid not in seen:
                seen.add(nid)
                item["category"] = None
                item["raw_data"] = item.get("raw_data", {})
                unique_new.append(item)
        if unique_new:
            classified.extend(unique_new)
            console.print(f"  [green]  补充 {len(unique_new)} 条 → 重新分类...[/green]")
            from pipeline.classifier import NewsClassifier, detect_sub_types
            if OPENAI_API_KEY:
                clf2 = NewsClassifier()
                classified = clf2.classify(classified)
            else:
                classified = classify_by_keywords(classified)
            classified = [it for it in classified if it.get("category") != "irrelevant"]
            classified = detect_sub_types(classified)
            # 标记已找到
            mark_signals_found(unique_new)

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
            rate_style = "green" if (total > 0 and dated / total >= 0.7) else "red"
            low_text = f"[red]{low}[/red]" if low else "0"
            src_table.add_row(
                src, str(total), str(dated), low_text,
                f"[{rate_style}]{rate}[/{rate_style}]",
            )

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


def run(recover_reasons: list[str] | None = None, recover_items: list[dict] | None = None,
        from_ci: bool = False):
    """完整运行一次管道。

    Args:
        recover_reasons: 过滤原因列表（如 ["too_short"]），跳过采集阶段回捞。
        recover_items: 直接指定要回捞的条目列表（--recover-reviewed 模式）。
        from_ci: CI 增量模式 — 跳过 RSS/Google News，加载 CI 数据 + 仅采集浏览器源。
    """
    print_banner()

    # 初始化数据库
    init_db()
    print_stats(get_stats())

    week_label = get_week_label()
    week_range = get_week_range(CUTOFF_DATE)
    console.print(f"[bold]本周标签: {week_label} ({week_range})[/bold]\n")

    if recover_items is not None:
        # === 审核回捞模式：直接使用传入的条目 ===
        recovered = recover_items
        recover_mode_label = "审核勾选"
    elif recover_reasons:
        # === 批量回捞模式：按原因从日志恢复 ===
        recover_mode_label = ", ".join(recover_reasons)

        # 显示当前过滤统计
        stats = show_filter_stats()
        if stats:
            console.print("[dim]当前过滤日志统计:[/dim]")
            for reason, count in sorted(stats.items(), key=lambda x: -x[1]):
                console.print(f"  [dim]{reason}: {count} 条[/dim]")

        recovered = recover_filtered_items(reasons=recover_reasons)
        if not recovered:
            console.print(f"[yellow]没有匹配 '{recover_reasons}' 的过滤条目，退出[/yellow]")
            return
    else:
        recovered = None

    if recovered:
        console.print(f"[bold yellow]🔁 回捞模式: {recover_mode_label}[/bold yellow]\n")
        console.print(f"[green]回捞 {len(recovered)} 条被过滤条目[/green]")
        for it in recovered[:10]:
            console.print(f"  [dim]+ {it.get('title', '')[:60]}[/dim]")
        if len(recovered) > 10:
            console.print(f"  [dim]... 及其他 {len(recovered) - 10} 条[/dim]")

        # 加载上次采集的原始数据
        raw_items = load_raw_checkpoint()
        if not raw_items:
            console.print("[red]未找到上次采集的 checkpoint，无法回捞。请先完整运行一次管道。[/red]")
            return

        console.print(f"[dim]已加载采集 checkpoint: {len(raw_items)} 条[/dim]")

        # 合并回捞条目
        all_items = raw_items + recovered
        console.print(f"[dim]合并后: {len(all_items)} 条 (原始 {len(raw_items)} + 回捞 {len(recovered)})[/dim]")
    elif from_ci:
        # === CI 增量模式：加载 CI 数据 + 仅采集浏览器源 ===
        console.print("[bold]📡 阶段 1：CI 增量模式[/bold]")
        console.print("[dim]跳过 RSS / Google News，加载 CI 已采集数据[/dim]")

        ci_items = _load_ci_raw_items()
        if not ci_items:
            console.print("[red]未加载到 CI 数据，退出。请先 git pull 拉取最新 CI 产出。[/red]")
            return
        console.print(f"[dim]加载 CI 数据: {len(ci_items)} 条[/dim]")

        console.print("[yellow]仅本地浏览器采集:[/yellow]")
        browser_items = collect_browsers()
        console.print(f"[dim]本地浏览器: {len(browser_items)} 条[/dim]")

        all_items = ci_items + browser_items
        _print_source_stats(all_items)

        if not all_items:
            console.print("[red]未采集到任何新闻，退出[/red]")
            return

        # 保存 checkpoint（供后续 --recover 使用）
        save_raw_checkpoint(all_items)
        console.print(f"[dim]已保存 checkpoint: {len(all_items)} 条[/dim]")

    else:
        # === 正常模式：完整采集 + 处理 ===
        console.print("[bold]📡 阶段 1：数据采集[/bold]")

        # 先跑 CI 覆盖的部分
        ci_items = collect_ci()
        # 导出 CI 数据供后续 --from-ci 复用（仅 CI 环境，避免本地污染 git）
        if os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
            _save_ci_raw_items(ci_items)
            console.print(f"[dim]已导出 CI 数据: {len(ci_items)} 条[/dim]")

        # 再跑本地浏览器部分
        browser_items = collect_browsers()
        all_items = ci_items + browser_items
        _print_source_stats(all_items)

        if not all_items:
            console.print("[red]未采集到任何新闻，退出[/red]")
            return

        # 交叉来源补全（短摘要 RSS → B站 搜索）
        try:
            from pipeline.enrich import enrich_thin_items
            enriched = enrich_thin_items(all_items)
        except Exception as e:
            console.print(f"[yellow]交叉补全失败(非致命): {e}[/yellow]")

        # 保存原始采集 checkpoint（防中途崩溃 + 供回捞模式使用）
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

    # 阶段 2.6：配图抓取
    selected = fetch_images(selected)

    # 阶段 3：生成
    markdown = generate(selected, week_label, week_range)

    # 可选：追加游戏折扣
    if os.getenv("GAME_DEALS", "").lower() in ("1", "true", "yes"):
        try:
            from deals.fetcher import fetch_and_format
            deals_md = fetch_and_format()
            if deals_md:
                markdown += "\n\n---\n\n" + deals_md
                console.print("[green]游戏折扣已追加[/green]")
        except Exception as e:
            console.print(f"[yellow]游戏折扣抓取失败: {e}[/yellow]")

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
    parser = argparse.ArgumentParser(description="游戏设备资讯周刊 - Gaming News Weekly")
    parser.add_argument(
        "--recover",
        type=str,
        default=None,
        help="回捞被过滤条目，跳过采集直接重新处理（按原因批量回捞）。"
             "用法: --recover too_short | --recover too_short,topic_irrelevant | --recover all",
    )
    parser.add_argument(
        "--recover-reviewed",
        action="store_true",
        default=False,
        help="根据审核清单 output/.filtered_review.md 中勾选的条目回捞。"
             "先用 python review_filtered.py --open 审核并勾选误踢条目。",
    )
    parser.add_argument(
        "--from-ci",
        action="store_true",
        default=False,
        help="CI 增量模式 — 跳过 RSS/Google News，加载 CI 已采集数据，仅运行本地浏览器采集。"
             "本地先 git pull，再 python main.py --from-ci。",
    )
    args = parser.parse_args()

    if args.from_ci:
        run(from_ci=True)
    elif args.recover_reviewed:
        from review_filtered import load_checked_items
        checked_titles = load_checked_items()
        if not checked_titles:
            console.print("[yellow]审核清单中未勾选任何条目（- [x]），退出[/yellow]")
            sys.exit(0)

        console.print(f"[green]审核清单中勾选了 {len(checked_titles)} 条[/green]")

        # 从过滤日志中匹配勾选的标题 → 获取完整条目数据回捞
        all_filtered = load_filtered_items()
        title_to_item = {}
        for it in all_filtered:
            t = (it.get("title") or "").strip()
            title_to_item[t] = it

        matched = []
        not_found = []
        for title in checked_titles:
            if title in title_to_item:
                matched.append(title_to_item[title])
            else:
                not_found.append(title)

        if not_found:
            console.print(f"[yellow]有 {len(not_found)} 条在过滤日志中未找到，跳过[/yellow]")

        if not matched:
            console.print("[yellow]没有匹配到可回捞的条目，退出[/yellow]")
            sys.exit(0)

        # 构造成管道兼容格式
        recovered = []
        for it in matched:
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

        console.print(f"[green]成功回捞 {len(recovered)} 条审核通过的条目[/green]")
        run(recover_items=recovered)
    elif args.recover:
        if args.recover.lower() == "all":
            reasons = None
        else:
            reasons = [r.strip() for r in args.recover.split(",") if r.strip()]
        run(recover_reasons=reasons)
    else:
        run()
