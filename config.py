"""项目配置 — 新闻来源、分类映射、时间窗口等"""

import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# ========== 时间窗口 ==========
NEWS_WINDOW_DAYS = int(os.getenv("NEWS_WINDOW_DAYS", "7"))
NOW = datetime.now(timezone.utc)
CUTOFF_DATE = NOW - timedelta(days=NEWS_WINDOW_DAYS)

# ========== 路径 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DB_PATH = os.path.join(BASE_DIR, "storage", "gaming_news.db")

# ========== LLM 配置 ==========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ========== Reddit 配置 ==========
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "gaming-news-bot/1.0")

# ========== 七大内容板块 ==========
CATEGORIES = {
    "steam_deck": {
        "name": "Steam Deck",
        "keywords": [
            "steam deck", "steamdeck", "steamos", "proton", "valve", "steam deck oled",
            "steam deck 2", "steamdeck2", "steam 掌机", "v社掌机", "steam deck lcd",
            "steam deck 评测", "steam deck 开箱", "steam deck 配件",
        ],
    },
    "windows_handheld": {
        "name": "Windows 掌机",
        "keywords": [
            "rog ally", "ayaneo", "gpd win", "msi claw", "微星 claw",
            "legion go", "联想 Legion Go", "windows 掌机", "win掌机",
            "onexplayer", "aokzoe", "player one",
            "掌机", "handheld", "handheld gaming pc",
            "amd z1", "amd z2", "ryzen z1", "ryzen z2",
            "ayaneo 3", "ayaneo next", "gpd win 5", "gpd win mini",
            "飞行家", "壹号本", "aya neo", "奥克",
            "拯救者掌机", "rog掌机",
        ],
    },
    "android_handheld": {
        "name": "安卓掌机",
        "keywords": [
            "odin", "retroid pocket", "retroid", "ayaneo pocket", "安卓掌机",
            "android 掌机", "android handheld",
            "rp4", "rp5", "rg557", "rg cube", "ayn odin", "ayn thor",
            "沙雕", "rp mini", "pocket dmg", "pocket evo",
            "天玑", "骁龙掌机", "安卓游戏机",
        ],
    },
    "linux_handheld": {
        "name": "开源掌机/Linux掌机",
        "keywords": [
            "anbernic", "miyoo", "trimui", "powkiddy", "开源掌机",
            "linux 掌机", "arkos", "jelos", "batocera", "onionos",
            "garlicos", "minui", "knuli", "rg35xx", "rg40xx",
            "rg cube", "rg556", "rg406", "rg arc",
            "周哥", "霸王小子", "小霸王", "霸王",
            "miyoo mini", "miyoo flip", "吹雪",
            "rg34xx", "rg28xx", "rg35xxsp", "rg35xx h",
            "trimui brick", "trimui smart", "powkiddy x55",
            "rgb30", "rgb20sx", "m17", "xx40",
        ],
    },
    "console": {
        "name": "传统主机",
        "keywords": [
            "ps5", "playstation 5", "ps5 pro", "xbox series",
            "switch 2", "switch2", "nintendo switch", "任天堂",
            "sony playstation", "微软 xbox", "主机",
            "switch oled", "switch lite", "switch pro",
            "ps portal", "playstation portal", "xbox game pass",
            "索尼", "次世代主机", "游戏主机",
        ],
    },
    "handheld_rumors": {
        "name": "厂商掌机传闻",
        "keywords": [
            "sony 掌机", "xbox 掌机", "微软掌机", "索尼掌机",
            "playstation handheld", "xbox handheld",
            "掌机传闻", "掌机曝光", "掌机爆料", "掌机专利",
            "switch 2 传闻", "switch2 爆料", "new handheld rumor",
            "next-gen handheld", "rumor", "rumour", "leak", "leaked",
            "传闻", "爆料", "曝光", "专利", "即将发布", "即将推出",
            "任天堂新机", "索尼新掌机", "微软新主机",
            "switch2 规格", "switch2 价格", "switch2 发售",
            "ps6", "xbox 新主机", "下一代 switch",
        ],
    },
    "emulator": {
        "name": "模拟器资讯",
        "keywords": [
            "yuzu", "ryujinx", "cemu", "rpcs3", "pcsx2", "dolphin",
            "citra", "vita3k", "xenia", "xemu", "melonDS",
            "模拟器", "emulator", "aethersx2", "nethersx2",
            "sudachi", "suyu", "uzuy",
            "switch 模拟器", "ps4 模拟器", "ps3 模拟器",
            "shadps4", "血缘模拟器", "血源诅咒pc",
            "模拟器更新", "模拟器安卓", "模拟器pc",
            "rpcs3 更新", "cemu 2", "dolphin 更新",
        ],
    },
}

