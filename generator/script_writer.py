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


def _inject_images(raw: str, items: list[dict]) -> str:
    """后处理：LLM 生成后，强制注入被遗漏的配图

    LLM 经常忽略 prompt 中的配图指令。此函数扫描 LLM 输出中每条新闻的
    标题行，若对应条目有 image_url 但标题后没有 ![](...) 则强制插入。
    """
    if not raw or not items:
        return raw

    # 构建 title → image_url 映射（每个条目生成后 LLM 用的 title）
    image_map: dict[str, str] = {}
    for it in items:
        img = it.get("image_url")
        if not img:
            continue
        if img.startswith("//"):
            img = "https:" + img
        # 用传给 LLM 的 display_title 作为 key
        raw_title = it.get("title", "")
        display = _translate_title(raw_title)
        # 取前 30 个字符做模糊匹配键
        key = _normalize(display)[:30]
        if len(key) >= 6:
            image_map[key] = img

    if not image_map:
        return raw

    lines = raw.split("\n")
    result = []
    i = 0
    injected = 0
    title_re = re.compile(r"^(#{2,4}\s+\d+[\.\、\s]|(?:\*\*)?\d+[\.\、]\s*(?:\*\*)?)\s*(.+)")

    while i < len(lines):
        line = lines[i]
        result.append(line)
        m = title_re.match(line)
        if not m:
            i += 1
            continue

        matched_title = m.group(2).strip()
        # 去掉末尾的 markdown 标记
        matched_title = re.sub(r"\*+$", "", matched_title).strip()
        title_key = _normalize(matched_title)[:30]

        # 检查这个标题是否有对应图片
        img_url = None
        for key, url in image_map.items():
            if _title_fuzzy_match(title_key, key):
                img_url = url
                break

        if not img_url:
            i += 1
            continue

        # 检查下一两行是否已有图片
        has_image = False
        for offset in range(1, min(4, len(lines) - i)):
            next_line = lines[i + offset].strip()
            if next_line.startswith("!["):
                has_image = True
                break
            if next_line and not next_line.startswith("-") and not next_line.startswith(">"):
                break

        if not has_image:
            result.append(f"![配图]({img_url})")
            result.append("")
            injected += 1

        i += 1

    if injected:
        console.log(f"[green]  配图注入: {injected} 张[/green]")
    return "\n".join(result)


def _validate_images(raw: str, items: list[dict]) -> str:
    """后处理：删除输出中不属于本板块条目或超出允许次数的配图

    LLM 常将一个条目的配图复制给同板块其他条目。此函数以 items 的
    image_url 集合为白名单，统计输出中每张图的出现次数，删除超额的。
    """
    if not raw or not items:
        return raw

    def _strip_query(url: str) -> str:
        if url.startswith("//"):
            url = "https:" + url
        return re.sub(r"[?#].*$", "", url)

    # 白名单：归一化 URL → 允许最大出现次数
    allowed: dict[str, int] = {}
    for it in items:
        img = it.get("image_url")
        if img:
            key = _strip_query(img)
            allowed[key] = allowed.get(key, 0) + 1

    if not allowed:
        return raw

    img_re = re.compile(r"^!\[配图\]\((.+)\)$")
    usage: dict[str, int] = {}
    lines = raw.split("\n")
    result = []
    removed = 0

    for line in lines:
        m = img_re.match(line.strip())
        if not m:
            result.append(line)
            continue
        key = _strip_query(m.group(1))
        if key not in allowed:
            removed += 1
            continue
        if usage.get(key, 0) >= allowed[key]:
            removed += 1
            continue
        usage[key] = usage.get(key, 0) + 1
        result.append(line)

    if removed:
        console.log(f"[green]  配图校验: 移除 {removed} 张错误配图[/green]")
    return "\n".join(result)


