"""文稿生成器：按板块分别调用 LLM 生成新闻播报 + 简要分析"""

import re
from datetime import datetime
from difflib import SequenceMatcher
from rich.console import Console
from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, CATEGORIES
from .citation import generate_citations_block

console = Console()

CROSS_DEDUP_SIMILARITY = 0.72


def _normalize(title: str) -> str:
    text = title.lower().strip()
    text = re.sub(r"[^\w一-鿿]", "", text)
    return text


def _cross_category_dedup(categorized: dict[str, list[dict]]) -> int:
    """跨板块去重：同一条 URL 或高相似度标题在不同板块出现时，保留首个，从其余板块移除"""
    removed = 0
    cat_keys = list(categorized.keys())
    seen_urls: set[str] = set()
    seen_titles: list[tuple[str, str]] = []  # (normalized_title, cat_key)

    for cat_key in cat_keys:
        items = categorized.get(cat_key, [])
        keep = []
        for it in items:
            url = it.get("url", "").strip()
            title = it.get("title", "").strip()

            # URL 精确匹配
            if url and url in seen_urls:
                removed += 1
                console.log(f"[yellow]   跨板块去重(URL) [{cat_key}]: {title[:50]}[/yellow]")
                continue

            # 标题相似度匹配
            norm_title = _normalize(title)
            is_dup = False
            for seen_norm, seen_cat in seen_titles:
                if SequenceMatcher(None, norm_title, seen_norm).ratio() > CROSS_DEDUP_SIMILARITY:
                    removed += 1
                    is_dup = True
                    console.log(f"[yellow]   跨板块去重(标题) [{cat_key}←{seen_cat}]: {title[:50]}[/yellow]")
                    break
            if is_dup:
                continue

            if url:
                seen_urls.add(url)
            seen_titles.append((norm_title, cat_key))
            keep.append(it)
        categorized[cat_key] = keep

    if removed > 0:
        console.log(f"[green]  跨板块去重完成，移除 {removed} 条重复[/green]")
    return removed

SECTION_PROMPT = """你是游戏设备资讯周报的编辑。请根据以下 {cat_name} 板块的新闻条目，生成该板块的新闻播报文稿。

## 要求

1. **格式**：
   ```
   ## {cat_name}

   ### 🔮 新机爆料
   [按条目逐条播报]

   ### 🆕 新机发售
   [按条目逐条播报]

   ### 📱 系统更新
   [按条目逐条播报]

   ### 📋 其他资讯
   [按条目逐条播报]
   ```
   没有对应内容的子板块直接跳过。

2. **每条格式**：
   ```
   #### [序号]. 新闻标题
   ![配图](图片URL)   ← 如果提供的 JSON 中有 image_url 字段，必须插入配图
   - 新闻内容：用约150字讲清楚事件（2-3句话），口播稿风格，包含背景+事件+具体细节（型号、规格、价格、日期等），写完后默念确认能顺畅读出
   - 简要分析：用约80字简要分析（1-2句话），有观点的解读，说明事件对品牌/行业的影响或值得关注的原因，避免套话
   - 来源: [来源名称]
   ```
   **字数要求**：新闻内容不少于150字，简要分析不少于80字。写完后默念一遍，确保口播流畅、内容饱满。
   **配图要求**：每条有 image_url 的新闻都必须插入 `![配图](URL)`，没有 image_url 的则跳过。

3. **风格**：新闻播报体（不是口播稿），客观准确，简练专业。对爆料类内容可稍加分析其可信度和影响。
4. **保留全部条目**：每一条输入的新闻都必须出现在输出中，不要省略。
5. 末尾输出该板块的参考资料链接列表。

## 新闻数据

{items_json}

请生成 {cat_name} 板块的完整新闻播报文稿。"""


