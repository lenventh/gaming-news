"""设备→操作系统映射表 — 解决品牌跨系统分类问题

规则：
- 匹配设备型号（大小写不敏感），如果设备在多个OS出现，按主要型号归类
- 设备品牌未列出型号的（如"RG 掌机"）→ 保持品牌默认分类，由 LLM/关键词兜底
- 新设备发现流程：标题中出现新设备名 → 手动加一行 → 三轮内覆盖

来源：Retro Game Corps / r/SBCGaming / Retro Handhelds / 社区知识库
"""

# ============================================================
# 安卓掌机 — 明确运行 Android 的设备（含 Play Store / 高通/天玑）
# ============================================================
ANDROID_DEVICES = {
    "abxylute",
    "abxylute one",
    "abxylute one pro",
    "anbernic rg406h",
    "anbernic rg406v",
    "anbernic rg556",
    "ayaneo pocket ace",
    "ayaneo pocket air",
    "ayaneo pocket air mini",
    "ayaneo pocket dmg",
    "ayaneo pocket ds",
    "ayaneo pocket evo",
    "ayaneo pocket max",
    "ayaneo pocket micro",
    "ayaneo pocket micro classic",
    "ayaneo pocket s",
    "ayaneo pocket s mini",
    "ayaneo pocket s2",
    "ayaneo pocket vert",
    "ayn odin 3",
    "ayn odin2 mini",
    "ayn odin2 portal",
    "ayn thor",
    "gamemt e5 modx",
    "gamemt e6 max",
    "gamemt ex5",
    "gamemt ex8",
    "gamemt psk5000",
    "kinhank k36",
    "kinhank k56",
    "kinhank k59",
    "konkr pocket fit", "konkr pocket advance", "konkr advance",
    "kt r2",
    "logitech g cloud",
    "lokiii",
    "odin",
    "odin 2",
    "odin 2 max",
    "odin 2 portal",
    "odin 2 pro",
    "odin lite",
    "odin portal",
    "pimax portal",
    "powkiddy x28",
    "razer edge",
    "retroid pocket 3",
    "retroid pocket 3+",
    "retroid pocket 4",
    "retroid pocket 4 pro",
    "retroid pocket 5",
    "retroid pocket 6",
    "retroid pocket classic",
    "retroid pocket flip",
    "retroid pocket flip 2",
    "retroid pocket g2",
    "retroid pocket mini",
    "retroid pocket mini v2",
    "retroid pocket nova",
    "rg vita",
    "rg vita pro",
    "rg405m",
    "rg405v",
    "rg406h",
    "rg406v",
    "rg505",
    "rg552",
    "rg556",
    "rp flip",
    "rp mini",
    "rp3",
    "rp3+",
    "rp4",
    "rp4p",
    "rp5",
    "thor",
    "xu20 v32",
    "罗技 g cloud",
    "雷蛇 edge",
}

# ============================================================
# Linux/开源掌机 — 运行 Linux CFW (ArkOS/AmberELEC/JELOS/MuOS/MinUI 等)
# ============================================================
LINUX_DEVICES = {
    "anbernic rg 477m",
    "anbernic rg 477v",
    "anbernic rg cube",
    "anbernic rg ds",
    "anbernic rg rotate",
    "anbernic rg slide",
    "anbernic rg476h",
    "anbernic rg557",
    "funkey s",
    "gcw zero",
    "gkd 350h",
    "gkd 350h ultra",
    "gkd mini",
    "gkd mini plus",
    "gkd pixel",
    "gkd pixel 2",
    "magicx mini zero 28",
    "magicx mini zero 28 v2",
    "magicx one35",
    "magicx touch 40",
    "magicx touch one",
    "magicx xu10",
    "magicx zero 28",
    "magicx zero 40",
    "mangmi air x",
    "mangmi air y",
    "芒米 air y",
    "mangmi pocket max",
    "miyoo a30",
    "miyoo flip",
    "miyoo mini",
    "miyoo mini plus",
    "miyoo mini v2",
    "miyoo mini v4",
    "odroid go advance",
    "odroid go super",
    "powkiddy m17",
    "powkiddy rgb10 max3",
    "powkiddy rgb20 pro",
    "powkiddy rgb20sx",
    "powkiddy rgb30",
    "powkiddy v10",
    "powkiddy x39",
    "powkiddy x55",
    "q36 mini",
    "r33s",
    "r35s",
    "r36s",
    "rg arc",
    "rg arc-d",
    "rg arc-s",
    "rg nano",
    "rg280m",
    "rg280v",
    "rg28xx",
    "rg300",
    "rg300x",
    "rg34xx",
    "rg350",
    "rg350m",
    "rg351m",
    "rg351p",
    "rg351v",
    "rg353m",
    "rg353p",
    "rg353v",
    "rg35xx",
    "rg35xx h",
    "rg35xx plus",
    "rg35xx sp",
    "rg40xx h",
    "rg40xx v",
    "rg503",
    "rgb10",
    "rgb10s",
    "trimui a66",
    "trimui brick",
    "trimui brick hammer pro u",
    "trimui model s",
    "trimui smart",
    "trimui smart pro",
}

