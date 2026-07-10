"""时效性验证器：页面日期提取 + LLM 交叉校验

阶段 4.1 — 页面日期提取：对 leak/release 条目 HTTP GET 目标页面，从 meta 标签提取真实发布时间
阶段 4.2 — LLM 交叉验证：输入 title + summary + published_at + 当前日期，判断时效性和分类
"""

import json
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from rich.console import Console
from rich.table import Table

from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, NEWS_WINDOW_DAYS

console = Console()

FETCH_TIMEOUT = 10

LLM_VALIDATE_PROMPT = """你是资讯时效性校验助手。当前日期: {current_date}，时间窗口: 近 {window_days} 天（从 {cutoff_date} 至今）。

请对以下每条新闻，逐条判断时效性和分类。

## 时效性判断标准 (date_confidence)

- **verified**: Google News日期或页面日期在窗口内，内容有近期时间标记（如具体日期、近日、本周、今天）
- **suspicious**: 内容没有明确时间标记，但日期在窗口内，无法确认也无法推翻
- **rejected**: 内容明确指向 {window_days} 天前的事件（如"3月发布"、"去年"、"半年前"），或日期明显在窗口外

## 分类判断 (sub_type 是否正确)

当前有4种子类型：
- **leak**: 新机爆料 — 未发布产品的传闻、泄露、专利曝光、预热预告、谍照、规格爆料
- **release**: 新机发售 — 已发布/开售/预售/到货/价格公布的新品
- **system**: 系统更新 — 固件/Bios/OS/CFW/驱动程序版本更新
- **general**: 其他资讯 — 评测、配件、行业分析等不属于以上三类

如果 sub_type 分错了，给出 corrected_sub_type。

## 输入

{items_json}

## 输出格式

返回 JSON 对象，key 为条目序号（字符串），不要遗漏任何条目：
{{"0": {{"date_confidence": "verified", "sub_type_ok": true, "corrected_sub_type": null, "reason": "明确提到7月8日"}}, "1": {{"date_confidence": "rejected", "sub_type_ok": true, "corrected_sub_type": null, "reason": "文中提到3月份"}}, ...}}

只返回 JSON 对象，不要 markdown。"""


def _extract_date_from_page(url: str) -> str | None:
    """从目标页面提取真实发布时间"""
    try:
        resp = requests.get(url, timeout=FETCH_TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (compatible; GamingNewsBot/1.0)"
        })
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 1. article:published_time / article:modified_time
        for attr in ("article:published_time", "article:modified_time"):
            meta = soup.find("meta", property=attr)
            if meta and meta.get("content"):
                return meta["content"]

        # 2. og:article:published_time
        meta = soup.find("meta", property="og:article:published_time")
        if meta and meta.get("content"):
            return meta["content"]

        # 3. schema.org JSON-LD datePublished
        for tag in soup.find_all("script", type="application/ld+json"):
            if not tag.string:
                continue
            try:
                data = json.loads(tag.string)
                if isinstance(data, dict):
                    for key in ("datePublished", "dateModified", "dateCreated"):
                        val = data.get(key)
                        if val:
                            return val
                elif isinstance(data, list):
                    for d in data:
                        if isinstance(d, dict):
                            for key in ("datePublished", "dateModified", "dateCreated"):
                                val = d.get(key)
                                if val:
                                    return val
            except (json.JSONDecodeError, AttributeError):
                pass

        # 4. <time datetime="...">
        time_tag = soup.find("time", datetime=True)
        if time_tag and time_tag.get("datetime"):
            return time_tag["datetime"]

        return None
    except requests.Timeout:
        return None
    except Exception:
        return None