def _title_fuzzy_match(a: str, b: str) -> bool:
    """两个规范化标题是否指向同一新闻"""
    if not a or not b:
        return False
    if a == b:
        return True
    # 一个是另一个的前缀（LLM 可能截断标题）
    shorter = min(a, b, key=len)
    longer = max(a, b, key=len)
    if len(shorter) >= 8 and longer.startswith(shorter):
        return True
    # SequenceMatcher 兜底
    if len(a) >= 8 and len(b) >= 8:
        return SequenceMatcher(None, a, b).ratio() > 0.65
    return False


def _strip_opening_phrases(text: str) -> str:
    """后处理：删除 LLM 生成的废话开场白

    LLM 经常忽略 prompt 中的"禁止废话"指令，生成"各位...欢迎...我是编辑"等
    主持人口播开场白。此函数按行扫描，删除完全匹配开场白模式的行。
    """
    if not text:
        return text

    # 匹配开场白行：各位[称呼][，,] 欢迎... | 我是[编辑/你们的编辑等]
    opening_re = re.compile(
        r"^(?:好的[，,]\s*)?"
        r"各位(?:读者|听众|观众|玩家)"
        r"(?:朋友)?[，,]\s*"
        r"欢迎(?:收看|收听|阅读).*?"
        r"(?:[。.]\s*(?:我是(?:你们[的]?)?编辑[。.]?\s*)?)?"
        r"$"
    )
    # 独立行："我是你们的编辑。" / "我是编辑。"
    editor_re = re.compile(r"^我是(?:你们[的]?)?编辑[。.]?\s*$")
    # 转折语："接下来，让我们..."/"以下[是]..."/"首先[...]"
    segue_re = re.compile(
        r"^(?:接下来[，,]?\s*(?:让[我们我]+\s*)?|"
        r"以下(?:是)?[，,]?\s*|"
        r"首先[，,]?\s*)"
        r"(?:聚焦|关注|看看|带来|进入|播报|了解|回顾|"
        r"将(?:目光|视线)|目光|视线|镜头|视线|为您)"
    )

    lines = text.split("\n")
    result = []
    stripped = 0
    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            result.append(line)
            continue
        if opening_re.match(stripped_line) or editor_re.match(stripped_line) or segue_re.match(stripped_line):
            stripped += 1
            continue
        result.append(line)

    if stripped:
        console.log(f"[green]  开场白清理: {stripped} 行[/green]")
    return "\n".join(result)


