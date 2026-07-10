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
2. **总量硬限制**：全文 **严格控制在 800-900 字**（含标题、来源标注、正文所有内容）。这是 5 分钟口播的标准长度，超出 900 字就是不合格
3. **条数**：12-15 条，不要多也不要少
4. **以爆料为主**：`sub_type: leak` 的条目必须优先选用，占总条数 50% 以上
5. **口语化**：短句、好念。每条口播正文 1-2 句话，不超过 60 字
6. **按 6 大板块组织**，板块间加一句自然的转场语（转场语也要简洁，5-10 字即可）
7. **板块内按子类型分段**：
   - `### 🔮 新机爆料`（leak）
   - `### 🆕 新机发售`（release）
   - `### 📱 系统更新`（system）
   - `### 📋 其他资讯`（general）
   - 没有对应内容就跳过这个子板块
8. **每条格式**：
   ```
   #### [序号]. [一句话标题]
   来源: [来源名]
   - [1-2 句口播内容，直接讲重点]
   ```
9. **合并同事件**：同一事件被多家报道的，合并为一条，列出所有来源
10. **文末**输出参考资料区块（只列链接，不占用口播字数统计）

## 板块顺序
1. Steam Deck
2. Windows 掌机
3. 安卓掌机
4. 开源掌机/Linux掌机
5. 传统主机
6. 模拟器资讯

## 字数检查清单（生成前自查）
- 每个板块的正文加起来不能超过 900 字
- 每条资讯的 `- [口播内容]` 不超过 60 字
- 引用文字排除在外，不算入口播字数
- 如果素材太多，选最重要的 12-15 条，剩下的果断删除

## 新闻数据

{news_json}

请生成完整的 Markdown 口播文稿。记住：**全文不超过 900 字，精炼再精炼！**"""


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
                messages=[
                    {"role": "system", "content": "你是一个极其精炼的科技口播编辑。你的口播稿每条信息只用 1 句话讲清楚，绝不多写一个字。全文严格控制在 900 字以内。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=1500,  # 900 中文字 ≈ 600 tokens，1500 给予 markdown 格式足够空间
            )
            content = response.choices[0].message.content

            # 统计实际口播正文字数（不包括参考资料区块和 markdown 标记）
            body = content.split("## 参考资料")[0] if "## 参考资料" in content else content
            # 去除 markdown 标记后统计纯文本字数
            import re as _re
            plain_text = _re.sub(r"#{1,4}\s*", "", body)
            plain_text = _re.sub(r"\*\*|__|\*|__|`", "", plain_text)
            plain_text = _re.sub(r"\[|\]\([^)]+\)", "", plain_text)
            char_count = len(plain_text.replace("\n", "").replace(" ", ""))

            console.log(f"[green]文稿生成完成 ({len(content)} 字符)[/green]")
            if char_count > 1000:
                console.log(f"[yellow]⚠ 口播正文约 {char_count} 字，超过 900 字目标。下次运行时会自动修正。[/yellow]")

            # 如果显着超出，用第二遍 LLM 压缩
            if char_count > 1200:
                console.log("[yellow]  正在用 LLM 压缩...[/yellow]")
                compress_prompt = f"""请将以下口播文稿压缩到 800-900 字。保持原有结构和所有条目，只缩短每条的口播正文内容。不要删除任何资讯条目。

原稿：
{content}"""
                compress_resp = self.client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": "你是一个文字压缩专家。保持结构和信息完整，只压缩字数。"},
                        {"role": "user", "content": compress_prompt},
                    ],
                    temperature=0.3,
                    max_tokens=1500,
                )
                content = compress_resp.choices[0].message.content
                console.log(f"[green]  压缩完成 ({len(content)} 字)[/green]")
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
