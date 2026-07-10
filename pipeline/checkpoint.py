"""管道中间状态保存 — 采集/精选阶段 checkpoint

采集阶段 15+ 分钟，中途崩溃全部丢失。在关键节点保存中间状态到 JSON。
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


CHECKPOINT_DIR = Path(__file__).resolve().parent.parent / "output" / ".checkpoints"
RAW_FILE = CHECKPOINT_DIR / "raw_items.json"
SELECTED_FILE = CHECKPOINT_DIR / "selected_items.json"
META_FILE = CHECKPOINT_DIR / "meta.json"


def _ensure_dir():
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


def _write_meta(stage: str, count: int):
    _ensure_dir()
    meta = {
        "last_stage": stage,
        "last_count": count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


def _read_meta() -> dict | None:
    if not META_FILE.exists():
        return None
    try:
        with open(META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---- 原始采集 checkpoint ----

def save_raw_checkpoint(items: list[dict]):
    """保存 collect_all() 的原始结果"""
    _ensure_dir()
    serializable = []
    for it in items:
        serialized = dict(it)
        # 处理 datetime 等不可序列化字段
        if isinstance(serialized.get("published_at"), str):
            pass  # already string
        elif serialized.get("published_at"):
            serialized["published_at"] = str(serialized["published_at"])
        # 清理 raw_data 中的复杂对象
        if "raw_data" in serialized:
            rd = serialized["raw_data"]
            if isinstance(rd, dict):
                serialized["raw_data"] = {k: str(v) if not isinstance(v, (str, int, float, bool, list, dict, type(None))) else v for k, v in rd.items()}
        serializable.append(serialized)

    with open(RAW_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, default=str)

    _write_meta("raw", len(items))


def load_raw_checkpoint() -> list[dict] | None:
    """加载 collect_all() 的原始结果"""
    if not RAW_FILE.exists():
        return None
    try:
        with open(RAW_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
        # 恢复 published_at 为 datetime
        from datetime import datetime as dt
        for it in items:
            if it.get("published_at"):
                try:
                    it["published_at"] = dt.fromisoformat(it["published_at"])
                except (ValueError, TypeError):
                    pass
        return items
    except Exception:
        return None


# ---- 精选后 checkpoint ----

def save_selected_checkpoint(selected: dict[str, list[dict]]):
    """保存 process() 精选后的结果"""
    _ensure_dir()
    with open(SELECTED_FILE, "w", encoding="utf-8") as f:
        json.dump(selected, f, ensure_ascii=False, default=str)

    total = sum(len(v) for v in selected.values())
    _write_meta("selected", total)


def load_selected_checkpoint() -> dict[str, list[dict]] | None:
    """加载 process() 精选后的结果"""
    if not SELECTED_FILE.exists():
        return None
    try:
        with open(SELECTED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---- 清理 ----

def clear_checkpoints():
    """清理所有 checkpoint"""
    for f in (RAW_FILE, SELECTED_FILE, META_FILE):
        if f.exists():
            f.unlink()
