"""游戏设备资讯周报脚本生成器 — 按板块生成精炼播报文稿"""

import json
import os
import re
from difflib import SequenceMatcher
from datetime import datetime

from openai import OpenAI
from rich.console import Console

from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, CATEGORIES, OUTPUT_DIR

console = Console()

CROSS_DEDUP_SIMILARITY = 0.72  # 跨板块去重阈值
MAX_TOKENS = 1500             # 每个板块最大 token
FALLBACK_TOTAL_LIMIT = 30     # 模板兜底总条目上限
COMPRESS_THRESHOLD = 1200     # 正文超此字数用二遍 LLM 压缩

_translate_client = None


def _get_translate_client():
    global _translate_client
    if _translate_client is None and OPENAI_API_KEY and OPENAI_API_KEY != "sk-xxx":
        _translate_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    return _translate_client


def _translate_title(title: str) -> str:
    """英文标题 → 中文，专有名词保留原文"""
    if not title:
        return title
    chinese = sum(1 for c in title if '一' <= c <= '鿿')
    if chinese / max(len(title), 1) > 0.4:
        return title  # 已有足够中文
    client = _get_translate_client()
    if not client:
        return title
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{
                "role": "user",
                "content": (
                    "将以下游戏硬件新闻标题翻译为简体中文。"
                    "保留品牌名/产品名/系统名原文(如Steam Deck/Switch/ROG Ally/AYANEO/GPD/PlayStation/Xbox/Proton/Linux)。"
                    "其余英文翻译为中文。只返回译文：\n\n" + title
                ),
            }],
            temperature=0.1,
            max_tokens=100,
        )
        translated = resp.choices[0].message.content.strip()
        if translated and len(translated) > 0:
            console.log(f"[dim]  译标题: {title[:40]} -> {translated[:40]}[/dim]")
            return translated
    except Exception as e:
        console.log(f"[yellow]  标题翻译失败: {e}[/yellow]")
    return title


def _normalize(title: str) -> str:
    text = title.lower().strip()
    text = re.sub(r"[^\w一-鿿]", "", text)
    return text


