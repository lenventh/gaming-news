"""多平台游戏折扣抓取 — Steam/Epic/Switch/PS

数据源（均为公开 API，无需认证）:
- SteamDB Sales API — Steam 国区折扣
- Epic Store API — 限免+折扣
- DekuDeals RSS — Switch 多区折扣
- PS Deals — PlayStation Store 各服折扣

使用: python -m deals.fetcher  （独立测试）
     from deals.fetcher import fetch_all  （模块导入）
"""

import json
import os
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# ============================================================
# 热门游戏筛选 — Steam AppID 白名单（知名大作+口碑独立游戏）
# ============================================================
POPULAR_STEAM_IDS = {
    # 3A 大作
    1245620,   # 艾尔登法环
    1086940,   # 博德之门3
    1091500,   # 赛博朋克2077
    1174180,   # 荒野大镖客2
    730,       # CS2
    271590,    # GTA5
    292030,    # 巫师3
    252490,    # 腐蚀
    582010,    # 怪物猎人世界
    1446780,   # MHW Iceborne
    1286830,   # 死亡搁浅
    1888160,   # 怪物猎人 崛起
    1448440,   # 战神
    1899550,   # 最后生还者 Part I
    1366540,   # 对马岛之魂
    814380,    # 只狼
    374320,    # 黑暗之魂3
    1240460,   # 霍格沃茨之遗
    2050650,   # 星空
    2348590,   # 黑神话悟空
    2555180,   # 黑神话悟空 (alternate)
    # 热门独立/国产
    553850,    # 星露谷物语
    105600,    # 泰拉瑞亚
    261550,    # 骑马与砍杀2
    632360,    # Risk of Rain 2
    646910,    # 极乐迪斯科
    1940340,   # 潜水员戴夫
    1693980,   # 咩咩启示录
    1740720,   # 火炬城
    1151640,   # 暗影火炬城
    1672870,   # 暖雪
    1817230,   # 完蛋！我被美女包围了
    1782120,   # 仙剑奇侠传7
    1548850,   # 永劫无间
    1621690,   # 太吾绘卷
    108600,    # Project Zomboid
    1868140,   # Dave The Diver
    1282100,   # Gunfire Reborn
}

# Steam 国区 AppID
STEAM_CC = "cn"

# ============================================================
# Steam
# ============================================================

def fetch_steam_deals() -> list[dict]:
    """从 SteamDB Sales API 获取国区折扣，筛选热门游戏"""
    try:
        resp = requests.get(
            "https://steamdb.info/api/GetCurrentSaleApps/",
            headers={"User-Agent": "Mozilla/5.0 (compatible; GameDealsBot/1.0)"},
            timeout=15,
        )
        resp.raise_for_status()
        # SteamDB 返回 HTML，需要解析 JSON
        html = resp.text
        match = re.search(r"var\s+apps\s*=\s*({.*?});", html, re.DOTALL)
        if not match:
            return []
        data = json.loads(match.group(1))
    except Exception:
        return []

    deals = []
    for appid_str, info in data.items():
        appid = int(appid_str)
        if appid not in POPULAR_STEAM_IDS:
            continue
        discount = info.get("discount_percent", 0)
        if discount < 20:  # 至少 20% off 才列
            continue
        original = info.get("price_original", 0) / 100
        current = info.get("price_discount", 0) / 100
        if original <= 0 or current <= 0:
            continue
        name = info.get("name", f"App {appid}")
        until_ts = info.get("discount_end", 0)
        until = datetime.fromtimestamp(until_ts).strftime("%m/%d") if until_ts else "?"

        deals.append({
            "game": name,
            "platform": "Steam国区",
            "discount": f"-{discount}%",
            "original": f"¥{original:.0f}",
            "price": f"¥{current:.0f}",
            "until": until,
            "url": f"https://store.steampowered.com/app/{appid}",
        })

    deals.sort(key=lambda d: int(d["discount"].strip("-%"))
               if d["discount"].strip("-%").isdigit() else 0, reverse=True)
    return deals[:15]


