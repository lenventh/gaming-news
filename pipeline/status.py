"""管道状态追踪器 — 5 阶段工作流 + 实时进度写入。

阶段定义:
    1. ci_collect      — CI 数据采集监控
    2. local_collect   — 本地数据采集（浏览器等）
    3. review_generate — 手动审核 + 内容生成
    4. online_merge    — 线上+本地整合，生成正式版
    5. jianying_draft  — 剪映视频草稿（浏览器自动化）

用法:
    from pipeline.status import PipelineStatus
    status = PipelineStatus()
    status.start_stage("ci_collect")
    status.stage_sub("RSS + Reddit sources")
    status.stage_add_samples(["RTX 5090 发布...", "Steam Deck OLED..."])
    status.stage_progress(3, 5)
    status.done_stage("ci_collect")
"""

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from config import OUTPUT_DIR

STATUS_FILE = os.path.join(OUTPUT_DIR, ".pipeline_status.json")

# ====== 5 阶段定义 ======
STAGE_DEFS = {
    "ci_collect": {
        "key": "ci_collect",
        "label": "CI 数据采集",
        "emoji": "☁️",
        "estimated_seconds": 600,  # 10 min (CI 自动运行，实际时间取决于 GitHub Actions)
        "next": "local_collect",
    },
    "local_collect": {
        "key": "local_collect",
        "label": "本地数据采集",
        "emoji": "📡",
        "estimated_seconds": 480,  # 8 min (浏览器采集)
        "next": "review_generate",
    },
    "review_generate": {
        "key": "review_generate",
        "label": "审核 + 内容生成",
        "emoji": "✅",
        "estimated_seconds": 300,  # 5 min (手动审核 + LLM 生成)
        "next": "online_merge",
    },
    "online_merge": {
        "key": "online_merge",
        "label": "线上整合正式版",
        "emoji": "🔗",
        "estimated_seconds": 120,  # 2 min (拉取+合并)
        "next": "jianying_draft",
    },
    "jianying_draft": {
        "key": "jianying_draft",
        "label": "剪映视频草稿",
        "emoji": "🎬",
        "estimated_seconds": 600,  # 10 min (浏览器自动化：筛选→文稿→TTS→字幕→草稿)
        "next": None,
    },
}

STAGE_ORDER = ["ci_collect", "local_collect", "review_generate", "online_merge", "jianying_draft"]

# 旧阶段 → 新阶段映射（向后兼容）
OLD_TO_NEW_STAGE = {
    "init": None,
    "load_ci": "ci_collect",
    "rss_google": "ci_collect",
    "browsers": "local_collect",
    "collected": "local_collect",
    "processing": "review_generate",
    "generating": "review_generate",
    "done": None,  # 需要特殊处理：标记当前阶段完成
}


def _make_stage_data(key: str) -> dict:
    """创建单个阶段的初始数据"""
    info = STAGE_DEFS.get(key, {})
    return {
        "key": key,
        "label": info.get("label", key),
        "emoji": info.get("emoji", ""),
        "status": "pending",  # pending | running | done | error
        "estimated_seconds": info.get("estimated_seconds", 300),
        "elapsed_seconds": 0,
        "started_at": None,
        "done_at": None,
        "sub_stage": "",
        "progress": None,  # {"current": N, "total": M} or None
        "items_count": 0,
        "samples": [],
    }


