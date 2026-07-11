"""文稿生成器：模板拼接 + LLM 润色（可选）

主输出使用模板拼接全部精选条目，LLM 仅用于标题润色（可选）。
这样不会因为 LLM 字数限制丢失重要资讯。
"""

from datetime import datetime, timezone
from rich.console import Console
from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, CATEGORIES
from .citation import generate_citations_block

console = Console()

NEWS_TITLE_PROMPT = """你是游戏设备资讯编辑。请将以下新闻标题改写为更简洁专业的新闻标题（15字以内）。

原标题：
{original_titles}

返回 JSON 对象，key 为序号，value 为改写后的标题：
{{"0": "改写后的标题", "1": "改写后的标题", ...}}

只返回 JSON 对象，不要 markdown。"""


class ScriptWriter:
    def __init__(self):
        self.client = None
        if OPENAI_API_KEY and OPENAI_API_KEY != "sk-xxx":
            self.client = OpenAI(
                api_key=OPENAI_API_KEY,
                base_url=OPENAI_BASE_URL,
            )

    def write(self, categorized_items: dict[str, list[dict]], week_label: str, week_range: str) -> str:
        """根据分类好的精选新闻生成周报 Markdown"""
        if not categorized_items:
            console.log("[red]没有精选条目可生成文稿[/red]")
            return ""

        return self._build_report(categorized_items, week_label, week_range)

    def _polish_titles(self, items: list[dict]) -> dict[str, str]:
        """用 LLM 批量润色标题，返回 {原标题: 新标题} 映射"""
        if not self.client or len(items) < 3:
            return {}

        # 构建下标 → 标题的索引
        idx_to_title = {}
        for i, it in enumerate(items):
            title = it.get("title", "")[:80]
            idx_to_title[str(i)] = title

        import json
        prompt = NEWS_TITLE_PROMPT.format(
            original_titles=json.dumps(idx_to_title, ensure_ascii=False, indent=2),
        )

        try:
            response = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "你是科技新闻标题编辑。简洁准确，15字以内。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                max_tokens=1000,
            )
            content = response.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                if content.endswith("```"):
                    content = content[:-3]
            result = json.loads(content)

            # 构建 原标题 → 新标题 映射
            mapping = {}
            for i_str, new_title in result.items():
                original = idx_to_title.get(i_str)
                if original and new_title and new_title != original:
                    mapping[original] = new_title
            return mapping
        except Exception as e:
            console.log(f"[dim]标题润色失败: {e}，使用原标题[/dim]")
            return {}

    def _build_report(self, categorized_items: dict[str, list[dict]], week_label: str, week_range: str) -> str:
        """模板拼接：全部精选条目 → Markdown 周报"""
        lines = [f"# 游戏设备周报 | {week_label} ({week_range})\n"]

        total_count = 0
        all_items = []

        # 收集所有条目用于标题润色
        flat_items = []
        for items in categorized_items.values():
            flat_items.extend(items)

        # LLM 标题润色（如果可用）
        title_map = self._polish_titles(flat_items)

        for cat_key in CATEGORIES:
            items = categorized_items.get(cat_key, [])
            if not items:
                continue

            cat_name = CATEGORIES[cat_key]["name"]
            lines.append(f"## {cat_name}\n")

            # 按子类型分组
            leak_items = [it for it in items if it.get("sub_type") == "leak"]
            release_items = [it for it in items if it.get("sub_type") == "release"]
            system_items = [it for it in items if it.get("sub_type") == "system"]
            general_items = [it for it in items if it.get("sub_type") not in ("leak", "release", "system")]

            sub_sections = [
                ("### 🔮 新机爆料", leak_items),
                ("### 🆕 新机发售", release_items),
                ("### 📱 系统更新", system_items),
                ("### 📋 其他资讯", general_items),
            ]

            for sub_title, sub_items in sub_sections:
                if not sub_items:
                    continue
                lines.append(sub_title)
                lines.append("")
                for item in sub_items:
                    total_count += 1
                    title = item.get("title", "无标题")

                    # 应用 LLM 润色的标题
                    polished = title_map.get(title, "")
                    if polished:
                        title = polished

                    pub_date = item.get("published_at", "")
                    if pub_date:
                        try:
                            dt = datetime.fromisoformat(pub_date)
                            pub_date = dt.strftime("%Y-%m-%d")
                        except Exception:
                            pub_date = ""

                    sources = item.get("merged_sources", [item.get("source_name", "")])
                    sources_str = ", ".join(sources) if sources else item.get("source_name", "")

                    lines.append(f"#### {total_count}. {title}")
                    if pub_date:
                        lines.append(f"日期: {pub_date} | 来源: {sources_str}")
                    else:
                        lines.append(f"来源: {sources_str}")
                    if item.get("summary"):
                        lines.append(f"> {item['summary'][:300]}")
                    if item.get("url"):
                        lines.append(f"链接: {item['url']}")
                    lines.append("")

                    all_items.append(item)

        lines.append(generate_citations_block(all_items))

        report = "\n".join(lines)
        console.log(f"[green]周报生成完成: {total_count} 条资讯[/green]")
        return report
