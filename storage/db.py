"""SQLite 数据库操作"""

import sqlite3
import json
import os
from config import DB_PATH


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """初始化数据库表"""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    conn = get_connection()
    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def insert_news_item(item: dict) -> bool:
    """插入一条新闻，如果 content_hash 已存在则跳过。返回是否成功插入"""
    import hashlib

    content = f"{item.get('title','')}{item.get('url','')}"
    content_hash = hashlib.md5(content.encode()).hexdigest()

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO news_items
            (title, summary, url, source_name, source_type, published_at, category, raw_data, content_hash, material_links)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.get("title", ""),
                item.get("summary", ""),
                item.get("url", ""),
                item.get("source_name", ""),
                item.get("source_type", ""),
                item.get("published_at"),
                item.get("category"),
                json.dumps(item.get("raw_data", {}), ensure_ascii=False),
                content_hash,
                json.dumps(item.get("material_links", []), ensure_ascii=False),
            ),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def get_items_in_window(cutoff_date):
    """获取时间窗口内未分类的新闻"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM news_items
            WHERE published_at >= ? AND category IS NULL
            ORDER BY published_at DESC
            """,
            (cutoff_date.isoformat(),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_items_by_category(cutoff_date):
    """按分类获取时间窗口内的新闻"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM news_items
            WHERE published_at >= ?
            ORDER BY category, published_at DESC
            """,
            (cutoff_date.isoformat(),),
        ).fetchall()
        grouped = {}
        for r in rows:
            item = dict(r)
            cat = item.get("category", "unknown")
            grouped.setdefault(cat, []).append(item)
        return grouped
    finally:
        conn.close()


def update_category(item_id: int, category: str):
    """更新新闻分类"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE news_items SET category = ? WHERE id = ?",
            (category, item_id),
        )
        conn.commit()
    finally:
        conn.close()


def save_weekly_output(week_label: str, markdown_content: str, item_count: int, stats: dict):
    """保存每周输出"""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO weekly_outputs
            (week_label, markdown_content, item_count, stats)
            VALUES (?, ?, ?, ?)
            """,
            (week_label, markdown_content, item_count, json.dumps(stats, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def get_weekly_output(week_label: str):
    """获取历史输出"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM weekly_outputs WHERE week_label = ?",
            (week_label,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_stats():
    """获取数据库统计"""
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) as c FROM news_items").fetchone()["c"]
        categorized = conn.execute(
            "SELECT COUNT(*) as c FROM news_items WHERE category IS NOT NULL"
        ).fetchone()["c"]
        weeks = conn.execute(
            "SELECT COUNT(*) as c FROM weekly_outputs"
        ).fetchone()["c"]
        return {"total_items": total, "categorized": categorized, "weeks_output": weeks}
    finally:
        conn.close()
