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
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from rich.console import Console

from urllib.parse import urlencode, urlparse

from config import CATEGORIES
from .base import BaseCollector

console = Console()

# ========== 搜索关键词（按分类组织） ==========
# 原则：品牌+核心词一个，避免中英文重复/评测子词（大词已覆盖）
# 精简目标：减少 API 调用次数，降低 B站 风控概率
BILIBILI_SEARCH_KEYWORDS = {
    "steam_deck": [
        "Steam Deck 掌机",
        "Steam Deck OLED",
        "SteamOS 更新",
        "Steam Deck 游戏",
        "Steam Deck 二代",
        "Steam Machine",
        "Steam 控制器",
        "Steam Controller",
    ],
    "windows_handheld": [
        # 一线品牌
        "ROG Ally 掌机", "ROG Ally 二代",
        "AYANEO 掌机", "AYANEO 新品",
        "GPD 掌机", "GPD Win 新品",
        "OneXPlayer 掌机", "壹号本 掌机",
        "联想 Legion Go 掌机", "联想 拯救者 掌机",
        "MSI Claw 掌机", "微星 Claw 掌机",
        "索泰 ZONE 掌机",
        # 其他品牌
        "AOKZOE 掌机",
        "ONEXFLY 掌机",
        # 通用
        "Windows 掌机", "Win 掌机",
        "Windows 掌机 新品",
        "掌机 新品 发布",
    ],
    "android_handheld": [
        "安卓掌机", "安卓掌机 新品",
        "攻氪 KONKR 掌机",
        "Retroid Pocket", "RP 掌机", "沙雕 掌机",
        "AYN Odin 掌机", "奥丁 掌机",
        "AYANEO Pocket",
        "Anbernic 安卓", "RG 安卓掌机",
        "高通 掌机", "芒米 掌机", "芒米 AIR Y",
        "安卓 模拟器 掌机",
    ],
    "linux_handheld": [
        # 品牌（中英文合并）
        "开源掌机", "开源掌机 新品",
        "Anbernic 掌机", "周哥 掌机", "RG 掌机",
        "Miyoo 掌机", "Miyoo Mini",
        "TrimUI 掌机", "吹米 掌机",
        "霸王小子 掌机", "泡机堂 掌机",
        # 通用
        "复古掌机 新品",
        "Linux 掌机", "寨机", "山寨掌机",
        "ArkOS", "OnionOS", "GarlicOS",
        # 小众品牌
        "GKD 掌机", "GKD Pixel",
        "MagicX 掌机",
        "吹砖 掌机",
    ],
    "playstation": [
        "PS5", "PS5 游戏", "PS5 Pro",
        "PS6", "PS6 游戏",
        "PlayStation 掌机", "PlayStation Portal",
        "索尼 主机",
    ],
    "xbox": [
        "Xbox Series", "Xbox 掌机",
        "Xbox Game Pass", "微软 主机",
    ],
    "nintendo": [
        "Switch 2", "Switch 2 爆料",
        "Switch 游戏", "Switch OLED",
        "任天堂 新机", "任天堂 发布会",
        "Switch 2 传闻",
    ],
    "emulator": [
        # Switch（独立模拟器各有新闻价值）
        "Switch 模拟器 安卓", "Switch 模拟器 PC",
        "Sudachi 模拟器", "Citron 模拟器",
        "Ryujinx 模拟器",
        # 平台级（平台词天然覆盖所有同平台模拟器）
        "PS4 模拟器", "PS3 模拟器", "PS2 模拟器",
        "PSP 模拟器", "PSV 模拟器",
        "3DS 模拟器", "NDS 模拟器",
        "GBA 模拟器", "GB 模拟器", "GBC 模拟器",
        "NGC 模拟器", "Wii 模拟器", "Wii U 模拟器",
        # Android 转译（掌机热点）
        "Winlator 模拟器", "Mobox 模拟器",
        # 前端/通用
        "模拟器 更新", "模拟器 汉化",
        "Batocera 系统", "RetroArch 模拟器",
        # 具体模拟器项目名（泛词搜不到时精准匹配）
        "melonDS 模拟器", "Drastic 模拟器", "Dolphin 模拟器 更新",
        "Citra 模拟器 更新", "Cemu 模拟器 更新", "Azahar 模拟器",
        "RPCS3 更新", "Vita3K 更新", "Xenia 模拟器", "Xemu 模拟器",
        "PCSX2 更新", "DuckStation 更新", "PPSSPP 更新",
    ],
    "peripherals": [
        # VR/AR
        "Quest 3", "PICO 4", "PSVR 2",
        "VR 头显 新品",
        # 手柄/控制器
        "游戏手柄 新品", "无线手柄 评测",
        "DualSense 手柄", "Xbox 手柄",
        "八位堂", "盖世小鸡 手柄",
        "拉伸手柄",
        # Switch 外设
        "Switch 配件", "Switch 外设",
        "Joy-Con", "Switch Pro 手柄",
        # PlayStation 外设
        "PS Portal", "DualSense Edge",
        # PC 游戏外设
        "PC 手柄", "Stream Deck",
        "外接显卡", "eGPU",
        # 模拟外设
        "方向盘 模拟", "飞行摇杆",
        # 改机/周边/创意
        "掌机 改机", "掌机 改造",
        "掌机 积木", "主机 积木",
        # 游戏手机/平板(AYANEO等跨界设备)
        "游戏手机 掌机",
        "AYANEO 手机", "AYANEO 平板",
        "红魔 游戏手机", "ROG 游戏手机",
    ],
}

