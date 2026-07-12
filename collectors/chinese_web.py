"""中文源补充采集器

Google News RSS 搜索引擎，配合 site: 限定，
获取 B站/微博/微信/贴吧/知乎/游戏媒体等中文源内容。
不直接抓取页面（反爬太强），走搜索引擎中转，稳定可靠。
"""

from datetime import datetime, timezone
from urllib.parse import quote

import feedparser
import requests
from rich.console import Console

from config import CATEGORIES, CUTOFF_DATE
from .base import BaseCollector
from .keyword_library import get_event_keywords_with_sites

console = Console()

# 每个板块的搜索关键词，site: 限定在中文常用平台
# 覆盖 B站/微博/微信/贴吧/知乎/游戏媒体/电商，重点抓厂商官宣和爆料
SITE_QUERIES = {
    "steam_deck": [
        # B站 + 微博 + 微信
        "Steam Deck site:bilibili.com",
        "Steam Deck 掌机 评测 site:bilibili.com",
        "Steam Deck site:weibo.com",
        "Steam Deck 掌机 site:mp.weixin.qq.com",
        # 贴吧
        "Steam Deck site:tieba.baidu.com",
        "steamdeck 吧 site:tieba.baidu.com",
        # 媒体/社区
        "Steam Deck 掌机 site:zhihu.com",
        "Steam Deck 评测 site:gamersky.com",
        "Steam Deck 掌机 site:yystv.cn",
        "Steam Deck site:3dmgame.com",
        "Steam Deck 掌机 site:smzdm.com",
        # 事件（来自关键词库）
        *get_event_keywords_with_sites("steam_deck", ["bilibili.com", "tieba.baidu.com"]),
    ],
    "windows_handheld": [
        # B站 — 厂商 + 通用
        "ROG Ally 掌机 site:bilibili.com",
        "AYANEO 掌机 site:bilibili.com",
        "GPD 掌机 site:bilibili.com",
        "OneXPlayer 壹号本 site:bilibili.com",
        "MSI Claw 掌机 site:bilibili.com",
        "Legion Go 掌机 site:bilibili.com",
        "Windows 掌机 新品 site:bilibili.com",
        "掌机 发布会 直播 site:bilibili.com",
        "掌机 新品 爆料 site:bilibili.com",
        "掌机 传闻 site:bilibili.com",
        "掌机 曝光 site:bilibili.com",
        # 微博 — 厂商官号
        "AYANEO site:weibo.com",
        "ROG 掌机 site:weibo.com",
        "GPD 掌机 site:weibo.com",
        "Windows 掌机 site:weibo.com",
        "掌机 爆料 site:weibo.com",
        "新掌机 传闻 site:weibo.com",
        "掌机 曝光 专利 site:weibo.com",
        # 微信 — 厂商发布
        "AYANEO 掌机 site:mp.weixin.qq.com",
        "ROG Ally 掌机 site:mp.weixin.qq.com",
        "Windows 掌机 新品 site:mp.weixin.qq.com",
        "掌机 爆料 site:mp.weixin.qq.com",
        # 贴吧
        "ROG Ally 吧 site:tieba.baidu.com",
        "AYANEO 吧 site:tieba.baidu.com",
        "GPD 掌机 site:tieba.baidu.com",
        "Windows 掌机 site:tieba.baidu.com",
        "掌机 爆料 site:tieba.baidu.com",
        "掌机 曝光 吧 site:tieba.baidu.com",
        # 媒体/社区
        "Windows 掌机 推荐 site:zhihu.com",
        "ROG Ally 评测 site:gamersky.com",
        "AYANEO 评测 site:smzdm.com",
        "GPD Win 掌机 site:smzdm.com",
        "Windows 掌机 site:3dmgame.com",
        "掌机 新品 发布 site:yystv.cn",
        "掌机 专利 曝光 site:zhihu.com",
        "掌机 爆料 site:gamersky.com",
        "掌机 传闻 site:3dmgame.com",
        "掌机 新品 2026 site:yystv.cn",
        # 事件（来自关键词库）
        *get_event_keywords_with_sites("windows_handheld", ["bilibili.com", "tieba.baidu.com"]),
    ],
    "android_handheld": [
        # B站
        "安卓掌机 site:bilibili.com",
        "Retroid 掌机 site:bilibili.com",
        "Odin 掌机 site:bilibili.com",
        "安卓掌机 新品 site:bilibili.com",
        "盖世小鸡 手柄 site:bilibili.com",
        # 微博
        "Retroid site:weibo.com",
        "AYN Odin 掌机 site:weibo.com",
        "安卓掌机 site:weibo.com",
        # 微信
        "Retroid 掌机 site:mp.weixin.qq.com",
        "安卓掌机 推荐 site:mp.weixin.qq.com",
        # 贴吧
        "Retroid 吧 site:tieba.baidu.com",
        "Odin 掌机 site:tieba.baidu.com",
        "安卓掌机 site:tieba.baidu.com",
        # 媒体/社区
        "Retroid 掌机 site:zhihu.com",
        "安卓掌机 推荐 site:smzdm.com",
        "盖世小鸡 手柄 site:smzdm.com",
        "拉伸手柄 评测 site:gamersky.com",
        "安卓游戏机 site:smzdm.com",
        # 事件（来自关键词库）
        *get_event_keywords_with_sites("android_handheld", ["bilibili.com", "tieba.baidu.com"]),
    ],
    "linux_handheld": [
        # B站
        "开源掌机 site:bilibili.com",
        "Anbernic 掌机 site:bilibili.com",
        "Miyoo 掌机 site:bilibili.com",
        "TrimUI 掌机 site:bilibili.com",
        "周哥 掌机 site:bilibili.com",
        "PowKiddy 掌机 site:bilibili.com",
        "开源掌机 新品 评测 site:bilibili.com",
        # 微博
        "Anbernic site:weibo.com",
        "开源掌机 site:weibo.com",
        # 微信
        "Anbernic 掌机 site:mp.weixin.qq.com",
        "开源掌机 推荐 site:mp.weixin.qq.com",
        # 贴吧
        "开源掌机 吧 site:tieba.baidu.com",
        "Anbernic site:tieba.baidu.com",
        "Miyoo 掌机 site:tieba.baidu.com",
        # 媒体/社区
        "开源掌机 推荐 site:zhihu.com",
        "Anbernic 掌机 site:smzdm.com",
        "Miyoo 掌机 site:gamersky.com",
        "开源掌机 site:3dmgame.com",
        # 事件（来自关键词库）
        *get_event_keywords_with_sites("linux_handheld", ["bilibili.com", "tieba.baidu.com"]),
    ],
    "console": [
        # B站
        "Switch 2 site:bilibili.com",
        "PS5 Pro site:bilibili.com",
        "Xbox Series site:bilibili.com",
        "任天堂 新机 site:bilibili.com",
        "主机 新闻 发布会 site:bilibili.com",
        "Switch 2 爆料 site:bilibili.com",
        "索尼 掌机 传闻 site:bilibili.com",
        # 微博
        "Switch 2 site:weibo.com",
        "PlayStation 中国 site:weibo.com",
        "任天堂 site:weibo.com",
        "Xbox 中国 site:weibo.com",
        "Switch 2 传闻 site:weibo.com",
        # 微信
        "Switch 2 site:mp.weixin.qq.com",
        "PS5 Pro site:mp.weixin.qq.com",
        "任天堂 主机 site:mp.weixin.qq.com",
        "Switch 2 传闻 site:mp.weixin.qq.com",
        "索尼 掌机 site:mp.weixin.qq.com",
        # 贴吧
        "Switch 2 吧 site:tieba.baidu.com",
        "PS5 吧 site:tieba.baidu.com",
        "Xbox 吧 site:tieba.baidu.com",
        "Switch 2 传闻 site:tieba.baidu.com",
        "索尼 掌机 site:tieba.baidu.com",
        # 媒体/社区
        "Switch 2 评测 site:zhihu.com",
        "PS5 Pro 新闻 site:gamersky.com",
        "主机 新闻 site:yystv.cn",
        "次世代主机 site:3dmgame.com",
        # 事件（来自关键词库）
        *get_event_keywords_with_sites("console", ["bilibili.com", "zhihu.com"]),
    ],
    "emulator": [
        # B站
        "模拟器 更新 site:bilibili.com",
        "Switch 模拟器 site:bilibili.com",
        "PS4 模拟器 site:bilibili.com",
        "Winlator 模拟器 site:bilibili.com",
        "Yuzu 模拟器 site:bilibili.com",
        # 微博
        "模拟器 更新 site:weibo.com",
        "Switch 模拟器 site:weibo.com",
        # 微信
        "模拟器 更新 site:mp.weixin.qq.com",
        "Switch 模拟器 site:mp.weixin.qq.com",
        # 贴吧
        "模拟器 吧 site:tieba.baidu.com",
        "Yuzu 模拟器 site:tieba.baidu.com",
        # 媒体/社区
        "模拟器 安卓 推荐 site:smzdm.com",
        "Yuzu 模拟器 site:zhihu.com",
        "Switch 模拟器 最新 site:gamersky.com",
        # 事件（来自关键词库）
        *get_event_keywords_with_sites("emulator", ["bilibili.com", "zhihu.com"]),
    ],
}