# ============================================================
# Windows 掌机 (x86_64)
# ============================================================
WINDOWS_DEVICES = {
    "rog ally", "rog ally x", "rog ally 2",
    "legion go", "legion go s",
    "msi claw", "msi claw 7", "msi claw 8", "msi claw 8 ai+",
    "ayaneo 2", "ayaneo 3", "ayaneo kun", "ayaneo air", "ayaneo air plus",
    "ayaneo slide", "ayaneo flip", "ayaneo next", "ayaneo next lite",
    "gpd win 4", "gpd win 5", "gpd win mini", "gpd win max 2", "gpd win max 3",
    "gpd pocket 4", "gpd win 3",
    "onexplayer x1", "onexplayer x1 mini", "onexplayer 2",
    "aokzoe a1", "aokzoe a1 x", "aokzoe a2",
    "onexfly", "onexfly f1", "onexfly f1 pro",
    "索泰 zone", "zotac zone", "zotac zone 2",
    "攻氪 konk",
    "acer nitro handheld", "宏碁 nitropad",
    "gigabyte aorus handheld", "技嘉 aorus handheld",
    "联想 拯救者 go", "联想 legion go",
}

# ============================================================
# Steam Deck 系列
# ============================================================
STEAM_DECK_DEVICES = {
    "steam deck", "steam deck oled", "steam deck lcd",
    "steam deck 2", "steamdeck",
    "steam machine",
}

# ============================================================
# 综合查询
# ============================================================

DEVICE_CATEGORY_MAP: dict[str, str] = {}
for d in ANDROID_DEVICES:
    DEVICE_CATEGORY_MAP[d] = "android_handheld"
for d in LINUX_DEVICES:
    DEVICE_CATEGORY_MAP[d] = "linux_handheld"
for d in WINDOWS_DEVICES:
    DEVICE_CATEGORY_MAP[d] = "windows_handheld"
for d in STEAM_DECK_DEVICES:
    DEVICE_CATEGORY_MAP[d] = "steam_deck"


def match_device_category(title: str) -> str | None:
    """在标题中匹配设备名，返回应属于的分类；不匹配返回 None"""
    lower = title.lower()
    # 按长度降序匹配，优先长名（"miyoo mini plus" > "miyoo mini"）
    sorted_devices = sorted(DEVICE_CATEGORY_MAP.keys(), key=len, reverse=True)
    for device in sorted_devices:
        if device in lower:
            return DEVICE_CATEGORY_MAP[device]
    return None


def reclassify_items(items: list[dict]) -> tuple[int, dict[str, int]]:
    """用设备映射校正一批条目的分类

    返回: (修正数量, {旧分类→新分类 计数})
    """
    corrected = 0
    stats: dict[str, int] = {}
    for item in items:
        title = item.get("title", "")
        if not title:
            continue
        new_cat = match_device_category(title)
        if new_cat and new_cat != item.get("category"):
            old = item.get("category", "none")
            key = f"{old} → {new_cat}"
            stats[key] = stats.get(key, 0) + 1
            item["category"] = new_cat
            corrected += 1
    return corrected, stats