def _cross_category_dedup(categorized: dict[str, list[dict]]) -> int:
    """跨板块去重：URL 精确匹配 + 标题相似度"""
    seen_urls: set[str] = set()
    seen_titles: list[tuple[str, str]] = []
    removed = 0
    for cat_key in list(categorized.keys()):
        items = categorized[cat_key]
        keep = []
        for it in items:
            url = (it.get("url") or "").strip()
            title = it.get("title", "").strip()
            if url and url in seen_urls:
                console.log(f"[yellow]   跨板块去重(URL) [{cat_key}]: {title[:50]}[/yellow]")
                removed += 1
                continue
            norm_title = _normalize(title)
            is_dup = False
            for seen_norm, seen_cat in seen_titles:
                if SequenceMatcher(None, norm_title, seen_norm).ratio() > CROSS_DEDUP_SIMILARITY:
                    console.log(f"[yellow]   跨板块去重(标题) [{cat_key}←{seen_cat}]: {title[:50]}[/yellow]")
                    removed += 1
                    is_dup = True
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
   ![配图](图片URL)
   - 新闻内容：约150字，2-3句话讲清事件，包含背景+事件+具体细节（型号/规格/价格/日期），口播流畅
   - 简要分析：约80字，1-2句话，有观点的解读，说明对品牌/行业的影响
   - 来源: [来源名称]
   ```
   配图有 image_url 才插入，无则跳过。英文标题翻译中文，品牌名/产品名保留原文。
   **Reddit/外媒短摘要**：如 summary 只有一句话，根据标题展开补充，确保新闻内容 ≥150字。

3. **风格**：新闻播报体，客观简练。爆料类注明来源可信度（如"来自Reddit用户爆料"）。
4. **保留全部条目**，不要省略任何一条。
5. 末尾输出该板块参考资料链接列表（含原始 URL）。

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
        """按板块分别生成，合并为完整周报（国内/海外双栏）"""
        if not categorized_items:
            console.log("[red]没有精选条目可生成文稿[/red]")
            return ""

        if "-" in week_label and week_label.split("-")[-1] in ("上", "下"):
            parts = week_label.rsplit("-", 1)
            base_label, half = parts[0], parts[1]
            title = f"# 游戏设备周报·{half} | {base_label} ({week_range})\n"
        else:
            title = f"# 游戏设备周报 | {week_label} ({week_range})\n"

        _cross_category_dedup(categorized_items)

        cn_source_types = {
            "bilibili_browser", "bilibili_manufacturer", "bilibili_space",
            "bilibili_article", "bilibili_dynamic",
            "tieba", "tieba_browser",
            "zhihu_browser", "smzdm_browser",
            "chinese_web", "rss_cn",
        }

        def _split(items_list):
            domestic = [it for it in items_list if it.get("source_type", "") in cn_source_types]
            overseas = [it for it in items_list if it.get("source_type", "") not in cn_source_types]
            return domestic, overseas

        sections = []
        all_items = []

        for region_label, region_key in [("🌍 海外资讯", "overseas"), ("🇨🇳 国内资讯", "domestic")]:
            lines = [f"## {region_label}\n"]
            region_all = []
            item_counter = 0

            for cat_key in CATEGORIES:
                items = categorized_items.get(cat_key, [])
                domestic, overseas = _split(items)
                region_items_list = domestic if region_key == "domestic" else overseas
                if not region_items_list:
                    continue

                cat_name = CATEGORIES[cat_key]["name"]
                region_all.extend(region_items_list)

                if self.client:
                    section = self._generate_section(f"{cat_name} ({region_label})", region_items_list)
                else:
                    section = self._template_section(cat_name, region_items_list, item_counter)
                    item_counter += len(region_items_list)

                lines.append(section)
                lines.append("")

            lines.append(generate_citations_block(region_all))
            sections.extend(lines)
            sections.append("")
            all_items.extend(region_all)

        report = "\n".join([title] + sections)
        console.log(f"[green]周报生成完成: {len(all_items)} 条资讯[/green]")
        return report

    def _generate_section(self, cat_label: str, items: list[dict]) -> str:
        """调用 LLM 生成一个板块的新闻播报"""
        import json as _json

        news_input = []
        for it in items:
            raw_title = it.get("title", "")
            display_title = _translate_title(raw_title)
            entry = {
                "title": display_title,
                "summary": it.get("summary", "")[:800],
                "sub_type": it.get("sub_type", "general"),
                "source": it.get("source_name", "") or ", ".join(it.get("merged_sources", [])),
                "url": it.get("url", ""),
                "date": (it.get("published_at") or "")[:10],
            }
            if it.get("image_url"):
                img = it["image_url"]
                if img.startswith("//"):
                    img = "https:" + img
                entry["image_url"] = img
            news_input.append(entry)

        prompt = SECTION_PROMPT.format(
            cat_name=cat_label,
            items_json=_json.dumps(news_input, ensure_ascii=False, indent=2),
        )

        try:
            resp = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=MAX_TOKENS,
            )
            raw = resp.choices[0].message.content.strip()

            # 压缩过长正文
            if raw and len(raw) > COMPRESS_THRESHOLD:
                compress_prompt = (
                    "将以下新闻播报文稿压缩到1200字以内，保持结构和关键数据不变，去除冗余修饰词：\n\n" + raw
                )
                compress_resp = self.client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "user", "content": compress_prompt}],
                    temperature=0.1,
                    max_tokens=MAX_TOKENS,
                )
                raw = compress_resp.choices[0].message.content.strip()
        except Exception as e:
            console.log(f"[red]  LLM 生成 [{cat_label}] 失败: {e}, 回退模板[/red]")
            raw = self._template_section(cat_label, items, 0)

        prefix = f"## {cat_label}\n"
        if prefix in raw:
            raw = raw.replace(prefix, "")
            raw = raw.strip()

        return raw

    def _template_section(self, cat_name: str, items: list[dict], start_num: int) -> str:
        """LLM 不可用时的模板兜底"""
        lines = [f"## {cat_name}\n"]
        sections_data = [
            ("### 🔮 新机爆料", "leak"),
            ("### 🆕 新机发售", "release"),
            ("### 📱 系统更新", "system"),
            ("### 📋 其他资讯", None),
        ]
        num = start_num
        for sub_title, sub_type in sections_data:
            if sub_type:
                sub_items = [it for it in items if it.get("sub_type") == sub_type]
            else:
                sub_items = [it for it in items if it.get("sub_type") not in ("leak", "release", "system")]
            if not sub_items:
                continue
            lines.append(sub_title)
            lines.append("")
            for item in sub_items:
                num += 1
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
                lines.append(f"#### {num}. {title}")
                if item.get("image_url"):
                    img = item["image_url"]
                    if img.startswith("//"):
                        img = "https:" + img
                    lines.append(f"![配图]({img})")
                    lines.append("")
                if date_str:
                    lines.append(f"日期: {date_str} | 来源: {sources_str}")
                else:
                    lines.append(f"来源: {sources_str}")
                if item.get("summary"):
                    lines.append(f"> {item['summary'][:300]}")
                lines.append("")
        return "\n".join(lines)


def generate_citations_block(items: list[dict]) -> str:
    """生成参考资料链接列表"""
    lines = ["## 参考资料链接列表", ""]
    seen = set()
    idx = 1
    for it in items:
        title = it.get("title", "无标题")
        url = it.get("url", "")
        if url and url not in seen:
            seen.add(url)
            source = it.get("source_name", "")
            prefix = f"[{source}] " if source else ""
            lines.append(f"{idx}. {prefix}{title}: {url}")
            idx += 1
    return "\n".join(lines)