class ChineseWebCollector(BaseCollector):
    def __init__(self):
        super().__init__("ChineseWeb")

    def _search_google(self, query: str, max_results: int = 8) -> list[dict]:
        """用 Google News RSS 搜索"""
        results = []
        try:
            url = f"https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (compatible; GamingNewsBot/1.0)"
            })
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)

            for entry in feed.entries[:max_results]:
                title = getattr(entry, "title", "").strip()
                title = title.split(" - ")[0]  # 去掉来源后缀

                link = getattr(entry, "link", "")
                # 提取真实 URL
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(link)
                params = parse_qs(parsed.query)
                real_url = params.get("url", [link])[0]

                pub_date = None
                tp = getattr(entry, "published_parsed", None)
                if tp:
                    try:
                        pub_date = datetime(*tp[:6], tzinfo=timezone.utc)
                    except Exception:
                        pass

                # 来源名称
                source = getattr(entry, "source", {})
                source_name = source.get("title", "Web") if isinstance(source, dict) else str(source)

                results.append({
                    "title": title,
                    "url": real_url,
                    "summary": "",
                    "source_name": source_name,
                    "published_at": pub_date,
                })

        except Exception as e:
            console.log(f"[dim]Google搜索失败 [{query[:40]}]: {e}[/dim]")

        return results

    def fetch_by_category(self, cat_key: str) -> list[dict]:
        queries = SITE_QUERIES.get(cat_key, [])
        items = []
        seen_urls = set()

        for query in queries:
            g_results = self._search_google(query)
            for r in g_results:
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    pub = r.get("published_at")
                    if pub and pub < CUTOFF_DATE:
                        continue
                    item = self.normalize_item(
                        title=r["title"],
                        url=r["url"],
                        source_name=r["source_name"],
                        source_type="chinese_web",
                        published_at=pub,
                        summary=r.get("summary", ""),
                    )
                    item["category"] = cat_key
                    items.append(item)

        return items

    def fetch(self) -> list[dict]:
        all_items = []
        for cat_key in CATEGORIES:
            items = self.fetch_by_category(cat_key)
            all_items.extend(items)
            if items:
                console.log(f"[dim]中文源 [{CATEGORIES[cat_key]['name']}]: {len(items)} 条[/dim]")

        console.log(f"[green]中文源总计: {len(all_items)} 条[/green]")
        return all_items
