"""采集器基类"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional


class BaseCollector(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def fetch(self) -> list[dict]:
        """采集新闻，返回标准化的 dict 列表"""

    def normalize_item(
        self,
        title: str,
        url: str,
        source_name: str,
        source_type: str,
        published_at: Optional[datetime] = None,
        summary: str = "",
        raw_data: Optional[dict] = None,
        material_links: Optional[list[str]] = None,
    ) -> dict:
        """将采集到的条目标准化为统一格式"""
        if published_at and published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

        return {
            "title": title.strip(),
            "url": url.strip(),
            "source_name": source_name,
            "source_type": source_type,
            "published_at": published_at.isoformat() if published_at else None,
            "summary": summary.strip(),
            "raw_data": raw_data or {},
            "material_links": material_links or [],
            "category": None,
        }
