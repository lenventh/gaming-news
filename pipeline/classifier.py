"""LLM 分类器：将新闻分类到七大板块"""

import json
from rich.console import Console
from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, CATEGORIES

console = Console()

BATCH_SIZE = 25  # 每批最多分类数

CLASSIFIER_PROMPT = """你是游戏硬件新闻分类助手。将以下新闻分类。

类别（只返回标识符）：
{categories_list}
- irrelevant: 与游戏硬件/设备完全无关的内容（汽车、金融、政治、健康、纯软件游戏评测等）

分类规则：
- steam_deck: Steam Deck、SteamOS、Proton、Valve 掌机
- windows_handheld: ROG Ally、AYANEO、GPD、微星Claw、Legion Go 等 Windows 掌机
- android_handheld: Odin、Retroid Pocket、安卓掌机
- linux_handheld: Anbernic、Miyoo、TrimUI、PowKiddy、开源/Linux 掌机
- console: PS5、Xbox、Switch/Switch 2 等传统游戏主机
- handheld_rumors: 厂商掌机传闻、爆料、专利
- emulator: 模拟器（Yuzu、Ryujinx、Cemu、RPCS3、Dolphin 等）
- irrelevant: 汽车、股票、耳机、家电等完全无关的新闻

{items}

返回纯 JSON: {{"0": "console", "1": "irrelevant", ...}}"""


class NewsClassifier:
    def __init__(self):
        self.client = OpenAI(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL,
        )

    def classify(self, items: list[dict]) -> list[dict]:
        if not items:
            return items

        uncategorized = [it for it in items if not it.get("category")]
        if not uncategorized:
            console.log("[green]所有条目已分类，跳过[/green]")
            return items

        console.log(f"[cyan]LLM 分类: {len(uncategorized)} 条待分类 (每批{BATCH_SIZE}条)[/cyan]")

        cat_lines = "\n".join(f"- {k}: {v['name']}" for k, v in CATEGORIES.items())

        # 分批处理
        for batch_start in range(0, len(uncategorized), BATCH_SIZE):
            batch = uncategorized[batch_start:batch_start + BATCH_SIZE]
            self._classify_batch(batch, cat_lines)
            console.log(f"[dim]  进度: {min(batch_start + BATCH_SIZE, len(uncategorized))}/{len(uncategorized)}[/dim]")

        # 将分类结果写回原列表
        classified_map = {it["url"]: it.get("category") for it in uncategorized}
        for item in items:
            if not item.get("category") and item["url"] in classified_map:
                item["category"] = classified_map[item["url"]]

        return items

    def _classify_batch(self, batch: list[dict], cat_lines: str):
        item_lines = []
        for i, item in enumerate(batch):
            title = item.get("title", "")[:150]
            item_lines.append(f"{i}. {title}")
        items_text = "\n".join(item_lines)

        prompt = CLASSIFIER_PROMPT.format(
            categories_list=cat_lines,
            items=items_text,
        )

        try:
            response = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1000,
            )
            result_text = response.choices[0].message.content.strip()
            if result_text.startswith("```"):
                result_text = result_text.split("\n", 1)[1]
                if result_text.endswith("```"):
                    result_text = result_text[:-3]

            mapping = json.loads(result_text)
            for i_str, cat in mapping.items():
                idx = int(i_str)
                if idx < len(batch) and (cat in CATEGORIES or cat == "irrelevant"):
                    batch[idx]["category"] = cat

        except json.JSONDecodeError as e:
            console.log(f"[yellow]JSON解析失败: {e}, 回退关键词[/yellow]")
            self._fallback_classify(batch)
        except Exception as e:
            console.log(f"[yellow]LLM 分类失败: {e}, 回退关键词[/yellow]")
            self._fallback_classify(batch)

    def _fallback_classify(self, items: list[dict]):
        for item in items:
            if item.get("category"):
                continue
            text = (item.get("title", "") + " " + item.get("summary", "")).lower()
            for cat_key, cat_info in CATEGORIES.items():
                for kw in cat_info["keywords"]:
                    if kw in text:
                        item["category"] = cat_key
                        break
                if item.get("category"):
                    break


def detect_sub_types(items: list[dict]) -> list[dict]:
    """检测每条新闻的子类型：leak（爆料）、release（发售）、system（系统更新）、general（其他）"""
    from config import NEWS_SUB_TAGS

    leak_kws = NEWS_SUB_TAGS["leak"]["keywords"]
    release_kws = NEWS_SUB_TAGS["release"]["keywords"]
    system_kws = NEWS_SUB_TAGS["system"]["keywords"]

    # 排除词：含这些词的内容不归入爆料/发售/系统更新（纯评测、游戏推荐等）
    exclude_kws = [
        "评测", "review", "游戏推荐", "折扣", "促销",
        "dlc", "mod", "特卖", "电影", "电视剧",
        "汽车", "金融", "股票", "基金",
    ]

    for item in items:
        text = (item.get("title", "") + " " + item.get("summary", "")).lower()

        # 1. 先检测系统更新
        system_score = sum(1 for kw in system_kws if kw.lower() in text)
        if system_score > 0:
            item["sub_type"] = "system"
            continue

        # 2. 含排除词直接归 general
        if any(kw.lower() in text for kw in exclude_kws):
            item["sub_type"] = "general"
            continue

        # 3. 检测爆料/发售
        release_score = sum(1 for kw in release_kws if kw.lower() in text)
        leak_score = sum(1 for kw in leak_kws if kw.lower() in text)

        if release_score > 0 and release_score >= leak_score:
            item["sub_type"] = "release"
        elif leak_score > 0:
            item["sub_type"] = "leak"
        else:
            item["sub_type"] = "general"

    return items


def count_by_category(items: list[dict]) -> dict:
    counts = {}
    for item in items:
        cat = item.get("category", "unknown")
        counts[cat] = counts.get(cat, 0) + 1
    return counts