def validate(selected: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """验证精选条目的时效性和分类

    对 rejected 条目从结果中移除，修正错误的 sub_type。
    """
    console.print("\n[bold]🔍 阶段 4：时效性验证[/bold]")

    now = datetime.now(timezone.utc)
    current_date = now.strftime("%Y-%m-%d")
    from config import CUTOFF_DATE
    cutoff_str = CUTOFF_DATE.strftime("%Y-%m-%d")

    # 展平条目
    all_items: list[tuple[str, dict]] = []
    for cat_key, cat_items in selected.items():
        for it in cat_items:
            all_items.append((cat_key, it))

    if not all_items:
        console.print("[dim]无条目需要验证[/dim]")
        return selected

    total = len(all_items)

    # ===== 阶段 4.1：页面日期提取 =====
    console.print("\n[yellow]  阶段 4.1：页面日期提取[/yellow]")
    extracted_count = 0
    eligible = 0

    for cat_key, it in all_items:
        url = it.get("url", "")
        sub_type = it.get("sub_type", "general")
        if sub_type not in ("leak", "release") or not url:
            continue
        eligible += 1

        page_date = _extract_date_from_page(url)
        if page_date:
            it["raw_data"]["page_date"] = page_date
            it["raw_data"]["date_source"] = "page_meta"
            extracted_count += 1
            console.log(f"[dim]    页面日期: {page_date[:25]} ← {it['title'][:60]}[/dim]")

    console.print(f"  提取成功: {extracted_count}/{eligible} (leak/release 条目)")

    # ===== 阶段 4.2：LLM 交叉验证 =====
    console.print("\n[yellow]  阶段 4.2：LLM 时效性交叉验证[/yellow]")

    if not OPENAI_API_KEY or OPENAI_API_KEY == "sk-xxx":
        console.print("[yellow]  未配置 LLM，跳过交叉验证[/yellow]")
        return selected

    validation_input = []
    for i, (_, it) in enumerate(all_items):
        date_info = it.get("published_at", "未知")
        extras = []
        if it.get("raw_data", {}).get("page_date"):
            extras.append(f"页面日期: {it['raw_data']['page_date']}")
        if it.get("raw_data", {}).get("date_source"):
            extras.append(f"来源: {it['raw_data']['date_source']}")
        if extras:
            date_info += " | " + " | ".join(extras)

        validation_input.append({
            "index": i,
            "title": it.get("title", "")[:150],
            "summary": it.get("summary", "")[:200],
            "published_at": date_info,
            "category": it.get("category", "unknown"),
            "sub_type": it.get("sub_type", "general"),
            "source_name": it.get("source_name", ""),
        })

    prompt = LLM_VALIDATE_PROMPT.format(
        current_date=current_date,
        window_days=NEWS_WINDOW_DAYS,
        cutoff_date=cutoff_str,
        items_json=json.dumps(validation_input, ensure_ascii=False, indent=2),
    )

    try:
        client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2000,
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            if content.endswith("```"):
                content = content[:-3]
        llm_results = json.loads(content)
    except Exception as e:
        console.log(f"[red]  LLM 验证失败: {e}，保留所有条目[/red]")
        return selected

    # ===== 应用结果 =====
    rejected_count = 0
    suspicious_count = 0
    corrected_count = 0

    for i_str, result in llm_results.items():
        idx = int(i_str)
        if idx >= len(all_items):
            continue

        cat_key, it = all_items[idx]
        confidence = result.get("date_confidence", "verified")
        sub_type_ok = result.get("sub_type_ok", True)
        corrected_st = result.get("corrected_sub_type")
        reason = result.get("reason", "")

        it["raw_data"]["llm_date_confidence"] = confidence
        it["raw_data"]["llm_reason"] = reason

        if confidence == "rejected":
            selected[cat_key].remove(it)
            rejected_count += 1
            console.log(f"[red]    ✗ 丢弃 [{cat_key}]: {it['title'][:60]} | {reason}[/red]")
            continue

        if not sub_type_ok and corrected_st in ("leak", "release", "system", "general"):
            old_st = it.get("sub_type", "?")
            it["sub_type"] = corrected_st
            corrected_count += 1
            console.log(f"[yellow]    ↻ 修正 [{old_st}→{corrected_st}]: {it['title'][:50]}[/yellow]")

        if confidence == "suspicious":
            suspicious_count += 1

    # ===== 报告 =====
    console.print()
    table = Table(title="时效性验证报告")
    table.add_column("指标", style="cyan")
    table.add_column("数值", style="green")

    leak_count = sum(1 for _, it in all_items if it.get("sub_type") == "leak")
    verified_count = total - rejected_count

    table.add_row("验证总数", str(total))
    table.add_row("通过", str(verified_count))
    table.add_row("存疑 (suspicious)", str(suspicious_count))
    table.add_row("丢弃 (rejected)", str(rejected_count))
    table.add_row("sub_type 修正", str(corrected_count))
    table.add_row("页面日期提取", f"{extracted_count}/{eligible}")
    table.add_row("最终爆料数 (leak)", str(leak_count))

    console.print(table)

    return selected