def _normalize_format(text: str) -> str:
    """后处理：归一化 LLM 生成的格式差异

    - `**N. Title**` → `#### N. Title`
    - 统一来源行格式
    - 去除残留的无关 ### 标题
    """
    if not text:
        return text

    # 1. `**N. Title**` (加粗数字标题) → `#### N. Title`
    text = re.sub(r"^\*\*(\d+[\.\、])\s*", r"#### \1 ", text, flags=re.MULTILINE)
    text = re.sub(r"^(\d+[\.\、])\s+(?=\*\*)", r"#### \1 ", text, flags=re.MULTILINE)

    # 2. 清理双 ** 包裹的标题尾
    text = re.sub(r"^(####\s+\d+[\.\、]\s+.+?)\*\*\s*$", r"\1", text, flags=re.MULTILINE)

    # 3. 来源行统一: `- 来源: ` 或 `来源：` → `- **来源**: `
    text = re.sub(r"^-\s*来源[：:]\s*", r"- **来源**: ", text, flags=re.MULTILINE)
    text = re.sub(r"^\*\*来源[：:]\*\*\s*", r"- **来源**: ", text, flags=re.MULTILINE)

    # 4. 新闻内容/简要分析 标签加粗归一化
    text = re.sub(r"^-\s*新闻内容[：:]", r"- **新闻内容**：", text, flags=re.MULTILINE)
    text = re.sub(r"^-\s*简要分析[：:]", r"- **简要分析**：", text, flags=re.MULTILINE)

    # 5. 删除空条目（LLM 截断导致的 "#### N. X" 无后续内容）
    lines = text.split("\n")
    cleaned = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(#{2,4}\s+\d+[\.\、]\s*.{1,5})$", line)
        if m:
            # 检查后续是否有实质内容
            j = i + 1
            has_content = False
            while j < len(lines) and j < i + 4:
                stripped = lines[j].strip()
                if stripped and not stripped.startswith("#") and not stripped.startswith("!["):
                    has_content = True
                    break
                j += 1
            if not has_content:
                i = j  # 跳过空条目
                continue
        cleaned.append(line)
        i += 1
    text = "\n".join(cleaned)

    return text


def _inject_report_intro_outro(report: str, all_items: list[dict],
                                week_label: str, client) -> str:
    """为整篇周报生成一个开场白和一个结尾（仅一份，非每板块）"""
    if not report or not all_items:
        return report

    # 提取本期的关键新闻用作生成素材
    highlights = []
    for it in all_items[:15]:
        title = _translate_title(it.get("title", ""))
        cat = it.get("category", "")
        sub = it.get("sub_type", "")
        if sub == "leak":
            tag = "爆料"
        elif sub == "release":
            tag = "发售"
        else:
            tag = ""
        line = f"- {title}"
        if tag:
            line += f" [{tag}]"
        highlights.append(line)

    hl_text = "\n".join(highlights) if highlights else "（暂无）"
    total = len(all_items)

    if client:
        try:
            # 开场白
            intro_prompt = (
                "你是游戏设备资讯周报的编辑。以下是本期({0})的新闻摘要"
                "（共{1}条）。请写一句简短的开场白（15-30字），"
                "概括本期重点，引出正文。不要使用主持人口播句式"
                "（各位观众/欢迎收看/我是编辑等），直接写开场内容。\n\n{2}"
            ).format(week_label, total, hl_text)
            intro_resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": intro_prompt}],
                temperature=0.4, max_tokens=80,
            )
            intro = intro_resp.choices[0].message.content.strip()

            # 结尾
            outro_prompt = (
                "你是游戏设备资讯周报的编辑。本期周报共{0}条新闻。"
                "请写一句简短的结尾语（15-30字），收尾本期内容。"
                "不要使用主持人口播句式（感谢收看/下期再见等），直接写结尾。"
            ).format(total)
            outro_resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": outro_prompt}],
                temperature=0.4, max_tokens=80,
            )
            outro = outro_resp.choices[0].message.content.strip()
        except Exception as e:
            console.log(f"[yellow]  开场白/结尾生成失败: {e}, 跳过[/yellow]")
            return report
    else:
        # 模板兜底
        cats = set(it.get("category", "") for it in all_items)
        cat_names = [CATEGORIES.get(c, {}).get("name", c) for c in cats if c in CATEGORIES]
        focus = "、".join(cat_names[:4]) if cat_names else "游戏设备"
        intro = f"本期周报聚焦{focus}领域，共收录{total}条资讯。"
        outro = f"以上为本期全部内容，我们下周再见。"

    # 在标题行之后、第一个 ## 之前插入开场白
    lines = report.split("\n")
    result = []
    intro_inserted = False
    for i, line in enumerate(lines):
        result.append(line)
        if not intro_inserted and line.startswith("# ") and intro:
            result.append("")
            result.append(f"> {intro}")
            result.append("")
            intro_inserted = True

    report = "\n".join(result)

    # 在末尾追加结尾
    if outro:
        report += f"\n\n---\n\n> {outro}\n"

    console.log(f"[green]  开场白+结尾已注入[/green]")
    return report