# ============================================================
# Epic
# ============================================================

def fetch_epic_freebies() -> list[dict]:
    """Epic Games Store 免费游戏"""
    try:
        resp = requests.get(
            "https://store-site-backend-static-ipv4.ak.epicgames.com/"
            "freeGamesPromotions?locale=zh-CN&country=CN&allowCountries=CN",
            timeout=15,
        )
        data = resp.json()
    except Exception:
        return []

    free_games = []
    games = data.get("data", {}).get("Catalog", {}).get("searchStore", {}).get("elements", [])
    for game in games:
        promotions = game.get("promotions")
        if not promotions:
            continue
        for promo in promotions.get("promotionalOffers", []) + promotions.get("upcomingPromotionalOffers", []):
            for offer in promo.get("promotionalOffers", []):
                if offer.get("discountSetting", {}).get("discountPercentage") == 0:
                    title = game.get("title", "?")
                    end_date = offer.get("endDate", "")
                    until = ""
                    if end_date:
                        try:
                            dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                            until = dt.strftime("%m/%d")
                        except Exception:
                            pass

                    url_slug = game.get("productSlug") or game.get("catalogNs", {}).get("mappings", [{}])[0].get("pageSlug", "")
                    free_games.append({
                        "game": title,
                        "platform": "Epic 限免",
                        "until": until,
                        "url": f"https://store.epicgames.com/p/{url_slug}" if url_slug else "",
                    })

    return free_games[:5]


# ============================================================
# DekuDeals (Switch 多区折扣)
# ============================================================

DEKU_REGIONS = {
    "Switch美服": "https://www.dekudeals.com/hottest?filter[discount]=30",
    "Switch港服": "https://www.dekudeals.com/hottest?filter[discount]=30&filter[store]=hk",
}


def _parse_dekudeals(html: str) -> list[dict]:
    """解析 DekuDeals 热门折扣页面"""
    soup = BeautifulSoup(html, "html.parser")
    deals = []
    for row in soup.select(".search-results .row")[:8]:
        title_el = row.select_one("h6 a, .cell-title a")
        if not title_el:
            continue
        title = title_el.text.strip()[:50]

        discount_el = row.select_one(".cell-price .badge, [class*='discount']")
        discount = discount_el.text.strip() if discount_el else "?"

        msrp_el = row.select_one(".cell-msrp, [class*='msrp']")
        msrp = msrp_el.text.strip() if msrp_el else ""

        price_el = row.select_one(".cell-price strong, [class*='price'] strong")
        price = price_el.text.strip() if price_el else ""

        # 过滤: 只保留 >30% 折扣
        try:
            pct = int(re.search(r"(\d+)", discount).group(1)) if discount else 0
        except Exception:
            pct = 0
        if pct < 30:
            continue

        deals.append({
            "game": title,
            "discount": f"-{pct}%",
            "original": msrp,
            "price": price,
        })
    return deals


def fetch_switch_deals() -> list[dict]:
    """Switch 多区折扣（仅美服/港服）"""
    all_deals = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; GameDealsBot/1.0)"}

    for label, url in DEKU_REGIONS.items():
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            deals = _parse_dekudeals(resp.text)
            for d in deals:
                d["platform"] = label
                d["until"] = "?"
                d["url"] = ""
            all_deals.extend(deals)
        except Exception:
            continue

    return all_deals[:10]


# ============================================================
# PS Deals (PlayStation Store)
# ============================================================

