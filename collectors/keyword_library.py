"""事件关键词库 — 正面/负面新闻关键词，按分类组织。

统一维护，供所有采集器导入使用。避免各采集器各自维护事件关键词导致不一致。

正面: 促销/优惠/销量/好评/限定版/联名/补货/获奖/新功能/固件更新
负面: 故障/品控/召回/维权/停产/诉讼/涨价/跑路/售后

使用方式:
    from .keyword_library import get_event_keywords, get_positive_keywords, \
        get_negative_keywords, with_site

    # 普通采集器（无需 site: 前缀）
    keywords = get_event_keywords("steam_deck")  # → ["Steam Deck 促销", ...]

    # site: 限定采集器
    keywords = with_site(get_event_keywords("steam_deck"), "bilibili.com")
    # → ["Steam Deck 促销 site:bilibili.com", ...]
"""

# ============================================================
# 通用事件关键词（不区分品类，可跨分类复用的词根）
# ============================================================

_COMMON_POSITIVE = [
    # 促销/价格
    "促销", "限时优惠", "折扣", "好价", "史低",
    # 销量/热度
    "销量", "热销", "爆款", "售罄", "卖爆",
    # 补货/现货
    "补货", "现货", "到货", "首发",
    # 评价
    "好评", "推荐", "神机", "真香", "值得买",
    # 限定/联名
    "限定版", "限定款", "联名", "特别版", "限定配色",
    # 获奖/评测
    "获奖", "年度最佳", "必买", "评测 推荐",
    # 更新/功能
    "新功能", "固件更新", "OTA 更新", "系统更新",
]

_COMMON_NEGATIVE = [
    # 质量/品控
    "故障", "品控", "翻车", "缺陷", "质量差",
    # 召回/维权
    "召回", "维权", "投诉", "退款", "差评",
    # 停产/下架
    "停产", "下架", "停售",
    # 法律/诉讼
    "诉讼", "起诉", "律师函",
    # 价格
    "涨价",
    # 其他
    "跑路", "暴雷", "售后差", "Bug", "死机", "卡顿", "发热",
]

# ============================================================
# 各分类专属事件关键词
# 原则: 品类名 + 通用事件词，拼音搜索词，分类特有场景
# ============================================================