def _cross_category_dedup(categorized: dict[str, list[dict]]) -> int:
    """跨板块去重：URL 精确匹配 + 产品名匹配 + 标题相似度"""
    from pipeline.device_os_map import DEVICE_CATEGORY_MAP

    seen_urls: set[str] = set()
    seen_titles: list[tuple[str, str]] = []
    seen_products: set[str] = set()  # 产品名 → 首次出现的板块
    removed = 0

    # 按设备名长度倒序，优先长名匹配
    sorted_devices = sorted(
        [d for d in DEVICE_CATEGORY_MAP if len(d) > 4],
        key=len, reverse=True,
    )

    # 中文品牌名 → 英文名（用于提取后归一化，确保同一产品在去重时被视为同一 key）
    _CN_BRAND_NORMALIZE = {
        "芒米": "mangmi",
        "安伯尼克": "anbernic",
        "吹米": "trimui",
        "壹号本": "onexplayer",
        "周哥": "anbernic",
        "老张": "gkd",
    }

    def _normalize_product(name: str) -> str:
        """将提取到的产品名中的中文品牌替换为英文，确保跨语言去重"""
        low = name.lower()
        for cn, en in _CN_BRAND_NORMALIZE.items():
            if low.startswith(cn):
                return en + low[len(cn):]
        return low

    def _extract_product(title: str) -> str | None:
        low = title.lower()
        for device in sorted_devices:
            tokens = device.split()
            if len(tokens) == 1:
                tok = tokens[0]
                if tok.isascii() and tok.isalpha():
                    if re.search(r"\b" + re.escape(tok) + r"\b", low, re.ASCII):
                        return device
                elif tok in low:
                    return device
            else:
                pos = 0
                ok = True
                for tok in tokens:
                    if tok.isascii() and tok.isalpha() and len(tok) > 2:
                        m = re.search(r"\b" + re.escape(tok) + r"\b", low, re.ASCII)
                        if not m or m.start() < pos:
                            ok = False
                            break
                        pos = m.end()
                    else:
                        idx = low.find(tok, pos)
                        if idx < 0:
                            ok = False
                            break
                        pos = idx + len(tok)
                if ok:
                    return device
        return None

    for cat_key in list(categorized.keys()):
        items = categorized[cat_key]
        keep = []
        for it in items:
            url = (it.get("url") or "").strip()
            title = it.get("title", "").strip()

            # 1. URL 精确去重
            if url and url in seen_urls:
                console.log(f"[yellow]   跨板块去重(URL) [{cat_key}]: {title[:50]}[/yellow]")
                removed += 1
                continue

            # 2. 产品名去重（同产品已在其他板块出现）
            product = _extract_product(title)
            if product:
                product = _normalize_product(product)
            if product and product in seen_products:
                console.log(f"[yellow]   跨板块去重(产品名) [{cat_key}←{product}]: {title[:50]}[/yellow]")
                removed += 1
                continue

            # 3. 标题相似度去重
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
            if product:
                seen_products.add(product)
            keep.append(it)
        categorized[cat_key] = keep

    if removed > 0:
        console.log(f"[green]  跨板块去重完成，移除 {removed} 条重复[/green]")
    return removed


SECTION_PROMPT = """你是游戏设备资讯周报的编辑。请根据以下 {cat_name} 板块的新闻条目，生成该板块的新闻播报文稿。

## 要求

0. **禁止废话**：不要添加"各位观众"/"欢迎收看"/"以下是"等主持人口播开场白或转接语，直接以格式模板开始。

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
   **强制配图**：每条有 image_url 的新闻**必须**在标题下一行插入 `![配图](URL)`（不可省略），无 image_url 则跳过。英文标题翻译中文，品牌名/产品名保留原文。
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

                lines.append(f"### {cat_name}")
                lines.append("")
                lines.append(section)
                lines.append("")

            lines.append(generate_citations_block(region_all))
            sections.extend(lines)
            sections.append("")
            all_items.extend(region_all)

        report = "\n".join([title] + sections)
        report = _inject_report_intro_outro(report, all_items, week_label, self.client)
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

        raw = _inject_images(raw, items)
        raw = _strip_opening_phrases(raw)
        raw = _normalize_format(raw)
        raw = _validate_images(raw, items)
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