def fetch_ps_deals() -> list[dict]:
    """PS Store 港服折扣（PSPrices API）"""
    try:
        resp = requests.get(
            "https://psprices.com/region-hk/discounts/?platform=PS4&sort=relevance",
            headers={"User-Agent": "Mozilla/5.0 (compatible; GameDealsBot/1.0)"},
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return []

    deals = []
    for card in soup.select(".game-item, .discount-item")[:8]:
        title_el = card.select_one(".game-title, h3 a")
        if not title_el:
            continue
        title = title_el.text.strip()[:50]

        discount_el = card.select_one(".discount-badge, .badge")
        discount = discount_el.text.strip() if discount_el else "?"

        price_el = card.select_one(".price, .price-new")
        price = price_el.text.strip() if price_el else ""

        original_el = card.select_one(".price-old, .price-original")
        original = original_el.text.strip() if original_el else ""

        try:
            pct = int(re.search(r"(\d+)", discount).group(1)) if discount else 0
        except Exception:
            pct = 0
        if pct < 30:
            continue

        deals.append({
            "game": title,
            "platform": "PS港服",
            "discount": f"-{pct}%",
            "original": original,
            "price": price,
            "until": "?",
            "url": "",
        })

    return deals[:8]


# ============================================================
# PS Plus 会员免费游戏
# ============================================================

def fetch_psplus_monthly() -> list[dict]:
    """PS Plus Essential 当月免费游戏（PlayStation Blog）"""
    try:
        resp = requests.get(
            "https://www.playstation.com/en-us/ps-plus/whats-new/",
            headers={"User-Agent": "Mozilla/5.0 (compatible; GameDealsBot/1.0)"},
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return []

    games = []
    # 查找游戏名称区域
    for card in soup.select("[class*='game'], [class*='title'], .monthly-games li, .psw-list-item")[:6]:
        text = card.text.strip()
        if text and len(text) > 3 and len(text) < 100:
            # 过滤掉页眉页脚
            if any(skip in text.lower() for skip in ("monthly games", "playstation plus", "essential", "extra", "premium", "deluxe")):
                continue
            games.append({
                "game": text[:60],
                "platform": "PS Plus 月免",
                "until": "月底",
                "url": "https://www.playstation.com/ps-plus",
            })

    return games[:5]


# ============================================================
# 聚合入口
# ============================================================

def fetch_all() -> dict:
    """抓取所有平台折扣，返回 {'deals': [...], 'freebies': [...], 'errors': [...]}"""
    errors = []
    all_deals = []
    all_freebies = []

    # Steam
    try:
        steam = fetch_steam_deals()
        all_deals.extend(steam)
    except Exception as e:
        errors.append(f"Steam: {e}")

    # Epic
    try:
        epic = fetch_epic_freebies()
        all_freebies.extend(epic)
    except Exception as e:
        errors.append(f"Epic: {e}")

    # Switch
    try:
        switch = fetch_switch_deals()
        all_deals.extend(switch)
    except Exception as e:
        errors.append(f"Switch: {e}")

    # PS
    try:
        ps = fetch_ps_deals()
        all_deals.extend(ps)
    except Exception as e:
        errors.append(f"PS: {e}")

    # PS Plus 月免
    try:
        psplus = fetch_psplus_monthly()
        all_freebies.extend(psplus)
    except Exception as e:
        errors.append(f"PS Plus: {e}")

    return {"deals": all_deals, "freebies": all_freebies, "errors": errors}


def fetch_and_format(title: str = "本周游戏折扣") -> str:
    """一站式：抓取 + 格式化 → Markdown"""
    result = fetch_all()
    parts = []

    # 限免
    freebie_md = format_free_games(result["freebies"])
    if freebie_md:
        parts.append(freebie_md)

    # 折扣
    deals_md = format_deals_table(result["deals"], title=title)
    if deals_md:
        parts.append(deals_md)

    # 错误
    for err in result["errors"]:
        parts.append(f"> ⚠ {err}")

    return "\n\n".join(parts)


# 从 formatter 导入（让 deals.fetcher 可以直接用）
from .formatter import format_deals_table, format_free_games  # noqa: E402, F401


if __name__ == "__main__":
    print("正在抓取各平台折扣...")
    md = fetch_and_format()
    print(md)