# ========== RSS 源配置 ==========
RSS_SOURCES = [
    # --- 中文源 ---
    {
        "name": "机核",
        "url": "https://www.gcores.com/rss",
        "category_hint": None,
    },
    {
        "name": "IT之家",
        "url": "https://www.ithome.com/rss",
        "category_hint": None,
        "filter_keywords": [
            "游戏", "掌机", "主机", "switch", "playstation", "xbox",
            "steam", "任天堂", "索尼", "微软", "显卡", "rtx", "gtx",
            "amd", "intel", "芯片", "cpu", "gpu", "ssd", "存储",
            "手柄", "电竞", "esports", "模拟器", "vr", "ar", "quest",
        ],
    },
    # --- 英文源 ---
    {
        "name": "The Verge - Gaming",
        "url": "https://www.theverge.com/rss/gaming/index.xml",
        "category_hint": None,
    },
    {
        "name": "Nintendo Life",
        "url": "https://www.nintendolife.com/feeds/latest",
        "category_hint": "console",
    },
    {
        "name": "Nintendo Everything",
        "url": "https://nintendoeverything.com/feed/",
        "category_hint": "console",
    },
    {
        "name": "Eurogamer",
        "url": "https://www.eurogamer.net/feed",
        "category_hint": None,
    },
    {
        "name": "PC Gamer - Hardware",
        "url": "https://www.pcgamer.com/feed/",
        "category_hint": "windows_handheld",
    },
    {
        "name": "Tom's Hardware",
        "url": "https://www.tomshardware.com/feeds/all",
        "category_hint": None,
    },
    {
        "name": "Retro Dodo (掌机)",
        "url": "https://retrododo.com/feed/",
        "category_hint": "linux_handheld",
    },
    # --- Reddit RSS (old.reddit.com, 每个请求间隔 1.5s 防限流) ---
    # 核心 6 个板块，按重要性排序
    {
        "name": "Reddit r/SteamDeck",
        "url": "https://old.reddit.com/r/SteamDeck/.rss",
        "category_hint": "steam_deck",
    },
    {
        "name": "Reddit r/SBCGaming",
        "url": "https://old.reddit.com/r/SBCGaming/.rss",
        "category_hint": "linux_handheld",
    },
    {
        "name": "Reddit r/ROGAlly",
        "url": "https://old.reddit.com/r/ROGAlly/.rss",
        "category_hint": "windows_handheld",
    },
    {
        "name": "Reddit r/NintendoSwitch2",
        "url": "https://old.reddit.com/r/NintendoSwitch2/.rss",
        "category_hint": "console",
    },
    {
        "name": "Reddit r/emulation",
        "url": "https://old.reddit.com/r/emulation/.rss",
        "category_hint": "emulator",
    },
    {
        "name": "Reddit r/GamingLeaksAndRumours",
        "url": "https://old.reddit.com/r/GamingLeaksAndRumours/.rss",
        "category_hint": "handheld_rumors",
    },
    # --- 中文游戏/科技媒体 ---
    {
        "name": "IGN中国",
        "url": "https://www.ign.com.cn/rss",
        "category_hint": None,
        "filter_keywords": [
            "掌机", "主机", "Switch", "PlayStation", "Xbox", "Steam Deck",
            "手柄", "硬件", "芯片", "显卡", "屏幕", "显示器",
            "模拟器", "配件", "键盘", "鼠标", "VR", "头显",
            "开箱", "评测", "发售", "发布", "泄露", "传闻",
        ],
    },
]

# ========== B站搜索关键词 ==========
BILIBILI_SEARCH_KEYWORDS = [
    "Steam Deck 掌机",
    "Switch 2 评测",
    "ROG Ally 掌机",
    "开源掌机 新品",
    "模拟器 更新",
    "PS5 Pro",
    "掌机 新品发布",
]

# ========== 新闻子类型 ==========
NEWS_SUB_TAGS = {
    "leak": {
        "name": "新机爆料",
        "icon": "🔮",
        "keywords": [
            "爆料", "曝光", "泄露", "泄漏", "传闻", "专利",
            "rumor", "rumour", "leak", "leaked", "leaks",
            "teaser", "teased", "render", "renders", "concept",
            "即将发布", "即将推出", "即将上市", "据传",
            "prototype", "原型", "谍照", "外观曝光",
            "规格曝光", "参数曝光", "跑分", "benchmark",
            "传闻称", "消息称", "或将于", "有望",
            "据透露", "据悉", "或采用",
        ],
    },
    "release": {
        "name": "新机发售",
        "icon": "🆕",
        "keywords": [
            "发售", "发布", "上市", "开售", "开卖", "预售",
            "预定", "预订", "到货", "现货", "开箱",
            "released", "launched", "launch", "launches",
            "available", "now shipping", "ships", "shipping",
            "pre-order", "preorder", "pre order",
            "正式发布", "正式开售", "官宣", "上架",
            "公布售价", "定价", "售价",
            "入手", "到手", "首批", "首批发货",
            "国行", "行货", "开订",
        ],
    },
}

# ========== 网页抓取源（备用） ==========
WEB_SOURCES = [
    {
        "name": "什么值得买-游戏设备",
        "url": "https://search.smzdm.com/?c=faxian&s=%D5%C6%BB%FA&v=b",
        "parser": "smzdm",
    },
    {
        "name": "知乎-掌机话题",
        "url": "https://www.zhihu.com/topic/19663636/hot",
        "parser": "zhihu",
    },
]