class PipelineStatus:
    """5 阶段管道状态写入器（自动 tick 已用时间）。"""

    def __init__(self, week_label: str = ""):
        self._week_label = week_label
        self._per_week_file = ""
        if week_label:
            self._per_week_file = os.path.join(OUTPUT_DIR, f".pipeline_status_{week_label}.json")
        self._start_time = time.time()
        self._global_samples: list[str] = []
        self._sources: dict[str, int] = {}
        self._current_stage: str | None = None
        self._stage_start_time: float = 0.0

        # 初始化 5 个阶段
        self._stages: dict[str, dict] = {}
        for key in STAGE_ORDER:
            self._stages[key] = _make_stage_data(key)

        self._data = {
            "pipeline_started_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "updated_at": "",
            "current_stage": None,
            "overall_done": False,
            "error": None,
            "stages": self._stages,
            "samples": [],
            "sources": {},
        }
        self._lock = threading.Lock()
        self._tick_stop = False
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._tick_thread.start()
        self._flush()

    # ====== Tick Loop ======

    def _tick_loop(self):
        """每秒更新 elapsed_seconds 写入文件"""
        while not self._tick_stop:
            time.sleep(1)
            with self._lock:
                overall_elapsed = int(time.time() - self._start_time)
                self._data["updated_at"] = datetime.now().strftime("%H:%M:%S")

                # 更新当前阶段的 elapsed
                if self._current_stage and self._stages[self._current_stage]["status"] == "running":
                    stage_elapsed = int(time.time() - self._stage_start_time)
                    self._stages[self._current_stage]["elapsed_seconds"] = stage_elapsed

                # 更新旧字段（向后兼容）
                self._data["elapsed_seconds"] = overall_elapsed
                self._data["samples"] = self._global_samples[-8:]
                self._flush_nolock()

    def stop_tick(self):
        self._tick_stop = True

    # ====== New 5-Stage API ======

    def start_stage(self, key: str):
        """标记一个阶段开始运行。自动完成前一个阶段。"""
        if key not in self._stages:
            raise ValueError(f"Unknown stage: {key}")

        with self._lock:
            # 完成前一个阶段（如果有的话）
            if self._current_stage and self._current_stage != key:
                prev = self._stages[self._current_stage]
                if prev["status"] == "running":
                    prev["status"] = "done"
                    prev["done_at"] = datetime.now().strftime("%H:%M:%S")
                    prev["elapsed_seconds"] = int(time.time() - self._stage_start_time)

            self._current_stage = key
            self._stage_start_time = time.time()
            stage = self._stages[key]
            stage["status"] = "running"
            stage["started_at"] = datetime.now().strftime("%H:%M:%S")
            stage["elapsed_seconds"] = 0
            stage["sub_stage"] = ""
            stage["progress"] = None

            self._data["current_stage"] = key
            self._sync_legacy_fields()
            self._flush_nolock()

    def done_stage(self, key: str):
        """标记一个阶段完成"""
        if key not in self._stages:
            return
        with self._lock:
            stage = self._stages[key]
            stage["status"] = "done"
            stage["done_at"] = datetime.now().strftime("%H:%M:%S")
            stage["elapsed_seconds"] = int(time.time() - self._stage_start_time)
            stage["sub_stage"] = ""
            stage["progress"] = None
            if self._current_stage == key:
                self._current_stage = None
                self._data["current_stage"] = None
            self._sync_legacy_fields()
            self._flush_nolock()

    def stage_progress(self, key: str | None = None, current: int = 0, total: int = 0):
        """更新当前或指定阶段的子进度"""
        k = key or self._current_stage
        if not k or k not in self._stages:
            return
        with self._lock:
            self._stages[k]["progress"] = {"current": current, "total": total}
            self._flush_nolock()

    def stage_sub(self, text: str, key: str | None = None):
        """更新子阶段描述"""
        k = key or self._current_stage
        if not k or k not in self._stages:
            return
        with self._lock:
            self._stages[k]["sub_stage"] = text
            self._flush_nolock()

    def stage_add_samples(self, titles: list[str], key: str | None = None):
        """向阶段添加样本标题（ticker 展示用）"""
        k = key or self._current_stage
        if not k or k not in self._stages:
            return
        with self._lock:
            samples = self._stages[k]["samples"]
            for t in titles:
                if t:
                    samples.insert(0, t)
            self._stages[k]["samples"] = samples[-10:]
            # 也加到全局 samples（向后兼容）
            self._global_samples.extend(titles)
            self._flush_nolock()

    def stage_items(self, count: int, key: str | None = None):
        """更新阶段的条目计数"""
        k = key or self._current_stage
        if not k or k not in self._stages:
            return
        with self._lock:
            self._stages[k]["items_count"] = count
            self._flush_nolock()

    def stage_error(self, key: str, msg: str):
        """标记某个阶段出错"""
        if key not in self._stages:
            return
        with self._lock:
            self._stages[key]["status"] = "error"
            self._stages[key]["sub_stage"] = msg
            self._data["error"] = f"[{key}] {msg}"
            self._flush_nolock()

    # ====== Old API (向后兼容 main.py 现有调用) ======

    def update(self, stage: str, label: str, items_so_far: int = 0,
               sources: dict | None = None, progress: tuple | None = None):
        """旧版 update() — 自动映射到新的 5 阶段模型"""
        with self._lock:
            # 累加来源统计
            if sources:
                for k, v in sources.items():
                    self._sources[k] = self._sources.get(k, 0) + v
                self._data["sources"] = dict(self._sources)

            # 自动映射并启动对应阶段
            new_key = OLD_TO_NEW_STAGE.get(stage)
            if new_key and new_key != self._current_stage:
                # 需要切换阶段
                if self._current_stage:
                    self._stages[self._current_stage]["status"] = "done"
                    self._stages[self._current_stage]["done_at"] = datetime.now().strftime("%H:%M:%S")
                self._current_stage = new_key
                self._stage_start_time = time.time()
                self._stages[new_key]["status"] = "running"
                self._stages[new_key]["started_at"] = datetime.now().strftime("%H:%M:%S")
                self._data["current_stage"] = new_key

            # 更新当前阶段的子进度
            cur = self._current_stage
            if cur:
                if progress:
                    self._stages[cur]["progress"] = {"current": progress[0], "total": progress[1]}
                if items_so_far:
                    self._stages[cur]["items_count"] = items_so_far

            # 同步旧字段
            self._data["items_so_far"] = items_so_far
            self._data["stage"] = stage
            self._data["stage_label"] = label
            self._data["sub_stage"] = ""
            self._data["elapsed_seconds"] = int(time.time() - self._start_time)
            self._data["samples"] = self._global_samples[-8:]
            self._flush_nolock()

    def sub_stage(self, name: str):
        """旧版 sub_stage() — 更新当前阶段的子阶段"""
        with self._lock:
            if self._current_stage:
                self._stages[self._current_stage]["sub_stage"] = name
            self._data["sub_stage"] = name
            self._data["elapsed_seconds"] = int(time.time() - self._start_time)
            self._flush_nolock()

    def add_samples(self, titles: list[str]):
        """旧版 add_samples() — 添加到当前阶段的样本"""
        with self._lock:
            self._global_samples.extend(titles)
            self._data["samples"] = self._global_samples[-8:]
            if self._current_stage:
                samples = self._stages[self._current_stage]["samples"]
                for t in titles:
                    if t:
                        samples.insert(0, t)
                self._stages[self._current_stage]["samples"] = samples[-10:]
            self._flush_nolock()

    def error(self, msg: str):
        """旧版 error() — 标记当前阶段出错"""
        with self._lock:
            self._data["error"] = msg
            self._data["overall_done"] = True
            self._data["done"] = True
            if self._current_stage:
                self._stages[self._current_stage]["status"] = "error"
                self._stages[self._current_stage]["sub_stage"] = msg
            self._data["elapsed_seconds"] = int(time.time() - self._start_time)
            self._flush_nolock()

    def done(self):
        """旧版 done() — 标记所有阶段完成"""
        with self._lock:
            # 完成当前阶段
            if self._current_stage:
                self._stages[self._current_stage]["status"] = "done"
                self._stages[self._current_stage]["done_at"] = datetime.now().strftime("%H:%M:%S")
                self._stages[self._current_stage]["elapsed_seconds"] = int(time.time() - self._stage_start_time)
                self._current_stage = None

            self._data["stage"] = "done"
            self._data["stage_label"] = "完成"
            self._data["sub_stage"] = ""
            self._data["done"] = True
            self._data["overall_done"] = True
            self._data["current_stage"] = None
            self._data["elapsed_seconds"] = int(time.time() - self._start_time)
            self._flush_nolock()

    # ====== Helpers ======

    def _sync_legacy_fields(self):
        """将当前 5 阶段状态同步到旧版兼容字段"""
        cur = self._current_stage
        if cur and self._stages[cur]["status"] == "running":
            s = self._stages[cur]
            self._data["stage"] = cur
            self._data["stage_label"] = s["label"]
            self._data["sub_stage"] = s["sub_stage"]
            self._data["items_so_far"] = s["items_count"]
            self._data["done"] = False
            self._data["elapsed_seconds"] = int(time.time() - self._start_time)
        elif not cur and all(
            self._stages[k]["status"] == "done" for k in STAGE_ORDER
        ):
            self._data["done"] = True
            self._data["stage"] = "done"
            self._data["stage_label"] = "全部完成"

    def get_eta_seconds(self, key: str) -> int:
        """获取指定阶段的预估剩余秒数"""
        stage = self._stages.get(key)
        if not stage:
            return 0
        if stage["status"] == "done":
            return 0
        if stage["status"] == "pending":
            return STAGE_DEFS.get(key, {}).get("estimated_seconds", 300)
        # running: 预估 - 已用
        elapsed = stage.get("elapsed_seconds", 0)
        estimated = STAGE_DEFS.get(key, {}).get("estimated_seconds", 300)
        return max(0, estimated - elapsed)

    def get_next_stage(self) -> dict | None:
        """获取下一个待执行的阶段信息"""
        for key in STAGE_ORDER:
            stage = self._stages.get(key)
            if stage and stage["status"] in ("pending",):
                info = STAGE_DEFS.get(key, {})
                return {
                    "key": key,
                    "label": info.get("label", key),
                    "emoji": info.get("emoji", ""),
                    "estimated_seconds": info.get("estimated_seconds", 300),
                }
        return None

    def get_overall_progress(self) -> float:
        """获取整体进度 0.0 ~ 1.0"""
        completed = sum(
            1 for k in STAGE_ORDER
            if self._stages[k]["status"] == "done"
        )
        current = self._current_stage
        if current:
            stage = self._stages[current]
            progress = stage.get("progress")
            if progress and progress.get("total", 0) > 0:
                frac = progress["current"] / progress["total"]
            else:
                frac = 0.5  # 阶段运行中但没有子进度 → 算一半
            return (completed + frac) / len(STAGE_ORDER)
        return completed / len(STAGE_ORDER) if completed > 0 else 0.0

    def _flush(self):
        with self._lock:
            self._data["updated_at"] = datetime.now().strftime("%H:%M:%S")
            self._flush_nolock()

    def _flush_nolock(self):
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        try:
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception:
            pass
        if self._per_week_file:
            try:
                with open(self._per_week_file, "w", encoding="utf-8") as f:
                    f.write(payload)
            except Exception:
                pass


def clear_status():
    """清理状态文件"""
    if os.path.exists(STATUS_FILE):
        os.remove(STATUS_FILE)
