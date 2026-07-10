"""B站浏览器采集器

用 Playwright 无头浏览器直接搜索 B站，抓取视频/图文内容。
比 Google News RSS 中转方案覆盖量大得多（实测 1051 条 vs 3 条）。

包含两个子模块：
1. 关键词搜索 — 按分类搜索 B站
2. 官号主页抓取 — 直接访问厂商 B站官号空间页，抓最新投稿+简介

适用场景：本地开发（中国 IP 无障碍访问 B站）
CI 环境默认关闭，由 BILIBILI_BROWSER=true 环境变量开启。
"""

import json
import os
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

from rich.console import Console

from config import CATEGORIES
from .base import BaseCollector

console = Console()

# ========== 搜索关键词（按分类组织） ==========
# 原则：用 B站 用户实际搜索的词，中文口语化，覆盖品牌名+通用词+场景词
BILIBILI_SEARCH_KEYWORDS = {
    "steam_deck": [
        "Steam Deck 掌机",
        "Steam Deck 评测",
        "Steam Deck OLED",
        "SteamOS 更新",
        "Steam Deck 游戏",
        "Steam Deck 性能",
        "Steam Deck 二代",
    ],
    "windows_handheld": [
        # 一线品牌
        "ROG Ally 掌机", "ROG Ally 评测", "ROG Ally 二代",
        "AYANEO 掌机", "AYANEO 新品", "AYANEO 评测",
        "GPD 掌机", "GPD Win 新品", "GPD 评测",
        "OneXPlayer 掌机", "壹号本 掌机",
        "联想 Legion Go 掌机", "联想 拯救者 掌机",
        "微星 Claw 掌机", "MSI Claw 掌机",
        "索泰 ZONE 掌机", "ZOTAC 掌机",
        # 其他品牌
        "AOKZOE 掌机",
        "飞行家 掌机", "ONEXFLY 掌机",
        "攻氪 KONKR 掌机",
        # 通用
        "Windows 掌机 新品", "Windows 掌机 推荐",
        "掌机 新品 发布", "掌机 发布会",
        "Win 掌机 2024", "Win 掌机 2025", "Win 掌机 2026",
        "PC 掌机 评测",
        "掌机 性能 对比",
    ],
    "android_handheld": [
        # 品牌
        "安卓掌机", "安卓掌机 新品", "安卓掌机 推荐",
        "Retroid Pocket", "Retroid 掌机", "沙雕 掌机",
        "AYN Odin 掌机", "奥丁 掌机", "奥丁 安卓",
        "盖世小鸡 手柄", "盖世小鸡 掌机",
        "拉伸手柄 安卓",
        # 通用
        "安卓 掌上游戏机",
        "高通 掌机", "骁龙 掌机",
        "安卓 模拟器 掌机",
    ],
    "linux_handheld": [
        # 品牌
        "开源掌机", "开源掌机 新品", "开源掌机 推荐",
        "Anbernic 掌机", "安伯尼克", "周哥 掌机", "RG 掌机",
        "Miyoo 掌机", "Miyoo Mini",
        "TrimUI 掌机", "吹米 掌机",
        "PowKiddy 掌机",
        "霸王小子 掌机",
        "泡机堂 掌机",
        # 通用
        "复古掌机 新品", "复古掌机 推荐",
        "Linux 掌机",
        "怀旧 掌机 游戏",
        "寨机 推荐", "寨机 评测",
    ],
    "console": [
        # Switch 系列
        "Switch 2", "Switch 2 评测", "Switch 2 游戏",
        "Switch 2 爆料", "Switch 2 新品",
        "NS2 掌机", "NS2 评测",
        # PS 系列
        "PS5 Pro", "PS5 新品",
        "索尼 掌机", "PSP 新机",
        "PlayStation 掌机",
        # Xbox 系列
        "Xbox 掌机", "Xbox 新品",
        # 任天堂
        "任天堂 新机", "任天堂 发布会",
        "任天堂 掌机 2026",
        # 通用
        "主机 新闻", "次世代 主机",
    ],
    "emulator": [
        # Switch 模拟器
        "Switch 模拟器 安卓", "Switch 模拟器 PC",
        "Yuzu 模拟器", "Suyu 模拟器", "Sudachi 模拟器",
        "Ryujinx 模拟器",
        # 其他模拟器
        "PS4 模拟器", "PS3 模拟器",
        "Winlator 安卓", "Winlator 模拟器",
        "Mobox 模拟器",
        "Citra 模拟器", "3DS 模拟器",
        "Cemu 模拟器", "Wii U 模拟器",
        "Vita3K 模拟器", "PSV 模拟器",
        # 通用
        "模拟器 更新", "模拟器 推荐",
        "安卓 模拟器 掌机 游戏",
    ],
}

