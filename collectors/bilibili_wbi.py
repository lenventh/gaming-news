"""B站 Wbi 签名工具

B站 API 自 2023 年起要求 Wbi 签名（w_rid + wts 参数）。
US IP 没有签名会被返回空响应。
"""

import hashlib
import time
import requests

# Wbi 混排表（B站前端固定值）
MIXIN_TABLE = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

# 缓存密钥，避免每次签名都请求一次 nav
_wbi_key: str | None = None
_wbi_key_ts: float = 0
_WBI_KEY_TTL = 3600  # 1 小时


def _fetch_wbi_key() -> str:
    """从 B站 nav 接口获取 img_key + sub_key 并混排"""
    global _wbi_key, _wbi_key_ts

    now = time.time()
    if _wbi_key and (now - _wbi_key_ts) < _WBI_KEY_TTL:
        return _wbi_key

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com",
    }
    try:
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=headers,
            timeout=10,
        )
        data = resp.json()
        img_key = data["data"]["wbi_img"]["img_url"].split("/")[-1].split(".")[0]
        sub_key = data["data"]["wbi_img"]["sub_url"].split("/")[-1].split(".")[0]

        raw = img_key + sub_key
        _wbi_key = "".join(raw[i] for i in MIXIN_TABLE)[:32]
        _wbi_key_ts = now
        return _wbi_key

    except Exception:
        return ""


def sign_params(params: dict) -> dict:
    """给请求参数添加 Wbi 签名（wts + w_rid）"""
    key = _fetch_wbi_key()
    if not key:
        return params  # 获取 key 失败，不签名（部分接口可能仍可用）

    params = dict(params)
    params["wts"] = int(time.time())

    # 按 key 排序
    sorted_items = sorted(params.items(), key=lambda x: x[0])
    query = "&".join(f"{k}={v}" for k, v in sorted_items)

    w_rid = hashlib.md5((query + key).encode()).hexdigest()
    params["w_rid"] = w_rid

    return params