# 分类排除词 — 防止搜索结果污染
CATEGORY_EXCLUSIONS: dict[str, list[str]] = {
    "steam_deck": [
        "ps5", "ps4", "playstation", "xbox", "joy-con", "joycon",
        "dualsense", "dualshock", "八位堂", "盖世小鸡",
        "手机", "安卓手机", "iphone", "ipad",
    ],
    "emulator": [
        "知乎", "zhihu", "deepseek", "论文", "isbn", "arxiv",
        "ai模型", "大模型", "gpt", "llm",
    ],
    "linux_handheld": [
        "手机", "安卓手机", "iphone",
    ],
    "android_handheld": [
        "手机壳", "手机膜", "平板",
    ],
    # 全分类盗版/破解工具排除
    "peripherals": [
        "相机", "云台", "稳定器",
    ],
    "_global": [
        "dlc 解锁", "dlc解锁", "dlc补丁", "解锁补丁",
        "破解补丁", "steam解锁", "epic解锁",
    ],
}

# ========== 厂商官号 B站 UID ==========
# 搜索品牌名时，B站搜索结果会自动包含官号内容
# 这里列出已知的官号 mids，用于识别和优先排序
MANUFACTURER_ACCOUNTS = {
    # === Windows 掌机 ===
    "AYANEO官方": {"mid": 366077183, "category": "windows_handheld"},
    "GPD掌机官方": {"mid": 13258977, "category": "windows_handheld"},
    "壹号本科技": {"mid": 519903075, "category": "windows_handheld"},  # OneXPlayer
    "AOKZOE掌机": {"mid": 1451834161, "category": "windows_handheld"},
    "ROG玩家国度": {"mid": 383768376, "category": "windows_handheld"},  # 华硕官方，机构认证
    # 联想 Legion Go / 微星 Claw / 索泰 ZONE / 飞行家 ONEXFLY / 攻氪 KONKR — 未找到独立官号
    # === 安卓掌机 ===
    "AYN掌机": {"mid": 2008853645, "category": "android_handheld"},  # AYN Odin
    "Retroid官方": {"mid": 2127886581, "category": "android_handheld"},
    "芒米科技": {"mid": 3546721894271865, "category": "android_handheld"},  # Mangmi
    "盖世小鸡": {"mid": 429886010, "category": "peripherals"},  # 手柄/外设
    # === Linux / 开源掌机 ===
    "Anbernic官方": {"mid": 678288374, "category": "linux_handheld"},
    "TrimUI掌机": {"mid": 3494368207964283, "category": "linux_handheld"},
    # Miyoo / 吹米 / 泡机堂 — 未找到独立官号; 霸王小子(PowKiddy)=388247581 无公开视频已移除
    # === 主机 ===
    # PlayStation中国 / 腾讯NintendoSwitch / Xbox中国 — 未确认
}

# 游戏资讯类 UP主（UID 直抓最新视频）
NEWS_UP_ACCOUNTS = {
    "二柄APP": {"mid": 90668673, "category": "nintendo"},
    "千夏的铲子": {"mid": 284571458, "category": None},       # 掌机垂类，LLM自由分类
    "董先生的游戏屋": {"mid": 441806315, "category": None},   # 掌机垂类，LLM自由分类
    "Xigua今天打游戏了吗": {"mid": 609290340, "category": "nintendo"},
    "游民星空官方": {"mid": 11233223, "category": "nintendo"},
}

