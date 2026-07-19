"""泄漏信号数据库：存储预告→跟进闭环中的产品名和来源"""

import sqlite3
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

STORAGE_DIR = Path(__file__).parent
DB_PATH = STORAGE_DIR / "leak_signals.db"
MAX_SIGNAL_AGE_DAYS = 14


def _connect() -> sqlite3.Connection:
    """获取数据库连接，自动建表"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leak_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            source_title TEXT,
            source_url TEXT,
            first_seen_at TEXT NOT NULL,
            last_checked_at TEXT,
            followup_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_product
        ON leak_signals(product_name, status)
    """)
    conn.commit()
    return conn


def get_pending_signals(max_age_days: int = MAX_SIGNAL_AGE_DAYS) -> list[dict]:
    """获取仍有价值的 pending 信号（未超过最大天数）"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, product_name, source_title, first_seen_at, followup_count "
            "FROM leak_signals "
            "WHERE status = 'pending' AND first_seen_at >= ? "
            "ORDER BY first_seen_at DESC",
            (cutoff,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "product_name": r[1],
                "source_title": r[2],
                "first_seen_at": r[3],
                "followup_count": r[4],
            }
            for r in rows
        ]
    finally:
        conn.close()


def upsert_signals(signals: list[dict]) -> int:
    """插入或更新泄漏信号。每个 (product_name, status='pending') 去重。

    Returns:
        新增的信号数量
    """
    if not signals:
        return 0

    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    try:
        for s in signals:
            product_name = s.get("product_name", "").strip()
            if not product_name:
                continue
            # 检查是否已存在同一产品名的 pending 信号
            existing = conn.execute(
                "SELECT id FROM leak_signals WHERE product_name = ? AND status = 'pending'",
                (product_name,),
            ).fetchone()
            if existing:
                # 更新 last_checked_at
                conn.execute(
                    "UPDATE leak_signals SET last_checked_at = ? WHERE id = ?",
                    (now, existing[0]),
                )
            else:
                conn.execute(
                    "INSERT INTO leak_signals (product_name, source_title, source_url, "
                    "first_seen_at, last_checked_at, status) VALUES (?, ?, ?, ?, ?, 'pending')",
                    (
                        product_name,
                        s.get("source_title", "")[:200],
                        s.get("source_url", ""),
                        s.get("first_seen_at", now),
                        now,
                    ),
                )
                added += 1
        conn.commit()
        if added:
            print(f"[leak_db] 新增 {added} 个泄漏信号")
    finally:
        conn.close()
    return added


def mark_found(product_names: list[str]) -> int:
    """标记产品名对应的信号为 found（已找到跟进报道）"""
    if not product_names:
        return 0
    conn = _connect()
    count = 0
    try:
        for name in product_names:
            c = conn.execute(
                "UPDATE leak_signals SET status = 'found', "
                "followup_count = followup_count + 1 "
                "WHERE product_name = ? AND status = 'pending'",
                (name.strip(),),
            ).rowcount
            count += c
        conn.commit()
    finally:
        conn.close()
    return count


def expire_old(max_age_days: int = MAX_SIGNAL_AGE_DAYS) -> int:
    """过期旧信号"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    conn = _connect()
    count = 0
    try:
        count = conn.execute(
            "UPDATE leak_signals SET status = 'expired' "
            "WHERE status = 'pending' AND first_seen_at < ?",
            (cutoff,),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if count:
        print(f"[leak_db] 过期 {count} 个旧信号")
    return count


def get_stats() -> dict:
    """获取信号统计"""
    conn = _connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM leak_signals").fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM leak_signals WHERE status = 'pending'"
        ).fetchone()[0]
        found = conn.execute(
            "SELECT COUNT(*) FROM leak_signals WHERE status = 'found'"
        ).fetchone()[0]
        return {"total": total, "pending": pending, "found": found}
    finally:
        conn.close()
