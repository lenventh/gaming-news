"""retrospecgame.com 设备列表抓取 — 中文搜索页，信息完整"""

import sys, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright

# 品牌OS映射规则
BRAND_OS = {
    "retroid": "android", "ayn": "android", "razer": "android",
    "logitech": "android", "abxylute": "android", "pimax": "android",
    "miyoo": "linux", "trimui": "linux", "powkiddy": "linux",
    "gkd": "linux", "game kiddy": "linux", "magicx": "linux",
    "mangmi": "linux", "minilong": "linux",
    "rog": "windows", "legion": "windows", "msi": "windows",
    "zotac": "windows", "acer": "windows", "gigabyte": "windows",
    "onexplayer": "windows", "aokzoe": "windows", "onexfly": "windows",
    "valve": "steam",
    "gamemt": "android", "kinhank": "android", "kt": "android",
    "anbernic": "mixed", "ayaneo": "mixed", "gpd": "windows",
    "konkr": "android",
}

# AYANEO Pocket线=安卓
ANDROID_AYANEO_WORDS = ["pocket", "pocket s", "pocket dmg", "pocket evo",
                         "pocket air", "pocket micro", "pocket max", "pocket play"]
# Anbernic Android线
ANDROID_ANBERNIC = ["rg556", "rg406", "rg405", "rg505", "rg552",
                    "rg353", "rg503", "rg arc-d", "rg arc-s"]


def classify(brand: str, model: str) -> str:
    bl = brand.lower().strip()
    ml = model.lower().strip()

    if bl in ("valve",):
        return "steam"
    if bl in ("ayaneo",):
        for kw in ANDROID_AYANEO_WORDS:
            if kw in ml:
                return "android"
        return "windows"
    if bl in ("anbernic",):
        for kw in ANDROID_ANBERNIC:
            if kw in ml:
                return "android"
        return "linux"
    return BRAND_OS.get(bl, "unknown")


def scrape():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})

        all_devices = set()

        # 按分类抓取
        for os_filter in ["android", "linux", "windows"]:
            page.goto(f"https://retrospecgame.com/zh/search?os={os_filter}",
                      wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)

            # 滚动加载更多
            for _ in range(20):
                prev = page.evaluate("() => document.querySelectorAll('a').length")
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(800)
                curr = page.evaluate("() => document.querySelectorAll('a').length")
                if curr == prev:
                    break

            # 提取设备名
            names = page.evaluate("""
                () => {
                    const seen = new Set();
                    document.querySelectorAll('a[href*="/zh/devices/"], [class*="card"] h2, [class*="card"] h3, [class*="title"]').forEach(el => {
                        const t = el.textContent.trim();
                        if (t && t.length > 3 && t.length < 120 && !t.startsWith('~'))
                            seen.add(t);
                    });
                    return [...seen];
                }
            """)
            print(f"  {os_filter}: {len(names)} devices", file=sys.stderr)
            for n in names:
                all_devices.add((n, os_filter))

        browser.close()

    # 解析品牌和型号
    parsed = []
    for name, site_os in sorted(all_devices):
        # 尝试提取品牌: "Retroid Pocket Nova" → brand=Retroid, model=Pocket Nova
        parts = name.split(None, 1)  # split by first whitespace
        if len(parts) == 2:
            brand, model = parts[0], parts[1]
        else:
            brand, model = name, ""
        inferred_os = classify(brand, model)
        parsed.append((brand, model, site_os, inferred_os))

    # 输出
    counts = {"android": [], "linux": [], "windows": [], "steam": [], "unknown": []}
    for brand, model, site_os, inferred_os in parsed:
        os_type = inferred_os if inferred_os != "unknown" else site_os
        name = f"{brand} {model}".strip()
        counts[os_type].append(name)

    print(f"# Total: {len(parsed)} devices")
    for label in ["android", "linux", "windows", "steam", "unknown"]:
        print(f"# {label}: {len(counts[label])}")

    # 输出 Python
    for label, varname in [
        ("android", "ANDROID_DEVICES"),
        ("linux", "LINUX_DEVICES"),
        ("windows", "WINDOWS_DEVICES"),
        ("steam", "STEAM_DECK_DEVICES"),
    ]:
        dl = sorted(set(counts[label]))
        if not dl:
            continue
        print(f"\n{varname} = {{")
        for name in dl:
            print(f'    "{name.lower()}",')
        print("}")

    if counts["unknown"]:
        print(f"\n# === UNKNOWN ===")
        for name in sorted(set(counts["unknown"])):
            print(f'    # "{name.lower()}",  # ???')


if __name__ == "__main__":
    scrape()