EVENT_KEYWORDS = {
    "steam_deck": {
        "positive": [
            # 促销/价格
            "Steam Deck 促销", "Steam Deck 折扣", "Steam Deck 好价",
            "Steam Deck 限时优惠",
            # 销量/热度
            "Steam Deck 销量", "Steam Deck 热销", "Steam Deck 爆款",
            # 补货/现货
            "Steam Deck 补货", "Steam Deck 现货",
            # 限定/联名
            "Steam Deck 限定版", "Steam Deck 特别版",
            # 更新/功能
            "Steam Deck 新功能", "SteamOS 更新", "Steam Deck 固件",
            # 好评
            "Steam Deck 好评", "Steam Deck 推荐", "Steam Deck 真香",
            # 获奖
            "Steam Deck 获奖", "Steam Deck 最佳",
        ],
        "negative": [
            "Steam Deck 故障", "Steam Deck 品控", "Steam Deck 问题",
            "Steam Deck 翻车", "Steam Deck 召回", "Steam Deck 缺陷",
            "Steam Deck 维权", "Steam Deck 投诉", "Steam Deck 死机",
        ],
    },
    "windows_handheld": {
        "positive": [
            # 促销/价格
            "掌机 促销", "掌机 限时优惠", "掌机 折扣", "掌机 史低",
            "ROG Ally 促销", "AYANEO 促销", "掌机 好价",
            # 销量/热度
            "掌机 销量", "掌机 热销", "掌机 爆款", "掌机 卖爆",
            # 限定/联名
            "掌机 限定版", "掌机 联名", "掌机 特别版",
            # 补货/现货
            "掌机 补货", "掌机 现货", "掌机 到货", "掌机 首发",
            # 更新/功能
            "掌机 新功能", "掌机 固件更新", "掌机 OTA",
            # 好评
            "掌机 好评", "掌机 推荐", "掌机 真香",
            # 获奖
            "掌机 获奖",
        ],
        "negative": [
            "掌机 翻车", "掌机 故障", "掌机 品控", "掌机 缺陷",
            "掌机 维权", "掌机 召回", "掌机 投诉",
            "掌机 涨价", "掌机 售后差", "掌机 死机", "掌机 卡顿",
            "Win掌机 Bug",
        ],
    },
    "android_handheld": {
        "positive": [
            # 促销/价格
            "安卓掌机 促销", "安卓掌机 折扣", "安卓掌机 好价",
            "Retroid 促销", "Odin 促销",
            # 销量/热度
            "安卓掌机 销量", "安卓掌机 热销", "安卓掌机 爆款",
            # 限定/联名
            "安卓掌机 限定版", "安卓掌机 联名",
            # 补货/现货
            "安卓掌机 补货", "安卓掌机 现货", "安卓掌机 首发",
            # 更新/功能
            "安卓掌机 新功能", "安卓掌机 OTA",
            # 好评
            "安卓掌机 好评", "安卓掌机 推荐", "安卓掌机 真香",
        ],
        "negative": [
            "安卓掌机 故障", "安卓掌机 翻车", "安卓掌机 品控",
            "安卓掌机 维权", "安卓掌机 投诉",
            "安卓掌机 卡顿", "安卓掌机 发热",
            "奥丁 问题", "沙雕 翻车",
        ],
    },
    "linux_handheld": {
        "positive": [
            # 促销/价格
            "开源掌机 促销", "开源掌机 折扣", "开源掌机 好价",
            "寨机 好价",
            # 销量/热度
            "开源掌机 销量", "开源掌机 热销", "开源掌机 爆款",
            # 限定/联名
            "开源掌机 限定版", "开源掌机 联名",
            # 补货/现货
            "开源掌机 补货", "开源掌机 现货", "开源掌机 首发",
            # 更新/功能
            "开源掌机 固件", "开源掌机 新功能",
            # 好评
            "开源掌机 好评", "开源掌机 推荐", "开源掌机 真香", "寨机 神机",
        ],
        "negative": [
            "开源掌机 故障", "开源掌机 翻车", "开源掌机 品控",
            "寨机 翻车", "寨机 品控",
            "Anbernic 问题", "Miyoo 故障", "Miyoo 品控",
            "开源掌机 维权", "开源掌机 投诉",
        ],
    },
    "console": {
        "positive": [
            # 促销/价格
            "Switch 促销", "Switch 折扣", "PS5 促销", "PS5 折扣",
            "Xbox 促销", "Xbox 折扣", "主机 限时优惠",
            "Switch 好价", "PS5 好价",
            # 销量/热度
            "Switch 销量", "PS5 销量", "主机 销量", "Switch 热销",
            # 限定/联名
            "Switch 限定版", "Switch 联名", "PS5 限定版", "主机 限定版",
            # 补货/现货
            "Switch 补货", "PS5 补货", "PS5 现货",
            # 更新/功能
            "Switch 更新", "PS5 新功能", "主机 固件更新",
            # 好评
            "Switch 好评", "PS5 好评", "主机 推荐",
            # 获奖
            "主机 获奖", "年度最佳 主机",
        ],
        "negative": [
            "Switch 故障", "Switch 品控", "Switch 召回",
            "PS5 故障", "PS5 品控", "PS5 召回",
            "Xbox 故障", "Xbox 召回",
            "Joy-Con 漂移", "Switch 死机",
            "主机 翻车", "主机 维权", "主机 投诉",
            "Switch 停产", "PS5 涨价",
            "任天堂 诉讼", "索尼 召回",
        ],
    },
    "emulator": {
        "positive": [
            # 更新/功能
            "模拟器 更新", "模拟器 大更新", "模拟器 新版本",
            "模拟器 优化", "模拟器 性能提升",
            # 好评
            "模拟器 推荐", "模拟器 好用",
            # 兼容性
            "模拟器 兼容", "模拟器 完美运行",
        ],
        "negative": [
            "模拟器 下架", "模拟器 起诉", "模拟器 停止开发",
            "模拟器 跑路", "模拟器 收费", "模拟器 被封",
            "模拟器 律师函", "模拟器 诉讼",
        ],
    },
}


# ============================================================
# 辅助函数
# ============================================================

def get_positive_keywords(category_key: str) -> list[str]:
    """获取某分类的正面事件关键词"""
    info = EVENT_KEYWORDS.get(category_key, {})
    return info.get("positive", [])


def get_negative_keywords(category_key: str) -> list[str]:
    """获取某分类的负面事件关键词"""
    info = EVENT_KEYWORDS.get(category_key, {})
    return info.get("negative", [])


def get_event_keywords(category_key: str) -> list[str]:
    """获取某分类的全部事件关键词（正面 + 负面合并）"""
    return get_positive_keywords(category_key) + get_negative_keywords(category_key)


def with_site(keywords: list[str], site: str) -> list[str]:
    """给关键词列表加上 site: 限定，用于 site: 限定类采集器"""
    return [f"{kw} site:{site}" for kw in keywords]


def get_event_keywords_with_sites(category_key: str, sites: list[str]) -> list[str]:
    """获取某分类的事件关键词，加上 site: 前缀（每个 site 生成一组）"""
    keywords = get_event_keywords(category_key)
    result = []
    for kw in keywords:
        for site in sites:
            result.append(f"{kw} site:{site}")
    return result


def get_common_positive() -> list[str]:
    """获取通用正面事件词根"""
    return list(_COMMON_POSITIVE)


def get_common_negative() -> list[str]:
    """获取通用负面事件词根"""
    return list(_COMMON_NEGATIVE)
