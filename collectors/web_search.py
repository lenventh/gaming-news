"""AI 联网搜索补充采集器

使用 Google News RSS 搜索 + LLM 摘要，为每个板块定向搜索近期资讯。
弥补 RSS 源覆盖面不足的问题。

工作流程：
1. 对每个板块的关键词用 Google News RSS 搜索
2. 获取搜索结果（标题、URL、摘要、日期）
3. 用 LLM 批量提取和去噪
4. 返回标准化的新闻 dict
"""

from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import feedparser
import requests
from rich.console import Console
from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, CATEGORIES, CUTOFF_DATE
from .base import BaseCollector

console = Console()

# 每个板块的搜索关键词组
CATEGORY_SEARCH_QUERIES = {
    "steam_deck": [
        "Steam Deck 最新",
        "SteamOS Proton 更新",
        "Steam Deck OLED 新闻",
    ],
    "windows_handheld": [
        "ROG Ally 掌机 新闻",
        "AYANEO GPD 掌机",
        "Legion Go MSI Claw",
    ],
    "android_handheld": [
        "安卓掌机 Odin Retroid",
        "Android gaming handheld 2026",
    ],
    "linux_handheld": [
        "开源掌机 Anbernic Miyoo",
        "retro handheld emulator device new",
    ],
    "console": [
        "PS5 Switch 2 Xbox 最新",
        "console gaming hardware news",
    ],
    "handheld_rumors": [
        "Sony Xbox 掌机 传闻",
        "Switch 2 掌机 爆料",
        "gaming handheld rumor leak",
    ],
    "emulator": [
        "模拟器 Yuzu Ryujinx 更新",
        "emulator release update 2026",
    ],
}

EXTRACT_PROMPT = """你是游戏设备新闻编辑。从以下 RSS 搜索结果中提取与游戏硬件/模拟器直接相关的新闻。

## 板块: {category_name}

## 要求
1. 只提取与游戏设备、掌机、主机、模拟器硬件直接相关的新闻
2. 过滤掉纯游戏软件新闻（如某游戏发售、DLC等）
3. 判断 is_recent: true/false（标题或摘要中是否能判断是近一周事件）
4. 如果原文是英文，标题和摘要翻译成中文
5. 跳过广告、过时内容、无关内容

## 输入
{search_results}

## 输出
返回 JSON 数组:
[{{"title": "中文标题", "summary": "2-3句中摘要", "is_recent": true/false, "url": "原URL"}}]

只返回 JSON 数组。"""