class ScriptWriter:
    def __init__(self):
        self.client = None
        if OPENAI_API_KEY and OPENAI_API_KEY != "sk-xxx":
            self.client = OpenAI(
                api_key=OPENAI_API_KEY,
                base_url=OPENAI_BASE_URL,
            )

    def write(self, categorized_items: dict[str, list[dict]], week_label: str, week_range: str) -> str:
        """按板块分别生成，合并为完整周报"""
        if not categorized_items:
            console.log("[red]没有精选条目可生成文稿[/red]")
            return ""

        lines = [f"# 游戏设备周报 | {week_label} ({week_range})\n"]

        # 跨板块去重
        _cross_category_dedup(categorized_items)

        all_items = []
        total_count = 0

        for cat_key in CATEGORIES:
            items = categorized_items.get(cat_key, [])
            if not items:
                continue

            cat_name = CATEGORIES[cat_key]["name"]
            all_items.extend(items)

            if self.client:
                section = self._generate_section(cat_name, items)
            else:
                section = self._template_section(cat_name, items, total_count)
                total_count += len(items)

            lines.append(section)
            lines.append("")

        lines.append(generate_citations_block(all_items))

        report = "\n".join(lines)
        console.log(f"[green]周报生成完成: {sum(len(v) for v in categorized_items.values())} 条资讯[/green]")
        return report

    def _generate_section(self, cat_name: str, items: list[dict]) -> str:
        """调用 LLM 生成一个板块的新闻播报"""
        import json

        # 准备 LLM 输入
        news_input = []
        for it in items:
            entry = {
                "title": it.get("title", ""),
                "summary": it.get("summary", "")[:300],
                "sub_type": it.get("sub_type", "general"),
                "source": it.get("source_name", "") or ", ".join(it.get("merged_sources", [])),
                "url": it.get("url", ""),
                "date": (it.get("published_at") or "")[:10],
            }
            if it.get("image_url"):
                entry["image_url"] = it["image_url"]
            news_input.append(entry)

        prompt = SECTION_PROMPT.format(
            cat_name=cat_name,
            items_json=json.dumps(news_input, ensure_ascii=False, indent=2),
        )

        console.log(f"[cyan]  LLM 生成 [{cat_name}] 板块... ({len(items)} 条)[/cyan]")

        try:
            response = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "你是科技新闻编辑。口播稿风格，客观准确有观点，每条新闻内容约150字、分析约80字，内容饱满有细节能顺畅读出，避免套话。保留全部条目不遗漏。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.6,
                max_tokens=5000,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            console.log(f"[yellow]  LLM [{cat_name}] 失败: {e}，使用模板[/yellow]")
            return self._template_section(cat_name, items, 0)

    def _template_section(self, cat_name: str, items: list[dict], start_num: int) -> str:
        """LLM 不可用时的模板兜底"""
        lines = [f"## {cat_name}\n"]

        leak_items = [it for it in items if it.get("sub_type") == "leak"]
        release_items = [it for it in items if it.get("sub_type") == "release"]
        system_items = [it for it in items if it.get("sub_type") == "system"]
        general_items = [it for it in items if it.get("sub_type") not in ("leak", "release", "system")]

        for sub_title, sub_items in [
            ("### 🔮 新机爆料", leak_items),
            ("### 🆕 新机发售", release_items),
            ("### 📱 系统更新", system_items),
            ("### 📋 其他资讯", general_items),
        ]:
            if not sub_items:
                continue
            lines.append(sub_title)
            lines.append("")
            for item in sub_items:
                start_num += 1
                title = item.get("title", "无标题")
                date_str = ""
                pub_date = item.get("published_at", "")
                if pub_date:
                    try:
                        dt = datetime.fromisoformat(pub_date)
                        date_str = dt.strftime("%Y-%m-%d")
                    except Exception:
                        pass
                sources = item.get("merged_sources", [item.get("source_name", "")])
                sources_str = ", ".join(sources) if sources else item.get("source_name", "")
                lines.append(f"#### {start_num}. {title}")
                if item.get("image_url"):
                    lines.append(f"![配图]({item['image_url']})")
                    lines.append("")
                if date_str:
                    lines.append(f"日期: {date_str} | 来源: {sources_str}")
                else:
                    lines.append(f"来源: {sources_str}")
                if item.get("summary"):
                    lines.append(f"> {item['summary'][:300]}")
                lines.append("")

        return "\n".join(lines)
