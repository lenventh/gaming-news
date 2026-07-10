"""LLM 文稿生成器：将精选条目改写成可读性强的视频脚本"""

from datetime import datetime, timezone
from rich.console import Console
from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, CATEGORIES
from .citation import generate_citations_block

console = Console()

SCRIPT_PROMPT = """你是一个掌机资讯口播节目的编辑。请根据以下精选新闻，生成一期 5 分钟视频口播文稿。

## 核心要求

1. **标题格式**：`# 掌机资讯 | {week_label} ({week_range})`
2. **总量**：全文 12-15 条资讯（5 分钟口播约 800-900 字），多了删、少了不凑
3. **以爆料为主**：`sub_type: leak` 的条目必须优先选用，占总条数 50% 以上
4. **口语化**：短句、好念、不书面。每条 1-2 句话，不要长段落。像在跟朋友聊新闻
5. **按 6 大板块组织**，板块间加一句自然的转场语
6. **板块内按子类型分段**：
   - `### 🔮 新机爆料`（leak）
   - `### 🆕 新机发售`（release）
   - `### 📱 系统更新`（system）
   - `### 📋 其他资讯`（general）
   - 没有对应内容就跳过这个子板块
7. **每条格式**：
   ```
   #### [序号]. [一句话博眼球的标题]
   来源: [来源名]
   - [1-2 句口播内容，讲清楚是什么事、为什么值得关注]
   ```
8. **合并同事件**：同一事件被多家报道的，合并为一条，列出所有来源
9. **文末**输出参考资料区块

## 板块顺序
1. Steam Deck
2. Windows 掌机
3. 安卓掌机
4. 开源掌机/Linux掌机
5. 传统主机
6. 模拟器资讯

## 新闻数据

{news_json}

请生成完整的 Markdown 口播文稿。"""


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
                sub_type = item.get("sub_type", "general")
                news_for_llm[cat_name].append({
                    "title": item.get("title", ""),
                    "summary": item.get("summary", "")[:300],
                    "date": item.get("published_at", ""),
                    "sources": item.get("merged_sources", [item.get("source_name", "")]),
                    "url": item.get("url", ""),
                    "materials": item.get("material_links", []),
                    "score": item.get("raw_data", {}).get("score", 0),
                    "comments": item.get("raw_data", {}).get("num_comments", 0),
                    "sub_type": sub_type,
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
                max_tokens=3000,
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
        """LLM 不可用时的兜底文稿：12-15 条总量，爆料优先"""
        lines = [f"# 掌机资讯 | {week_label} ({week_range})\n"]

        MAX_TOTAL = 15
        total_count = 0
        all_items = []

        # 按板块顺序遍历，每个板块最多 4 条，优先 leak
        for cat_key in CATEGORIES:
            items = categorized_items.get(cat_key, [])
            if not items:
                continue
            cat_name = CATEGORIES[cat_key]["name"]
            lines.append(f"## {cat_name}\n")

            # 按子类型优先级分组：leak > release > system > general
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
                    if total_count >= MAX_TOTAL:
                        break
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

                    total_count += 1
                    lines.append(f"#### {total_count}. {title}")
                    lines.append(f"来源: {sources_str}")
                    if item.get("summary"):
                        lines.append(f"- {item['summary'][:200]}")
                    lines.append("")

                    all_items.append(item)

                if total_count >= MAX_TOTAL:
                    break
            if total_count >= MAX_TOTAL:
                break

        lines.append(generate_citations_block(all_items))
        return "\n".join(lines)