# ========== 厂商官号 B站 UID ==========
# 搜索品牌名时，B站搜索结果会自动包含官号内容
# 这里列出已知的官号 mids，用于识别和优先排序
MANUFACTURER_ACCOUNTS = {
    # === Windows 掌机 ===
    "AYANEO官方": {"mid": 17560816, "category": "windows_handheld"},
    "GPD掌机官方": {"mid": 13258977, "category": "windows_handheld"},
    "壹号本科技": {"mid": 519903075, "category": "windows_handheld"},  # OneXPlayer
    "AOKZOE掌机": {"mid": 1760429024, "category": "windows_handheld"},
    "ROG玩家国度": {"mid": 383768376, "category": "windows_handheld"},  # 华硕官方，机构认证
    # 联想 Legion Go / 微星 Claw / 索泰 ZONE / 飞行家 ONEXFLY / 攻氪 KONKR — 未找到独立官号
    # === 安卓掌机 ===
    "AYN掌机": {"mid": 2008853645, "category": "android_handheld"},  # AYN Odin
    "Retroid官方": {"mid": 2127886581, "category": "android_handheld"},
    "盖世小鸡": {"mid": 429886010, "category": "android_handheld"},  # 外设/拉伸手柄
    # === Linux / 开源掌机 ===
    "Anbernic官方": {"mid": 678288374, "category": "linux_handheld"},
    "TrimUI掌机": {"mid": 3494368207964283, "category": "linux_handheld"},
    "PowKiddy掌机": {"mid": 1479010746, "category": "linux_handheld"},
    # Miyoo / 霸王小子 / 吹米 / 泡机堂 — 未找到独立官号
    # === 主机 ===
    # PlayStation中国 / 腾讯NintendoSwitch / Xbox中国 — 未确认
}

# 按分类组织的官号搜索关键词
MANUFACTURER_SEARCHES = [
    # === Windows 掌机 ===
    ("AYANEO 掌机", "windows_handheld"),
    ("GPD 掌机 官方", "windows_handheld"),
    ("壹号本 OneXPlayer 掌机", "windows_handheld"),
    ("AOKZOE 掌机", "windows_handheld"),
    ("ROG Ally 掌机", "windows_handheld"),
    ("联想 拯救者 Legion Go 掌机", "windows_handheld"),
    ("微星 MSI Claw 掌机", "windows_handheld"),
    ("索泰 ZOTAC ZONE 掌机", "windows_handheld"),
    ("ONEXFLY 飞行家 掌机", "windows_handheld"),
    ("攻氪 KONKR 掌机", "windows_handheld"),
    # === 安卓掌机 ===
    ("AYN Odin 奥丁 掌机", "android_handheld"),
    ("Retroid Pocket 沙雕 掌机", "android_handheld"),
    ("盖世小鸡 掌机 手柄", "android_handheld"),
    # === Linux / 开源掌机 ===
    ("Anbernic 安伯尼克 掌机", "linux_handheld"),
    ("Miyoo Mini 掌机", "linux_handheld"),
    ("TrimUI 吹米 掌机", "linux_handheld"),
    ("PowKiddy 掌机", "linux_handheld"),
    ("霸王小子 掌机", "linux_handheld"),
    ("泡机堂 掌机", "linux_handheld"),
    ("周哥 开源掌机", "linux_handheld"),
    # === 主机 ===
    ("PlayStation 中国", "console"),
    ("任天堂 Switch 官方", "console"),
    ("Xbox 中国 官方", "console"),
    ("腾讯 NintendoSwitch", "console"),
]

# 每次采集的上限
MAX_SEARCH_PER_KEYWORD = 8
MAX_MANUFACTURER_PER_ACCOUNT = 8
SEARCH_DELAY_MIN = 2
SEARCH_DELAY_MAX = 4


def _parse_bilibili_count(count_str: str) -> int:
    """解析 B站播放量/弹幕数文字为整数"""
    if not count_str:
        return 0
    count_str = count_str.strip()
    try:
        if "万" in count_str:
            return int(float(count_str.replace("万", "")) * 10000)
        return int(count_str.replace(",", ""))
    except (ValueError, TypeError):
        return 0


