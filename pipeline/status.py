"""管道状态追踪器 — 实时写入进度供仪表盘读取。

用法:
    from pipeline.status import PipelineStatus
    status = PipelineStatus()
    status.update("collecting", "RSS 源采集", items_so_far=50, sources={"rss": 50})
    status.sub_stage("Reddit r/SteamDeck")
    status.add_samples(["RTX 5090 发布...", "Steam Deck OLED..."])
    status.done()
"""

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from config import OUTPUT_DIR

STATUS_FILE = os.path.join(OUTPUT_DIR, ".pipeline_status.json")


class PipelineStatus:
    """管道状态写入器（自动 tick 已用时间）。"""

    def __init__(self):
        self._start_time = time.time()
        self._samples: list[str] = []
        self._data = {
            "stage": "init",
            "stage_label": "初始化",
            "sub_stage": "",
            "progress": None,
            "items_so_far": 0,
            "elapsed_seconds": 0,
            "samples": [],
            "sources": {},
            "started_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "updated_at": "",
            "done": False,
            "error": None,
        }
        self._lock = threading.Lock()
        self._tick_stop = False
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._tick_thread.start()
        self._flush()

    def _tick_loop(self):
        """每秒更新 elapsed_seconds 写入文件"""
        while not self._tick_stop:
            time.sleep(1)
            with self._lock:
                self._data["elapsed_seconds"] = int(time.time() - self._start_time)
                self._data["updated_at"] = datetime.now().strftime("%H:%M:%S")
                self._flush_nolock()

    def stop_tick(self):
        self._tick_stop = True

    def update(self, stage: str, label: str, items_so_far: int = 0,
               sources: dict | None = None, progress: tuple | None = None):
        """更新当前阶段"""
        with self._lock:
            self._data["stage"] = stage
            self._data["stage_label"] = label
            self._data["sub_stage"] = ""  # 阶段切换时清除子阶段
            self._data["items_so_far"] = items_so_far
            self._data["elapsed_seconds"] = int(time.time() - self._start_time)
            self._data["samples"] = self._samples[-8:]
            if sources:
                merged = dict(self._data.get("sources", {}))
                for k, v in sources.items():
                    merged[k] = merged.get(k, 0) + v
                self._data["sources"] = merged
            if progress:
                self._data["progress"] = {"current": progress[0], "total": progress[1]}
            else:
                self._data["progress"] = None
            self._flush_nolock()

    def sub_stage(self, name: str):
        """更新子阶段名称"""
        with self._lock:
            self._data["sub_stage"] = name
            self._data["elapsed_seconds"] = int(time.time() - self._start_time)
            self._flush_nolock()

    def add_samples(self, titles: list[str]):
        """添加样本标题（滚动展示用）"""
        with self._lock:
            self._samples.extend(titles)
            self._data["samples"] = self._samples[-8:]
            self._flush_nolock()

    def error(self, msg: str):
        """记录错误"""
        with self._lock:
            self._data["error"] = msg
            self._data["done"] = True
            self._data["elapsed_seconds"] = int(time.time() - self._start_time)
            self._flush_nolock()

    def done(self):
        """标记管道完成"""
        with self._lock:
            self._data["stage"] = "done"
            self._data["stage_label"] = "完成"
            self._data["sub_stage"] = ""
            self._data["done"] = True
            self._data["elapsed_seconds"] = int(time.time() - self._start_time)
            self._flush_nolock()

    def _flush(self):
        with self._lock:
            self._data["updated_at"] = datetime.now().strftime("%H:%M:%S")
            self._flush_nolock()

    def _flush_nolock(self):
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        try:
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def clear_status():
    """清理状态文件"""
    if os.path.exists(STATUS_FILE):
        os.remove(STATUS_FILE)
