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
    # Anbernic 安卓线
    "rg556", "rg406v", "rg406h", "rg405v", "rg405m",
    "rg505", "rg552", "rg552",
    # AYN
    "odin", "odin lite", "odin 2", "odin 2 pro", "odin 2 max",
    "odin portal", "odin 2 portal", "lokiii", "thor",
    # Retroid
    "retroid pocket 5", "retroid pocket 4", "retroid pocket 4 pro",
    "retroid pocket 3", "retroid pocket 3+",
    "retroid pocket flip", "retroid pocket mini",
    "retroid pocket 6", "rp5", "rp4", "rp4p", "rp3", "rp3+", "rp mini", "rp flip",
    # AYANEO 安卓线
    "ayaneo pocket s", "ayaneo pocket dmg", "ayaneo pocket evo",
    "ayaneo pocket air", "ayaneo pocket micro", "ayaneo pocket max",
    # 高通/旗舰安卓
    "pimax portal", "abxylute one", "abxylute",
    "razer edge", "雷蛇 edge",
    "logitech g cloud", "罗技 g cloud",
    # 小品牌安卓
    "powkiddy x28",  # Android (T618)
    "gamemt ex8", "gamemt ex5", "gamemt e6 max",  # Android
    "kinhank k59", "kinhank k56", "kinhank k36",  # Android TV boxes
    "kt r2",  # Android handheld
}

# ============================================================
# Linux/开源掌机 — 运行 Linux CFW (ArkOS/AmberELEC/JELOS/MuOS/MinUI 等)
# ============================================================
LINUX_DEVICES = {
    # Anbernic Linux 线 (RG35XX/RG40XX/RG28XX/RG34XX 系列)
    "rg35xx", "rg35xx plus", "rg35xx h", "rg35xx sp",
    "rg40xx v", "rg40xx h", "rg28xx", "rg34xx",
    "rg353v", "rg353m", "rg353p", "rg351v", "rg351p", "rg351m",
    "rg350", "rg350m", "rg280v", "rg280m",
    "rg nano", "rg arc", "rg arc-d", "rg arc-s",
    "rg503", "rg300x", "rg300",
    # Miyoo
    "miyoo mini", "miyoo mini plus", "miyoo mini v2", "miyoo mini v4",
    "miyoo flip", "miyoo a30",
    # TrimUI
    "trimui smart", "trimui smart pro", "trimui brick",
    "trimui model s", "trimui a66",
    # PowKiddy
    "powkiddy x55", "powkiddy v10", "powkiddy x39",
    "powkiddy rgb30", "powkiddy rgb20sx", "powkiddy rgb20 pro",
    "powkiddy rgb10 max3", "powkiddy m17",
    # GKD / Game Kiddy
    "gkd pixel", "gkd pixel 2", "gkd mini", "gkd mini plus",
    "gkd 350h", "gkd 350h ultra",
    # MagicX
    "magicx xu10", "magicx zero 28", "magicx mini zero 28",
    "magicx touch 40", "magicx touch one",
    # 其他小众 Linux
    "r36s", "r35s", "r33s",
    "gcw zero", "funkey s", "q36 mini",
    "rgb10", "rgb10s", "odroid go advance", "odroid go super",
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
    "攻氪 konk", "konkr pocket",
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