class BilibiliBrowserCollector(BaseCollector):
    """用 Playwright 浏览器采集 B站内容：关键词搜索 + 官号主页"""

    def __init__(self):
        super().__init__("BilibiliBrowser")
        self._seen_bvs: set[str] = set()
        self._browser = None
        self._context = None
        self._page = None

    def _warmup(self):
        """预热浏览器环境"""
        try:
            self._page.goto("https://www.bilibili.com", wait_until="domcontentloaded", timeout=15000)
            self._page.wait_for_timeout(2000)
        except Exception as e:
            console.log(f"[dim]B站预热失败: {e}[/dim]")

    def _fetch_video_description(self, bvid: str) -> str:
        """通过 B站 API 获取视频简介文字"""
        try:
            result = self._page.evaluate(f"""
                async () => {{
                    try {{
                        const res = await fetch('https://api.bilibili.com/x/web-interface/view?bvid={bvid}');
                        const json = await res.json();
                        return json.data?.desc || '';
                    }} catch(e) {{ return ''; }}
                }}
            """)
            return result or ""
        except Exception:
            return ""

    def _fetch_from_space_api(self, mid: int, name: str, cat_hint: str) -> list[dict]:
        """根据 UID 直接从 B站 用户空间 API 拉取最近视频"""
        try:
            raw = self._page.evaluate(f"""
                async () => {{
                    try {{
                        const res = await fetch(
                            'https://api.bilibili.com/x/space/wbi/arc/search?mid={mid}&ps=10&pn=1&order=pubdate',
                            {{ headers: {{ 'Referer': 'https://space.bilibili.com/{mid}' }} }}
                        );
                        const json = await res.json();
                        if (json.code === 0 && json.data?.list?.vlist) {{
                            return json.data.list.vlist.map(v => ({{
                                title: v.title || '',
                                bvid: v.bvid || '',
                                author: v.author || '',
                                description: (v.description || '').substring(0, 200),
                                play: v.play || 0,
                                video_review: v.video_review || 0,
                                pubdate: v.created || 0,
                                length: v.length || '',
                                mid: v.mid || {mid},
                            }}));
                        }}
                    }} catch(e) {{}}
                    return [];
                }}
            """)
            result = raw if isinstance(raw, list) else []
        except Exception:
            return []

        results = []
        for v in result:
            title = v.get("title", "").strip()
            bvid = v.get("bvid", "")
            if not title or not bvid:
                continue
            if bvid in self._seen_bvs:
                continue
            self._seen_bvs.add(bvid)

            url = f"https://www.bilibili.com/video/{bvid}"
            author = v.get("author", "")
            description = v.get("description", "").strip()
            if description in ("-", "暂无简介"):
                description = ""

            summary_parts = [f"UP主: {author}"]
            play_count = v.get("play", 0)
            if play_count > 0:
                summary_parts.append(
                    f"播放: {play_count/10000:.1f}万" if play_count >= 10000
                    else f"播放: {play_count}"
                )
            if description:
                summary_parts.append(description[:200])

            published_at = None
            pubdate = v.get("pubdate", 0)
            if pubdate > 0:
                try:
                    published_at = datetime.fromtimestamp(pubdate, tz=timezone.utc)
                except Exception:
                    pass

            results.append({
                "title": title,
                "url": url,
                "published_at": published_at,
                "summary": " | ".join(summary_parts),
                "raw": {
                    "bvid": bvid, "author": author, "mid": v.get("mid", mid),
                    "play_count": play_count, "danmaku": str(v.get("video_review", "")),
                    "duration": v.get("length", ""), "is_official": True,
                    "manufacturer": name,
                },
                "category_hint": cat_hint,
            })

        return results

    def _search_keyword(self, keyword: str, cat_hint: str) -> list[dict]:
        """搜索一个关键词，提取视频卡片数据"""
        encoded = quote(keyword)
        url = f"https://search.bilibili.com/all?keyword={encoded}&order=pubdate"

        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            return []

        try:
            self._page.wait_for_selector(".bili-video-card", timeout=8000)
        except Exception:
            return []

        self._page.wait_for_timeout(1000)

        # 轻量滚动
        for _ in range(1):
            self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self._page.wait_for_timeout(1000)

        videos = self._page.evaluate("""
            () => {
                const videos = [];
                document.querySelectorAll('.bili-video-card').forEach(card => {
                    try {
                        const titleEl = card.querySelector('.bili-video-card__info--tit');
                        const title = titleEl ? (titleEl.getAttribute('title') || titleEl.textContent.trim()) : '';
                        if (!title) return;

                        const linkEl = card.querySelector('a[href*="/video/"]');
                        const url = linkEl ? linkEl.href : '';

                        const authorEl = card.querySelector('.bili-video-card__info--author');
                        const author = authorEl ? authorEl.textContent.trim() : '';

                        const statsItems = card.querySelectorAll('.bili-video-card__stats--item');
                        let plays = '', danmaku = '';
                        statsItems.forEach(item => {
                            const text = item.textContent.trim();
                            if (!plays) plays = text;
                            else if (!danmaku) danmaku = text;
                        });

                        videos.push({ title, url, author, plays, danmaku });
                    } catch(e) {}
                });
                return videos;
            }
        """)

        results = []
        for v in videos[:MAX_SEARCH_PER_KEYWORD]:
            title = v.get("title", "").strip()
            url = v.get("url", "").strip()
            if not title or not url:
                continue

            bv_match = re.search(r"/video/(BV[\w]+)", url)
            bvid = bv_match.group(1) if bv_match else url

            if bvid in self._seen_bvs:
                continue
            self._seen_bvs.add(bvid)

            author = v.get("author", "")
            summary_parts = []
            if author:
                summary_parts.append(f"UP主: {author}")
            if v.get("plays"):
                summary_parts.append(f"播放: {v['plays']}")

            # 识别是否为官号
            is_official = any(
                author == name or name in author
                for name in MANUFACTURER_ACCOUNTS
            )
            source_name = f"B站@{author}" if is_official else f"B站搜索(via {keyword})"

            results.append({
                "title": title,
                "url": url,
                "published_at": None,
                "summary": " | ".join(summary_parts),
                "raw": {
                    "bvid": bvid,
                    "author": author,
                    "play_count": _parse_bilibili_count(v.get("plays", "")),
                    "danmaku": v.get("danmaku", ""),
                    "keyword": keyword,
                    "is_official": is_official,
                },
                "category_hint": cat_hint,
            })

        return results

    def _search_manufacturer(self, query: str, cat_hint: str) -> list[dict]:
        """搜索厂商官号内容，通过 B站搜索 API 获取含简介的视频数据"""
        api_url = (
            "https://api.bilibili.com/x/web-interface/search/type"
            f"?search_type=video&keyword={quote(query)}&page=1&order=pubdate"
        )

        try:
            raw = self._page.evaluate(
                f"""async () => {{
                    try {{
                        const r = await fetch({json.dumps(api_url)});
                        const j = await r.json();
                        if (j.code !== 0 || !j.data?.result) return [];
                        return j.data.result.slice(0, {MAX_MANUFACTURER_PER_ACCOUNT});
                    }} catch(e) {{ return []; }}
                }}"""
            )
            result = raw if isinstance(raw, list) else []
        except Exception:
            return []

        if not result:
            return []

        results = []
        for v in result:
            # API 返回的 title 含 HTML 标签（如 <em class="keyword">）
            raw_title = v.get("title", "")
            title = re.sub(r"<[^>]+>", "", raw_title).strip()
            bvid = v.get("bvid", "")
            if not title or not bvid:
                continue

            if bvid in self._seen_bvs:
                continue

            url = f"https://www.bilibili.com/video/{bvid}"
            author = v.get("author", "")
            description = v.get("description", "").strip()
            if description in ("-", "暂无简介"):
                description = ""

            # 摘要：作者 + 播放量 + 简介
            summary_parts = []
            if author:
                summary_parts.append(f"UP主: {author}")
            play_count = v.get("play", 0)
            if play_count > 0:
                summary_parts.append(
                    f"播放: {play_count/10000:.1f}万" if play_count >= 10000
                    else f"播放: {play_count}"
                )
            if description:
                summary_parts.append(description[:200])

            # 时间戳转换
            published_at = None
            pubdate = v.get("pubdate", 0)
            if pubdate > 0:
                try:
                    published_at = datetime.fromtimestamp(pubdate, tz=timezone.utc)
                except Exception:
                    pass

            # 识别官号
            is_official = any(
                author == name or name in author
                for name in MANUFACTURER_ACCOUNTS
            )

            results.append({
                "title": title,
                "url": url,
                "published_at": published_at,
                "summary": " | ".join(summary_parts),
                "raw": {
                    "bvid": bvid,
                    "author": author,
                    "mid": v.get("mid", 0),
                    "play_count": play_count,
                    "danmaku": str(v.get("video_review", "")),
                    "duration": v.get("length", ""),
                    "tags": v.get("tag", ""),
                    "is_official": is_official,
                    "manufacturer": query,
                },
                "category_hint": cat_hint,
            })

            self._seen_bvs.add(bvid)

        return results

    def fetch(self) -> list[dict]:
        """采集 B站内容：关键词搜索 + 官号搜索"""
        if os.getenv("BILIBILI_BROWSER", "").lower() not in ("1", "true", "yes"):
            console.log("[dim]B站浏览器采集已跳过 (设置 BILIBILI_BROWSER=true 启用)[/dim]")
            return []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            console.log("[red]playwright 未安装，跳过 B站浏览器采集[/red]")
            return []

        total_kw = sum(len(kws) for kws in BILIBILI_SEARCH_KEYWORDS.values())
        console.print(f"\n[yellow]B站浏览器采集: {total_kw} 关键词 + {len(MANUFACTURER_SEARCHES)} 官号[/yellow]")

        all_items = []

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )
            page = context.new_page()
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
            """)

            self._page = page
            self._warmup()

            # ===== 阶段 1：关键词搜索 =====
            for cat_key, keywords in BILIBILI_SEARCH_KEYWORDS.items():
                for kw in keywords:
                    try:
                        videos = self._search_keyword(kw, cat_key)
                        for v in videos:
                            item = self.normalize_item(
                                title=v["title"],
                                url=v["url"],
                                source_name=v.get("raw", {}).get("is_official")
                                    and f"B站官号@{v['raw']['author']}"
                                    or f"B站(via {kw})",
                                source_type="bilibili_browser",
                                published_at=v.get("published_at"),
                                summary=v.get("summary", ""),
                                raw_data=v.get("raw", {}),
                            )
                            item["category"] = v["category_hint"]
                            all_items.append(item)
                        if videos:
                            console.log(f"[dim]B站搜索 '{kw}': {len(videos)} 条[/dim]")
                    except Exception as e:
                        console.log(f"[red]B站搜索 '{kw}' 失败: {e}[/red]")

                    time.sleep(random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX))

            # ===== 阶段 2a：官号搜索（关键词 API + 简介） =====
            console.log("\n[yellow]  搜索厂商官号 (含视频简介):[/yellow]")
            for query, cat_hint in MANUFACTURER_SEARCHES:
                try:
                    videos = self._search_manufacturer(query, cat_hint)
                    for v in videos:
                        is_official = v.get("raw", {}).get("is_official", False)
                        source = f"B站官号@{v['raw']['author']}" if is_official else f"B站(via {query})"
                        item = self.normalize_item(
                            title=v["title"],
                            url=v["url"],
                            source_name=source,
                            source_type="bilibili_manufacturer",
                            published_at=v.get("published_at"),
                            summary=v.get("summary", ""),
                            raw_data=v.get("raw", {}),
                        )
                        item["category"] = v["category_hint"]
                        all_items.append(item)
                    if videos:
                        console.log(f"[dim]B站官号 '{query}': {len(videos)} 条[/dim]")
                except Exception as e:
                    console.log(f"[red]B站官号 '{query}' 失败: {e}[/red]")

                time.sleep(random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX))

            # ===== 阶段 2b：已知 UID 的官号直接用 space API 拉视频 =====
            console.log("\n[yellow]  直接拉取官号空间 (space API):[/yellow]")
            for name, info in MANUFACTURER_ACCOUNTS.items():
                mid = info["mid"]
                cat_hint = info["category"]
                try:
                    videos = self._fetch_from_space_api(mid, name, cat_hint)
                    for v in videos:
                        item = self.normalize_item(
                            title=v["title"],
                            url=v["url"],
                            source_name=f"B站官号@{v['raw']['author']}",
                            source_type="bilibili_space",
                            published_at=v.get("published_at"),
                            summary=v.get("summary", ""),
                            raw_data=v.get("raw", {}),
                        )
                        item["category"] = v["category_hint"]
                        all_items.append(item)
                    if videos:
                        console.log(f"[dim]  B站官号 '{name}' (UID:{mid}): {len(videos)} 条[/dim]")
                except Exception as e:
                    console.log(f"[red]  B站官号 '{name}' 失败: {e}[/red]")
                time.sleep(random.uniform(1.0, 2.0))

            browser.close()

        console.log(f"[green]B站浏览器总计: {len(all_items)} 条[/green]")
        return all_items