class WebSearchCollector(BaseCollector):
    """用 Google News RSS 搜索补充新闻"""

    def __init__(self):
        super().__init__("GoogleNewsSearch")

    def _search_google_news(self, query: str, max_results: int = 10) -> list[dict]:
        """通过 Google News RSS 搜索"""
        results = []
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"

        try:
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (compatible; GamingNewsBot/1.0)"
            })
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)

            for entry in feed.entries[:max_results]:
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "")

                # Google News 的 link 格式特殊，需要提取真实 URL
                if link and "news.google.com" in link:
                    from urllib.parse import urlparse, parse_qs
                    parsed = urlparse(link)
                    params = parse_qs(parsed.query)
                    article_url = params.get("url", [link])[0]
                else:
                    article_url = link

                # 解析日期
                pub_date = None
                for attr in ("published_parsed", "updated_parsed"):
                    tp = getattr(entry, attr, None)
                    if tp:
                        try:
                            pub_date = datetime(*tp[:6], tzinfo=timezone.utc)
                        except Exception:
                            pass
                        break

                # 提取摘要
                summary = ""
                if hasattr(entry, "summary"):
                    from bs4 import BeautifulSoup
                    summary = BeautifulSoup(entry.summary, "html.parser").get_text()[:500]
                elif hasattr(entry, "description"):
                    from bs4 import BeautifulSoup
                    summary = BeautifulSoup(entry.description, "html.parser").get_text()[:500]

                # 提取来源名
                source_name = getattr(entry, "source", {})
                if isinstance(source_name, dict):
                    source_name = source_name.get("title", "Google News")

                results.append({
                    "title": title,
                    "url": article_url,
                    "published_at": pub_date.isoformat() if pub_date else None,
                    "summary": summary,
                    "source_name": str(source_name),
                })

        except Exception as e:
            console.log(f"[dim]Google News 搜索失败 [{query[:30]}...]: {e}[/dim]")

        return results

    def _extract_with_llm(self, search_results: list[dict], category_key: str) -> list[dict]:
        """用 LLM 从搜索结果中提取结构化新闻"""
        if not search_results:
            return []

        # 先用日期预过滤
        recent = []
        for r in search_results:
            if r.get("published_at"):
                try:
                    dt = datetime.fromisoformat(r["published_at"])
                    if dt < CUTOFF_DATE:
                        continue
                except Exception:
                    pass
            recent.append(r)

        if not recent:
            return []

        cat_name = CATEGORIES.get(category_key, {}).get("name", category_key)

        simplified = []
        for r in recent[:20]:  # 最多 20 条送入 LLM
            simplified.append({
                "title": r["title"][:150],
                "snippet": r["summary"][:200],
                "url": r["url"],
                "source": r.get("source_name", ""),
            })

        import json
        prompt_text = EXTRACT_PROMPT.format(
            category_name=cat_name,
            search_results=json.dumps(simplified, ensure_ascii=False, indent=2),
        )

        try:
            client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt_text}],
                temperature=0.3,
                max_tokens=2000,
            )
            content = response.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                if content.endswith("```"):
                    content = content[:-3]
            return json.loads(content)
        except Exception as e:
            console.log(f"[red]LLM 提取失败 [{cat_name}]: {e}[/red]")
            # 兜底：直接返回最近条目
            fallback = []
            for r in recent[:5]:
                fallback.append({
                    "title": r["title"],
                    "summary": r["summary"][:200] if r["summary"] else "",
                    "is_recent": True,
                    "url": r["url"],
                })
            return fallback

    def fetch_by_category(self, category_key: str) -> list[dict]:
        """针对单个板块搜索并提取新闻"""
        queries = CATEGORY_SEARCH_QUERIES.get(category_key, [])
        if not queries:
            return []

        all_results = []
        seen_urls = set()

        for query in queries:
            results = self._search_google_news(query, max_results=8)
            for r in results:
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    all_results.append(r)

        if not all_results:
            return []

        # 先日期预过滤
        in_window = []
        for r in all_results:
            if r.get("published_at"):
                try:
                    dt = datetime.fromisoformat(r["published_at"])
                    if dt >= CUTOFF_DATE:
                        in_window.append(r)
                except Exception:
                    in_window.append(r)
            else:
                in_window.append(r)

        console.log(
            f"[dim]搜索 [{CATEGORIES[category_key]['name']}]: "
            f"{len(all_results)} → {len(in_window)} 条(日期预过滤)[/dim]"
        )

        # LLM 提取（仅在 LLM 配置时使用）
        if OPENAI_API_KEY and OPENAI_API_KEY != "sk-xxx":
            extracted = self._extract_with_llm(in_window, category_key)
        else:
            # 无 LLM：直接透传 Google News 结果（前 5 条）
            extracted = [{
                "title": r["title"],
                "summary": r.get("summary", ""),
                "is_recent": True,
                "url": r["url"],
            } for r in in_window[:5]]

        # 标准化
        items = []
        now = datetime.now(timezone.utc)
        for ext in extracted:
            if not ext.get("is_recent", False):
                continue

            item = self.normalize_item(
                title=ext.get("title", ""),
                url=ext.get("url", ""),
                source_name=f"Google News",
                source_type="web_search",
                published_at=now - timedelta(hours=12),  # 估算
                summary=ext.get("summary", ""),
            )
            item["category"] = category_key
            items.append(item)

        console.log(
            f"[green]搜索 [{CATEGORIES[category_key]['name']}]: "
            f"{len(extracted)} 条提取 → {len(items)} 条有效[/green]"
        )
        return items

    def fetch(self) -> list[dict]:
        """采集所有板块的搜索新闻"""
        all_items = []
        for cat_key in CATEGORIES:
            items = self.fetch_by_category(cat_key)
            all_items.extend(items)
        console.log(f"[green]AI 搜索总计: {len(all_items)} 条[/green]")
        return all_items