# 按分类组织的官号搜索关键词（精简版：合并同义，去除非活跃品牌）
MANUFACTURER_SEARCHES = [
    # === Windows 掌机 ===
    ("AYANEO 掌机", "windows_handheld"),
    ("GPD 掌机", "windows_handheld"),
    ("OneXPlayer 壹号本 掌机", "windows_handheld"),
    ("AOKZOE 掌机", "windows_handheld"),
    ("ROG Ally 掌机", "windows_handheld"),
    ("联想 Legion Go 掌机", "windows_handheld"),
    ("MSI Claw 掌机", "windows_handheld"),
    ("索泰 ZONE 掌机", "windows_handheld"),
    ("ONEXFLY 飞行家 掌机", "windows_handheld"),
    ("攻氪 KONKR 掌机", "android_handheld"),
    # === 安卓掌机 ===
    ("AYN Odin 奥丁 掌机", "android_handheld"),
    ("Retroid Pocket 沙雕 掌机", "android_handheld"),
    ("芒米 掌机", "android_handheld"),
    ("盖世小鸡 手柄", "peripherals"),
    # === Linux / 开源掌机 ===
    ("Anbernic 安伯尼克 掌机", "linux_handheld"),
    ("Miyoo Mini 掌机", "linux_handheld"),
    ("TrimUI 吹米 掌机", "linux_handheld"),
    ("霸王小子 掌机", "linux_handheld"),
    ("周哥 开源掌机", "linux_handheld"),
    ("GKD 掌机", "linux_handheld"),
    ("MagicX 掌机", "linux_handheld"),
    # === 主机 ===
    ("PlayStation 中国", "playstation"),
    ("任天堂 Switch 官方", "nintendo"),
]

# 每次采集的上限
MAX_SEARCH_PER_KEYWORD = 8
MAX_MANUFACTURER_PER_ACCOUNT = 8
SEARCH_DELAY_MIN = 2
SEARCH_DELAY_MAX = 4


def _parse_bilibili_date(date_str: str) -> datetime | None:
    """解析 B站 时间文字为 datetime: '6小时前', '昨天', '7-10', '2025-6-15'"""
    if not date_str or not date_str.strip():
        return None
    date_str = date_str.strip()
    now = datetime.now(timezone.utc)
    try:
        if "小时前" in date_str:
            m = re.search(r"(\d+)", date_str)
            if m:
                return now - timedelta(hours=int(m.group(1)))
        elif "分钟前" in date_str:
            m = re.search(r"(\d+)", date_str)
            if m:
                return now - timedelta(minutes=int(m.group(1)))
        elif "昨天" in date_str:
            return now - timedelta(days=1)
        elif "前天" in date_str:
            return now - timedelta(days=2)
        elif re.search(r"(\d+)\s*天前", date_str):
            days = int(re.search(r"(\d+)\s*天前", date_str).group(1))
            return now - timedelta(days=days)
        elif re.match(r"^\d{1,2}-\d{1,2}$", date_str):
            month, day = date_str.split("-")
            return datetime(now.year, int(month), int(day), tzinfo=timezone.utc)
        elif re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", date_str):
            return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        pass
    return None


