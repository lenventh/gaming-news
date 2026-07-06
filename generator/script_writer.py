"""LLM 文稿生成器：将精选条目改写成可读性强的视频脚本"""

from datetime import datetime, timezone
from rich.console import Console
from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, CATEGORIES
from .citation import generate_citations_block

console = Console()

SCRIPT_PROMPT = """你是一个游戏设备资讯节目的编辑。请根据以下精选新闻和分类，生成一期视频文稿。

## 要求

1. **标题格式**：`# 游戏设备周刊 | {week_label} ({week_range})`
2. **按 7 大板块组织**，每个板块有 Markdown 二级标题
3. **每个板块 5 条资讯**（如果某板块不足 5 条，有几条写几条，不凑数）
4. **每条资讯格式**：
   ```
   ### [序号]. [简洁吸引人的标题]
   日期: [日期] | 来源: [来源名]
   - [2-3 句话的摘要，说明事件是什么、为什么值得关注]
   - 素材: [图片链接，如果没有就写"待补充"]
   ```
5. **合并同事件**：如果多条新闻来自不同来源但报道同一事件（比如任天堂Switch 2电池改版被Eurogamer/Nintendo Life/IT之家同时报道），合并为一条综合新闻，列出所有来源，而不是重复写多次。
6. **语言风格**：口语化，适合朗读，每段不要太长。在板块之间可以加一句过渡语。
7. **文末**输出参考资料区块，用论文引文格式。

## 分类顺序

按以下顺序组织：
1. Steam Deck
2. Windows 掌机
3. 安卓掌机
4. 开源掌机/Linux掌机
5. 传统主机
6. 厂商掌机传闻
7. 模拟器资讯

## 新闻数据

{news_json}

请生成完整的 Markdown 文稿。"""


class ScriptWriter:
    def __init__(self):
        self.client = None
        if OPENAI_API_KEY and OPENAI_API_KEY != "sk-xxx":
            self.client = OpenAI(
                api_key=OPENAI_API_KEY,
                base_url=OPENAI_BASE_URL,
            )

    def write(self, categorized_items: dict[str, list[dict]], week_label: str, week_range: str) -> str:
        """根据分类好的精选新闻生成完整文稿"""
        if not categorized_items:
            console.log("[red]没有精选条目可生成文稿[/red]")
            return ""

        # 无 LLM 配置时直接用模板拼接
        if not self.client:
            console.log("[yellow]LLM 未配置，使用模板拼接生成文稿[/yellow]")
            return self._fallback_script(categorized_items, week_label, week_range)
        news_for_llm = {}
        for cat, items in categorized_items.items():
            cat_name = CATEGORIES.get(cat, {}).get("name", cat)
            news_for_llm[cat_name] = []
            for item in items:
                news_for_llm[cat_name].append({
                    "title": item.get("title", ""),
                    "summary": item.get("summary", "")[:300],
                    "date": item.get("published_at", ""),
                    "sources": item.get("merged_sources", [item.get("source_name", "")]),
                    "url": item.get("url", ""),
                    "materials": item.get("material_links", []),
                    "score": item.get("raw_data", {}).get("score", 0),
                    "comments": item.get("raw_data", {}).get("num_comments", 0),
                })

        import json
        prompt = SCRIPT_PROMPT.format(
            week_label=week_label,
            week_range=week_range,
            news_json=json.dumps(news_for_llm, ensure_ascii=False, indent=2),
        )

        console.log(f"[cyan]LLM 生成文稿中... (模型: {OPENAI_MODEL})[/cyan]")

        try:
            response = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=8000,
            )
            content = response.choices[0].message.content

            # 追加引文区块
            all_items = []
            for items in categorized_items.values():
                all_items.extend(items)
            citations = generate_citations_block(all_items)

            # 如果 LLM 没有生成参考资料区块，追加
            if "## 参考资料" not in content:
                content += "\n\n" + citations

            console.log(f"[green]文稿生成完成 ({len(content)} 字)[/green]")
            return content

        except Exception as e:
            console.log(f"[red]LLM 文稿生成失败: {e}[/red]")
            return self._fallback_script(categorized_items, week_label, week_range)

    def _fallback_script(self, categorized_items: dict[str, list[dict]], week_label: str, week_range: str) -> str:
        """LLM 不可用时的兜底文稿"""
        lines = [f"# 游戏设备周刊 | {week_label} ({week_range})\n"]

        all_items = []
        for cat_key in CATEGORIES:
            items = categorized_items.get(cat_key, [])
            if not items:
                continue
            cat_name = CATEGORIES[cat_key]["name"]

            lines.append(f"## {cat_name}\n")
            for i, item in enumerate(items, 1):
                title = item.get("title", "无标题")
                pub_date = item.get("published_at", "未知日期")
                if pub_date and pub_date != "未知日期":
                    try:
                        dt = datetime.fromisoformat(pub_date)
                        pub_date = dt.strftime("%Y-%m-%d")
                    except Exception:
                        pass

                sources = item.get("merged_sources", [item.get("source_name", "")])
                sources_str = ", ".join(sources)

                lines.append(f"### {i}. {title}")
                lines.append(f"📅 {pub_date} | 🔗 {sources_str}")
                if item.get("summary"):
                    lines.append(f"- {item['summary'][:300]}")
                lines.append(f"- 📷 素材: 待补充")
                lines.append("")

            all_items.extend(items)

        lines.append(generate_citations_block(all_items))
        return "\n".join(lines)