class BilibiliBrowserCollector(BaseCollector):
    """用 Playwright 浏览器采集 B站内容：关键词搜索 + 官号主页"""

    def __init__(self):
        super().__init__("BilibiliBrowser")
        self._seen_bvs: set[str] = set()
        self._browser = None
        self._context = None
        self._page = None
        self._external_page = False
    def set_page(self, page):
        """注入外部 Playwright page（共享浏览器实例）"""
        self._page = page
        self._external_page = True

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

    def _fetch_video_subtitles(self, bvid: str) -> str:
        """通过 B站 API 获取视频字幕/CC文字内容（page.goto 避免 fetch 风控）"""
        import json as _json
        try:
            # 1. 获取视频 cid
            page = self._page
            page.goto(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                      wait_until="domcontentloaded", timeout=8000)
            raw = page.evaluate("() => document.body.textContent")
            data = _json.loads(raw)
            if data.get("code") != 0 or not data.get("data"):
                return ""
            cid = data["data"].get("cid", 0)
            if not cid:
                return ""

            # 2. 获取字幕列表
            page.goto(f"https://api.bilibili.com/x/player/wbi/v2?bvid={bvid}&cid={cid}",
                      wait_until="domcontentloaded", timeout=8000)
            raw = page.evaluate("() => document.body.textContent")
            data = _json.loads(raw)
            subtitles = data.get("data", {}).get("subtitle", {}).get("subtitles", [])
            if not subtitles:
                return ""

            # 3. 优先选择中文（AI生成 > 人工上传 > 翻译）
            zh_sub = None
            for s in subtitles:
                lan = s.get("lan", "")
                if lan.startswith("ai-zh") or lan == "zh-Hans":
                    zh_sub = s; break
            if not zh_sub:
                for s in subtitles:
                    if s.get("lan", "").startswith("zh"):
                        zh_sub = s; break
            if not zh_sub and subtitles:
                zh_sub = subtitles[0]
            if not zh_sub or not zh_sub.get("subtitle_url"):
                return ""

            # 4. 下载字幕 JSON 并提取文字（CDN URL，page.evaluate fetch 可访问）
            sub_url = zh_sub["subtitle_url"]
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url

            text = page.evaluate(f"""
                async () => {{
                    try {{
                        const res = await fetch({_json.dumps(sub_url)});
                        const json = await res.json();
                        if (!json.body) return '';
                        return json.body
                            .map(b => b.content || '')
                            .filter(c => c.trim())
                            .join(' ');
                    }} catch(e) {{ return ''; }}
                }}
            """)
            return (text or "").strip()[:1500]

        except Exception:
            return ""

    def _fetch_from_space_api(self, mid: int, name: str, cat_hint: str) -> list[dict]:
        """访问 UP主 空间页面，从 DOM 提取视频列表（API 被 B站 风控封锁）

        策略：先试 /video 页面（标准），若跳转到 /upload 或无数据则回退主页。
        """
        results = []
        pages_to_try = [
            f"https://space.bilibili.com/{mid}/video",
            f"https://space.bilibili.com/{mid}",          # 主页兜底（部分账号 /video 不可用）
        ]

        for page_url in pages_to_try:
            try:
                self._page.goto(page_url, wait_until="domcontentloaded", timeout=15000)
                try:
                    self._page.wait_for_selector(
                        'a[href*="/video/BV"]', timeout=8000
                    )
                except Exception:
                    pass
                self._page.wait_for_timeout(1500)

                current_url = self._page.url
                # 如果被重定向到上传管理页，跳过
                if "/upload/" in current_url:
                    continue

                raw = self._page.evaluate(f"""(mid) => {{
                    const cards = [];
                    const seen = new Set();
                    // 匹配两种 BV 链接格式: /video/BVxxx 和 bilibili.com/video/BVxxx
                    const items = document.querySelectorAll(
                        'a[href*="/video/BV"], a[href*="bilibili.com/video/BV"]'
                    );
                    items.forEach(a => {{
                        const href = a.getAttribute('href') || '';
                        let bvid = (href.split('/video/')[1] || '').split('?')[0];
                        if (!bvid) return;
                        if (seen.has(bvid)) return;
                        seen.add(bvid);

                        const title = (a.getAttribute('title') || a.textContent || '').trim();
                        if (title === 'TA的视频' || !title) return;

                        let card = a.closest('[class*="card"], [class*="item"], [class*="video"]');
                        if (!card) card = a.parentElement;
                        const text = card ? card.textContent || '' : '';

                        let play = '';
                        const playMatch = text.match(/([\\d.]+万?)\\s*(播放|观看|次)/);
                        if (playMatch) play = playMatch[0];
                        else {{
                            const pm = text.match(/([\\d.]+万)/);
                            if (pm) play = pm[0];
                        }}

                        let duration = '';
                        const durMatch = text.match(/(\\d{{1,2}}:\\d{{2}}(:\\d{{2}})?)/);
                        if (durMatch) duration = durMatch[0];

                        // 提取发布时间（B站主页/视频页用 MM-DD 或 小时前/昨天）
                        let dateText = '';
                        const dm = text.match(/(\\d{{1,2}}-\\d{{1,2}})|(\\d+\\s*(?:小时前|分钟前|天前))|(?:昨天|前天)/);
                        if (dm) dateText = dm[0];

                        let pic = '';
                        const img = card ? card.querySelector('img') : null;
                        if (img) {{
                            pic = img.getAttribute('src') || img.getAttribute('data-src') || '';
                            if (pic && pic.startsWith('//')) pic = 'https:' + pic;
                        }}

                        cards.push({{
                            bvid: bvid, title: title.substring(0, 200),
                            play_text: play, duration: duration, date_text: dateText,
                            pic: pic, mid: mid
                        }});
                    }});
                    return cards;
                }}""", mid)

                if raw and isinstance(raw, list) and len(raw) > 0:
                    break  # 拿到数据了，不用再试下一个页面
            except Exception as e:
                console.log(f"[dim]  空间页面抓取 '{name}' 失败: {e}[/dim]")
                continue

        if not raw or not isinstance(raw, list):
            return []

        for v in raw:
            title = (v.get("title") or "").strip()
            bvid = v.get("bvid", "")
            if not title or not bvid:
                continue
            if bvid in self._seen_bvs:
                continue
            self._seen_bvs.add(bvid)

            url = f"https://www.bilibili.com/video/{bvid}"

            date_text = v.get("date_text", "")
            published_at = _parse_bilibili_date(date_text) if date_text else None

            summary_parts = [f"UP主: {name}"]
            if date_text:
                summary_parts.append(date_text)
            play_text = v.get("play_text", "")
            if play_text:
                summary_parts.append(play_text)

            duration = v.get("duration", "")

            results.append({
                "title": title,
                "url": url,
                "published_at": published_at,
                "summary": " | ".join(summary_parts),
                "raw": {
                    "bvid": bvid, "author": name, "mid": v.get("mid", mid),
                    "play_count": 0, "danmaku": "",
                    "duration": duration, "is_official": True,
                    "manufacturer": name, "pic": v.get("pic", ""),
                    "date_text": date_text,
                },
                "category_hint": cat_hint,
            })

        return results

    def _enrich_dates(self, items: list[dict], max_items: int = 100):
        """为无日期的 B站 条目批量补全发布时间（视频信息 API）

        间隔 2-3s 避免反爬，100条上限 ~4分钟。
        """
        import random as _random, time as _time, json as _json
        from datetime import datetime as _datetime, timezone as _tz

        undated = []
        for it in items:
            bvid = it.get("raw_data", {}).get("bvid", "")
            if bvid and not it.get("published_at"):
                undated.append(it)
        if not undated:
            return

        capped = undated[:max_items]
        console.log(f"[dim]  补全 {len(capped)} 条无日期 ({max_items}上限, ~{max_items*4:.0f}s)...[/dim]")
        enriched = 0
        for it in capped:
            bvid = it["raw_data"]["bvid"]
            try:
                self._page.goto(
                    f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                    wait_until="domcontentloaded", timeout=8000,
                )
                data = _json.loads(self._page.evaluate("() => document.body.textContent"))
                ts = data.get("data", {}).get("pubdate", 0)
                if ts and ts > 0:
                    it["published_at"] = _datetime.fromtimestamp(ts, tz=_tz.utc)
                    pub_str = it["published_at"].strftime("%Y-%m-%d")
                    it["summary"] = f"[{pub_str}] {it.get('summary', '')}"
                    enriched += 1
            except Exception:
                pass
            _time.sleep(_random.uniform(3, 5))
        if enriched:
            console.log(f"[green]  补全 {enriched}/{len(capped)} 条日期[/green]")

    def _apply_exclusions(self, items: list[dict]) -> list[dict]:
        """应用分类+全局排除词"""
        global_exclude = CATEGORY_EXCLUSIONS.get("_global", [])
        filtered = []
        excluded = 0
        for it in items:
            title = (it.get("title", "") + " " + it.get("summary", "")).lower()
            if any(kw in title for kw in global_exclude):
                excluded += 1
                continue
            cat = it.get("category_hint", "")
            if cat:
                cat_exclude = CATEGORY_EXCLUSIONS.get(cat, [])
                if any(kw in title for kw in cat_exclude):
                    excluded += 1
                    continue
            filtered.append(it)
        if excluded > 0:
            console.log(f"[dim]  排除 {excluded} 条(排除词匹配)[/dim]")
        return filtered

    def _search_keyword(self, keyword: str, cat_hint: str) -> list[dict]:
        """通过 B站搜索 API 搜索关键词，获取精确发布日期和简介"""
        api_url = (
            "https://api.bilibili.com/x/web-interface/search/type"
            f"?search_type=video&keyword={quote(keyword)}&page=1&order=pubdate"
        )

        try:
            raw = self._page.evaluate(
                f"""async () => {{
                    try {{
                        const r = await fetch({json.dumps(api_url)});
                        const j = await r.json();
                        if (j.code !== 0 || !j.data?.result) return [];
                        return j.data.result.slice(0, {MAX_SEARCH_PER_KEYWORD});
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
            raw_title = v.get("title", "")
            title = re.sub(r"<[^>]+>", "", raw_title).strip()
            bvid = v.get("bvid", "")
            if not title or not bvid:
                continue

            if bvid in self._seen_bvs:
                continue
            self._seen_bvs.add(bvid)

            url = f"https://www.bilibili.com/video/{bvid}"
            author = v.get("author", "")

            summary_parts = [f"UP主: {author}"]
            play_count = v.get("play", 0)
            if play_count > 0:
                summary_parts.append(
                    f"播放: {play_count/10000:.1f}万" if play_count >= 10000
                    else f"播放: {play_count}"
                )
            description = v.get("description", "").strip()
            if description and description not in ("-", "暂无简介"):
                summary_parts.append(description[:200])

            # 精确 Unix 时间戳
            published_at = None
            pubdate = v.get("pubdate", 0)
            if pubdate > 0:
                try:
                    published_at = datetime.fromtimestamp(pubdate, tz=timezone.utc)
                except Exception:
                    pass

            is_official = any(
                author == name or name in author
                for name in MANUFACTURER_ACCOUNTS
            )
            source_name = f"B站@{author}" if is_official else f"B站(via {keyword})"

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
                    "keyword": keyword,
                    "is_official": is_official,
                    "pic": v.get("pic", ""),
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
                    "pic": v.get("pic", ""),
                },
                "category_hint": cat_hint,
            })

            self._seen_bvs.add(bvid)

        return results

    def fetch(self) -> list[dict]:
        """采集 B站内容：关键词搜索 + 官号搜索 + 字幕提取"""
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

        # 如果有外部注入的 page，直接使用
        if self._page is not None:
            return self._do_fetch()

        # 否则创建独立浏览器
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

            result = self._do_fetch()
            browser.close()
            if not self._external_page:
                self._page = None
            return result

    def _do_fetch(self) -> list[dict]:
        """执行采集逻辑（需要 self._page 已设置）"""
        all_items = []

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
                            image_url=v.get("raw", {}).get("pic", ""),
                        )
                        item["category"] = v["category_hint"]
                        all_items.append(item)
                    if videos:
                        console.log(f"[dim]B站搜索 '{kw}': {len(videos)} 条[/dim]")
                except Exception as e:
                    console.log(f"[red]B站搜索 '{kw}' 失败: {e}[/red]")

                time.sleep(random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX))

        # ===== 阶段 2A：官号空间 API 直抓（按 UID 拉最新视频，最可靠）=====
        console.log("\n[yellow]  厂商官号空间 API (按UID拉取最新视频):[/yellow]")
        for acct_name, acct_info in MANUFACTURER_ACCOUNTS.items():
            mid = acct_info["mid"]
            cat_hint = acct_info["category"]
            try:
                videos = self._fetch_from_space_api(mid, acct_name, cat_hint)
                for v in videos:
                    raw_data = v.get("raw", {})
                    item = self.normalize_item(
                        title=v["title"],
                        url=v["url"],
                        source_name=f"B站官号@{raw_data.get('author', acct_name)}",
                        source_type="bilibili_manufacturer",
                        published_at=v.get("published_at"),
                        summary=v.get("summary", ""),
                        raw_data=raw_data,
                        image_url=raw_data.get("pic", ""),
                    )
                    item["category"] = v["category_hint"]
                    all_items.append(item)
                if videos:
                    console.log(
                        f"[dim]  {acct_name} (mid={mid}): {len(videos)} 条[/dim]"
                    )
            except Exception as e:
                console.log(f"[red]  官号空间API '{acct_name}' 失败: {e}[/red]")
            time.sleep(random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX))

        # ===== 阶段 2A+：资讯 UP主空间 API 直抓 =====
        if NEWS_UP_ACCOUNTS:
            console.log("\n[yellow]  资讯UP主空间 API (按UID拉取最新视频):[/yellow]")
            for up_name, up_info in NEWS_UP_ACCOUNTS.items():
                mid = up_info["mid"]
                cat_hint = up_info["category"]
                try:
                    videos = self._fetch_from_space_api(mid, up_name, cat_hint)
                    for v in videos:
                        raw_data = v.get("raw", {})
                        author = raw_data.get("author", up_name)
                        # 用 API 返回的真实UP主名替换占位名
                        display_name = author if author != up_name else up_name
                        item = self.normalize_item(
                            title=v["title"],
                            url=v["url"],
                            source_name=f"B站资讯UP@{display_name}",
                            source_type="bilibili_news_up",
                            published_at=v.get("published_at"),
                            summary=v.get("summary", ""),
                            raw_data=raw_data,
                            image_url=raw_data.get("pic", ""),
                        )
                        item["category"] = v["category_hint"]
                        all_items.append(item)
                    if videos:
                        console.log(
                            f"[dim]  UP主 {display_name} (mid={mid}): {len(videos)} 条[/dim]"
                        )
                except Exception as e:
                    console.log(f"[red]  UP主空间API '{up_name}' 失败: {e}[/red]")
                time.sleep(random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX))

        # ===== 阶段 2B：官号搜索（关键词 API + 简介） =====
        console.log("\n[yellow]  搜索厂商官号 (关键词补充):[/yellow]")
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
                        image_url=v.get("raw", {}).get("pic", ""),
                    )
                    item["category"] = v["category_hint"]
                    all_items.append(item)
                if videos:
                    console.log(f"[dim]B站官号 '{query}': {len(videos)} 条[/dim]")
            except Exception as e:
                console.log(f"[red]B站官号 '{query}' 失败: {e}[/red]")

            time.sleep(random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX))

        # ===== 阶段 3：视频内容提取（字幕 + 转录，高热度优先）=====
        # CI 环境默认跳过（page.evaluate fetch 不可靠）; 本地 ENRICH_VIDEO_CONTENT=true 开启
        enrich_enabled = os.getenv("ENRICH_VIDEO_CONTENT", "").lower() in ("1", "true", "yes")
        if not enrich_enabled:
            console.log(
                "[dim]  视频内容提取已跳过 (设置 ENRICH_VIDEO_CONTENT=true 开启)[/dim]"
            )
        # 批量补全日期（DOM 抓取的无日期条用 API 获取）
        self._enrich_dates(all_items)
        all_items = self._apply_exclusions(all_items)

        console.log(f"[green]B站浏览器总计: {len(all_items)} 条[/green]")
        return all_items

        from utils.video_content import extract_video_content, is_transcription_available

        subtitle_candidates = sorted(
            all_items,
            key=lambda it: it.get("raw_data", {}).get("play_count", 0),
            reverse=True,
        )[:30]
        has_transcription = is_transcription_available()
        console.log(
            f"\n[yellow]  视频内容提取: 前 {len(subtitle_candidates)} 条"
            f"{' (含转录)' if has_transcription else ' (仅字幕)'}[/yellow]"
        )
        enriched = 0
        transcribed = 0
        for item in subtitle_candidates:
            bvid = item.get("raw_data", {}).get("bvid", "")
            if not bvid:
                continue
            try:
                subtitle_text = self._fetch_video_subtitles(bvid)
                content_text = subtitle_text

                if not content_text or len(content_text) < 50:
                    if has_transcription:
                        content_text = extract_video_content(
                            bvid, self._page, item.get("title", "")
                        )
                        if content_text and len(content_text) > 30:
                            transcribed += 1

                if content_text and len(content_text) > 30:
                    item["raw_data"]["video_content"] = content_text
                    item["raw_data"]["content_source"] = (
                        "transcription" if (not subtitle_text or len(subtitle_text) < 50)
                        else "subtitle"
                    )
                    current_summary = item.get("summary", "")
                    item["summary"] = f"{current_summary} | 内容: {content_text[:300]}"
                    enriched += 1
                    console.log(
                        f"[dim]    {item['raw_data']['content_source']} "
                        f"{len(content_text)} 字: {item['title'][:40]}[/dim]"
                    )
            except Exception:
                pass
            time.sleep(random.uniform(0.5, 1.0))
        console.log(
            f"[green]  内容提取完成: {enriched}/{len(subtitle_candidates)} 条"
            f" (字幕: {enriched - transcribed}, 转录: {transcribed})[/green]"
        )

        console.log(f"[green]B站浏览器总计: {len(all_items)} 条[/green]")
        return all_items
