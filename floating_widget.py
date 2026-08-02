#!/usr/bin/env python3
"""桌面悬浮仪表盘 — 5 阶段工作流监控 (风格同 Web Dashboard)。

用法:
    python floating_widget.py          # 启动悬浮窗
    python floating_widget.py --run    # 启动 + 自动运行管道
"""

import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
import socketserver
from http.server import HTTPServer, BaseHTTPRequestHandler

# Windows: prevent subprocess from creating console windows
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0

class ReusableHTTPServer(HTTPServer):
    # Do NOT set allow_reuse_address — it lets two widget processes
    # silently share port 8766, causing requests to randomly hit old
    # code and produce stale / inconsistent state.
    daemon_threads = True

from config import OUTPUT_DIR
from pipeline.status import STATUS_FILE, STAGE_ORDER, STAGE_DEFS

DEFAULT_PORT = 8766
CI_RAW_FILE = os.path.join(OUTPUT_DIR, ".ci_raw_items.json")

def _per_week_path(base_name: str) -> str:
    """Get per-week file path for the currently selected week. No fallback to global
    when a week is selected — avoids stale global data masking per-week state."""
    week = WidgetHandler.selected_week
    if week:
        return os.path.join(OUTPUT_DIR, f"{base_name}_{week}.json")
    return os.path.join(OUTPUT_DIR, f"{base_name}.json")

def _selection_file() -> str:
    return _per_week_path(".filtered_selection")

def _filtered_log_file() -> str:
    return _per_week_path(".filtered_items")

CI_POLL_INTERVAL_MS = 60000
NORMAL_POLL_INTERVAL_MS = 2000


# ====== Mini HTTP Server ======
class WidgetHandler(BaseHTTPRequestHandler):
    pipeline_proc = None
    video_proc = None
    selected_week = ""
    available_reports = []
    triggered_actions = {}  # {week_label: set of triggered stage keys}

    def log_message(self, f, *a):
        pass

    def do_GET(self):
        if self.path == "/":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:8765")
            self.end_headers()
        elif self.path == "/status":
            sw = WidgetHandler.selected_week or WidgetHandler._get_active_week_label()
            self._json(WidgetHandler._assemble_status(sw))
        elif self.path == "/schedule":
            self._serve_schedule()
        elif self.path == "/filtered":
            self._serve_filtered()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/run-pipeline":
            self._start_pipeline(); self._json({"ok": True})
        elif self.path == "/run-recover":
            self._run_recover()
        elif self.path == "/run-merge":
            self._run_merge()
        elif self.path == "/run-jianying":
            self._run_jianying()
        elif self.path == "/notify-done":
            self._handle_jianying_done()
        elif self.path == "/save-review":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            self._save_selection(data.get("titles", []))
            self._json({"ok": True})
        else:
            self.send_error(404)

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _serve_json(self, path, default):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            else:
                content = json.dumps(default, ensure_ascii=False)
            self._json(json.loads(content))
        except Exception:
            self.send_error(500)

    # ── Week state helpers ───────────────────────────────────────────

    _ci_cache = None
    _ci_cache_time = 0

    @classmethod
    def _get_ci_data(cls):
        """Return (exists, items_count, samples) with 60s cache.

        The CI raw file is ~700 KB.  Reading + parsing it on every poll
        cycle would cause /schedule requests to exceed the 3 s fetch
        timeout, which makes the UI silently keep stale state after a
        week switch.  Cache it for 60 s so the HTTP handler stays fast.
        """
        import time as _time
        now = _time.time()
        if cls._ci_cache is not None and now - cls._ci_cache_time < 60:
            return cls._ci_cache
        try:
            if os.path.exists(CI_RAW_FILE):
                with open(CI_RAW_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    cls._ci_cache = (
                        True,
                        len(data),
                        [it.get("title", "") for it in data[:30] if it.get("title")],
                    )
                else:
                    cls._ci_cache = (True, 0, [])
            else:
                cls._ci_cache = (False, 0, [])
        except Exception:
            cls._ci_cache = (False, 0, [])
        cls._ci_cache_time = now
        return cls._ci_cache

    @classmethod
    def _get_active_week_label(cls):
        """Return the calendar-based active week label (e.g. 2026-W31-上)."""
        from datetime import datetime, timezone, timedelta
        now_utc = datetime.now(timezone.utc)
        wd = now_utc.weekday()
        if wd <= 2:
            half, rw = "下", (now_utc - timedelta(days=3)).isocalendar()
        elif wd <= 4:
            half, rw = "上", now_utc.isocalendar()
        else:
            half, rw = "下", (now_utc - timedelta(days=3)).isocalendar()
        return f"{rw[0]}-W{rw[1]:02d}-{half}"

    @classmethod
    def _build_steps(cls, week_label):
        """Compute stage states for a given week label.

        Returns: {steps, ci_available, ci_items_count, ci_samples,
                  report_exists, per_week_stages}

        Single source of truth — both /status and /schedule use this.
        """
        report_path = os.path.join(OUTPUT_DIR, f"{week_label}.md")
        report_exists = os.path.isfile(report_path)

        # Per-week status file
        per_week_file = os.path.join(OUTPUT_DIR, f".pipeline_status_{week_label}.json")
        per_week_stages = {}
        if os.path.isfile(per_week_file):
            try:
                with open(per_week_file, "r", encoding="utf-8") as f:
                    pw = json.load(f)
                if pw.get("pipeline_started_at", ""):
                    per_week_stages = pw.get("stages", {})
            except Exception:
                pass

        # CI availability for this week
        ci_available = False
        ci_items_count = 0
        ci_samples = []
        if per_week_stages.get("ci_collect", {}).get("status") == "done":
            ci_available = True
        elif week_label == cls._get_active_week_label():
            ci_available = True

        ci_exists, ci_count, ci_samp = cls._get_ci_data()
        if ci_available and ci_exists:
            ci_items_count = ci_count
            ci_samples = ci_samp

        # ── Build steps ──
        steps = []

        if report_exists:
            # Category A: Report generated.
            # Core stages 0-2 = always done.
            # Post stages 3-4 = cascade: only the **first** non-done one
            #   is needs_you — the user acts on them sequentially.
            first_post_undone_found = False
            for i, key in enumerate(STAGE_ORDER):
                info = STAGE_DEFS.get(key, {})
                est = info.get("estimated_seconds", 300)
                ps = per_week_stages.get(key, {})

                if i < 3:
                    steps.append({
                        "key": key, "name": info.get("label", key),
                        "emoji": info.get("emoji", ""),
                        "done": True, "running": False, "needs_you": False,
                        "error": False, "estimated_seconds": est,
                        "elapsed_seconds": ps.get("elapsed_seconds", est),
                        "sub_stage": "", "items_count": ps.get("items_count", 0),
                        "samples": [],
                    })
                else:
                    is_done = (ps.get("status") == "done")
                    needs_you = (not is_done and not first_post_undone_found)
                    if needs_you:
                        first_post_undone_found = True
                    steps.append({
                        "key": key, "name": info.get("label", key),
                        "emoji": info.get("emoji", ""),
                        "done": is_done, "running": False,
                        "needs_you": needs_you, "error": False,
                        "estimated_seconds": est,
                        "elapsed_seconds": ps.get("elapsed_seconds", 0),
                        "sub_stage": "", "items_count": ps.get("items_count", 0),
                        "samples": [],
                    })

        elif ci_available and ci_exists:
            # Category B: CI data ready — local pipeline may or may not be running.
            # Check the global status file to see if a pipeline is active RIGHT NOW
            # for the active week.  (The pipeline writes to the global file, not
            # per-week, so we must consult it directly.)
            # Check if a pipeline is actively running (process alive, not stale file)
            pipeline_alive = (
                WidgetHandler.pipeline_proc is not None
                and WidgetHandler.pipeline_proc.poll() is None
            )
            global_stages = {}
            if pipeline_alive and os.path.exists(STATUS_FILE):
                try:
                    with open(STATUS_FILE, "r", encoding="utf-8") as f:
                        gs = json.load(f)
                    if (not gs.get("overall_done", False)
                            and gs.get("pipeline_started_at", "")):
                        global_stages = gs.get("stages", {})
                except Exception:
                    pass

            # If the process is alive but no status file written yet, treat
            # local_collect as "just started" so the UI doesn't jump back.
            just_started = pipeline_alive and not global_stages

            for i, key in enumerate(STAGE_ORDER):
                info = STAGE_DEFS.get(key, {})
                est = info.get("estimated_seconds", 300)
                gs_st = global_stages.get(key, {})
                gs_status = gs_st.get("status", "")

                if just_started:
                    # Process alive, no status yet → local_collect just started
                    if key == "ci_collect":
                        done, running, needs_you, ic, sub = True, False, False, ci_items_count, ""
                    elif key == "local_collect":
                        done, running, needs_you, ic, sub = False, True, False, 0, "启动中..."
                    else:
                        done, running, needs_you, ic, sub = False, False, False, 0, ""
                elif gs_status == "running":
                    done, running, needs_you, ic = False, True, False, gs_st.get("items_count", 0)
                    sub = gs_st.get("sub_stage", "")
                elif gs_status == "done":
                    done, running, needs_you, ic = True, False, False, gs_st.get("items_count", 0)
                    sub = ""
                elif key == "ci_collect":
                    done, running, needs_you, ic, sub = True, False, False, ci_items_count, ""
                elif key == "local_collect":
                    done, running, needs_you, ic, sub = False, False, True, 0, ""
                else:
                    done, running, needs_you, ic, sub = False, False, False, 0, ""

                steps.append({
                    "key": key, "name": info.get("label", key),
                    "emoji": info.get("emoji", ""),
                    "done": done, "running": running, "needs_you": needs_you,
                    "error": gs_status == "error",
                    "estimated_seconds": est,
                    "elapsed_seconds": gs_st.get("elapsed_seconds", 0),
                    "sub_stage": sub,
                    "items_count": ic,
                    "samples": gs_st.get("samples", [])[-5:] if gs_status == "running" else [],
                })

        else:
            # Category C: Nothing yet — all pending.
            for i, key in enumerate(STAGE_ORDER):
                info = STAGE_DEFS.get(key, {})
                est = info.get("estimated_seconds", 300)
                steps.append({
                    "key": key, "name": info.get("label", key),
                    "emoji": info.get("emoji", ""),
                    "done": False, "running": False, "needs_you": False,
                    "error": False, "estimated_seconds": est,
                    "elapsed_seconds": 0, "sub_stage": "",
                    "items_count": 0, "samples": [],
                })

        return {
            "steps": steps,
            "ci_available": ci_available,
            "ci_items_count": ci_items_count,
            "ci_samples": ci_samples,
            "report_exists": report_exists,
            "per_week_stages": per_week_stages,
        }

    # ── Data assembly (used by both HTTP handlers and widget poll) ───

    @classmethod
    def _read_review_state(cls, week_label):
        """Return (review_done, review_count) for *week_label*."""
        sel_file = os.path.join(OUTPUT_DIR, f".filtered_selection_{week_label}.json")
        if os.path.isfile(sel_file):
            try:
                with open(sel_file, "r", encoding="utf-8") as f:
                    sel = json.load(f)
                return True, sel.get("count", 0)
            except Exception:
                return False, 0
        # Sync from global if just written (review_filtered.py may have
        # saved to the global file before per-week was created).
        global_sel = os.path.join(OUTPUT_DIR, ".filtered_selection.json")
        if os.path.isfile(global_sel):
            try:
                if time.time() - os.path.getmtime(global_sel) < 120:
                    import shutil as _shutil
                    _shutil.copyfile(global_sel, sel_file)
                    with open(sel_file, "r", encoding="utf-8") as f:
                        sel = json.load(f)
                    return True, sel.get("count", 0)
            except Exception:
                pass
        return False, 0

    @classmethod
    def _assemble_schedule(cls, week_label):
        """Return the schedule dict for *week_label* (pure data, no I/O beyond
        what _build_steps already does)."""
        state = cls._build_steps(week_label)
        review_done, review_count = cls._read_review_state(week_label)
        show_ci_data = not state["report_exists"] and not state["per_week_stages"]
        return {
            "week_label": week_label,
            "ci_done": state["ci_available"],
            "ci_items_count": state["ci_items_count"] if show_ci_data else 0,
            "ci_samples": state["ci_samples"] if show_ci_data else [],
            "local_running": False,
            "local_done": False,
            "review_done": review_done,
            "review_count": review_count,
            "overall_done": all(s["done"] for s in state["steps"]),
            "report_exists": state["report_exists"],
            "steps": state["steps"],
        }

    @classmethod
    def _assemble_status(cls, week_label):
        """Return the status dict for *week_label* (pure data)."""
        state = cls._build_steps(week_label)

        # Elapsed time: per-week first, then global status (active pipeline),
        # then sum stage elapsed times.
        elapsed = 0
        pwf = os.path.join(OUTPUT_DIR, f".pipeline_status_{week_label}.json")
        if os.path.isfile(pwf):
            try:
                with open(pwf, "r", encoding="utf-8") as f:
                    pw = json.load(f)
                elapsed = pw.get("elapsed_seconds", 0) or 0
            except Exception:
                pass
        if not elapsed and os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, "r", encoding="utf-8") as f:
                    gs = json.load(f)
                elapsed = gs.get("elapsed_seconds", 0) or 0
            except Exception:
                pass
        if not elapsed:
            for s in state["steps"]:
                elapsed += s.get("elapsed_seconds", 0) or 0

        stages = {}
        for s in state["steps"]:
            key = s["key"]
            status = "done" if s["done"] else ("running" if s["running"] else "pending")
            stages[key] = {
                "key": key, "label": s["name"], "emoji": s["emoji"],
                "status": status,
                "elapsed_seconds": s["elapsed_seconds"],
                "items_count": s.get("items_count", 0),
                "sub_stage": s["sub_stage"], "samples": s["samples"],
                "estimated_seconds": s["estimated_seconds"],
            }

        return {
            "stage": "init", "stage_label": "idle",
            "elapsed_seconds": elapsed,
            "items_so_far": state["ci_items_count"],
            "samples": state["ci_samples"], "sources": {},
            "done": all(s["done"] for s in state["steps"]),
            "current_stage": None, "stages": stages,
            "overall_done": all(s["done"] for s in state["steps"]),
        }

    # ── HTTP route handlers ──────────────────────────────────────────

    def _serve_schedule(self):
        week_label = WidgetHandler.selected_week or WidgetHandler._get_active_week_label()

        # Side effect: detect new CI and reset local stage states on disk
        state = WidgetHandler._build_steps(week_label)
        if state["ci_available"] and not state["report_exists"] and os.path.exists(STATUS_FILE):
            try:
                ci_mtime = os.path.getmtime(CI_RAW_FILE)
                with open(STATUS_FILE, "r", encoding="utf-8") as f:
                    gs = json.load(f)
                pipe_start = gs.get("pipeline_started_at", "")
                if pipe_start and ci_mtime > 0:
                    from datetime import datetime as dt2
                    if dt2.fromtimestamp(ci_mtime) > dt2.strptime(pipe_start, "%Y-%m-%dT%H:%M:%S"):
                        self._reset_status_for_new_ci(week_label, state["ci_items_count"])
            except Exception:
                pass

        self._json(WidgetHandler._assemble_schedule(week_label))

    def _serve_filtered(self):
        try:
            flf = _filtered_log_file()
            if os.path.isfile(flf):
                with open(flf, "r", encoding="utf-8") as f:
                    log = json.load(f)
                items = []
                for run in log.get("runs", []):
                    for it in run.get("items", []):
                        it["_filtered_at"] = run.get("filtered_at", "")
                        it["_filter_name"] = run.get("filter_name", "")
                        items.append(it)
            else:
                items = []
            self._json(items)
        except Exception:
            self.send_error(500)

    def _start_pipeline(self):
        # Don't re-run if the selected week already has a report
        sw = WidgetHandler.selected_week
        if sw:
            report_path = os.path.join(OUTPUT_DIR, f"{sw}.md")
            if os.path.isfile(report_path):
                self._json({"ok": False, "msg": f"报告 {sw} 已存在，跳过管道运行"})
                return
        project_dir = os.path.dirname(os.path.abspath(__file__))
        self.__class__.pipeline_proc = subprocess.Popen(
            [sys.executable, "main.py", "--from-ci"],
            cwd=project_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW)

    def _save_selection(self, titles):
        from pathlib import Path
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        sel_f = _selection_file()
        with open(sel_f, "w", encoding="utf-8") as f:
            json.dump({"titles": titles, "count": len(titles)}, f, ensure_ascii=False, indent=2)

    def _run_recover(self):
        try:
            result = subprocess.run(
                [sys.executable, "main.py", "--recover-reviewed"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                capture_output=True, text=True, timeout=600,
                creationflags=_NO_WINDOW)
            self._json({"ok": result.returncode == 0})
        except Exception:
            self.send_error(500)

    def _run_merge(self):
        week = WidgetHandler.selected_week
        WidgetHandler.triggered_actions.setdefault(week, set()).add("online_merge")
        project_dir = os.path.dirname(os.path.abspath(__file__))
        out = os.path.join(project_dir, "output")
        target = (week + ".md") if week else ""
        target_path = os.path.join(out, target) if target else ""
        if target_path and os.path.exists(target_path):
            os.startfile(target_path)
            self._json({"ok": True, "file": target})
        else:
            reports = sorted(
                [f for f in os.listdir(out) if f.endswith(".md") and f.startswith("20")],
                reverse=True,
            )
            if reports:
                os.startfile(os.path.join(out, reports[0]))
                self._json({"ok": True, "file": reports[0]})
            else:
                self._json({"ok": False, "msg": "No report found"})
        self._update_stage_status("online_merge", "done")

    def _run_jianying(self):
        week = WidgetHandler.selected_week
        WidgetHandler.triggered_actions.setdefault(week, set()).add("jianying_draft")
        project_dir = os.path.dirname(os.path.abspath(__file__))
        target = (week + ".md") if week else ""
        target_path = os.path.join(project_dir, "output", target)
        args = [sys.executable, "video_workflow.py"]
        if os.path.exists(target_path):
            args.append(target_path)
        # Pass widget port so video_workflow can notify when draft is done
        env = os.environ.copy()
        env["WIDGET_NOTIFY_URL"] = f"http://127.0.0.1:{DEFAULT_PORT}/notify-done"
        self.__class__.video_proc = subprocess.Popen(
            args, cwd=project_dir, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        # Mark as running (not done!) — will be updated when notified or process exits
        self._update_stage_status("jianying_draft", "running")
        # Open browser to video workflow Step 1
        webbrowser.open("http://127.0.0.1:5050")
        self._json({"ok": True, "msg": "视频工作流已启动 → http://127.0.0.1:5050"})

    def _handle_jianying_done(self):
        """Receive notification from video_workflow that draft generation completed."""
        self._update_stage_status("jianying_draft", "done")
        self._json({"ok": True})

    def _update_stage_status(self, key: str, status_val: str):
        try:
            sw = WidgetHandler.selected_week
            targets = [STATUS_FILE]
            pwf = ""
            if sw:
                pwf = os.path.join(OUTPUT_DIR, f".pipeline_status_{sw}.json")
                if os.path.isfile(pwf):
                    targets.append(pwf)

            for target in targets:
                if os.path.exists(target):
                    with open(target, "r", encoding="utf-8") as f:
                        st = json.load(f)
                else:
                    st = {}
                stages = st.setdefault("stages", {})
                stage = stages.setdefault(key, {})
                stage["status"] = status_val
                st["updated_at"] = time.strftime("%H:%M:%S")
                if status_val == "done":
                    stage["done_at"] = time.strftime("%H:%M:%S")
                if all(stages.get(k, {}).get("status") == "done" for k in STAGE_ORDER):
                    st["overall_done"] = True
                with open(target, "w", encoding="utf-8") as f:
                    json.dump(st, f, ensure_ascii=False, indent=2)

            # Create per-week file from report_exists template if missing but report exists
            if sw and pwf and not os.path.isfile(pwf):
                report_path = os.path.join(OUTPUT_DIR, f"{sw}.md")
                if os.path.isfile(report_path):
                    st = {
                        "pipeline_started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "updated_at": time.strftime("%H:%M:%S"),
                        "current_stage": None,
                        "overall_done": False,
                        "error": None,
                        "stages": {},
                    }
                    for k in STAGE_ORDER:
                        info = STAGE_DEFS.get(k, {})
                        idx = STAGE_ORDER.index(k)
                        is_core = idx < 3
                        st["stages"][k] = {
                            "key": k, "label": info.get("label", k), "emoji": info.get("emoji", ""),
                            "status": "done" if is_core else ("done" if k == key and status_val == "done" else "pending"),
                            "estimated_seconds": info.get("estimated_seconds", 300),
                            "elapsed_seconds": info.get("estimated_seconds", 300) if is_core else 0,
                            "started_at": time.strftime("%H:%M:%S") if is_core else None,
                            "done_at": time.strftime("%H:%M:%S") if is_core else None,
                            "sub_stage": "", "progress": None, "items_count": 0, "samples": [],
                        }
                    # Apply the specific status update
                    st["stages"][key]["status"] = status_val
                    if status_val == "done":
                        st["stages"][key]["done_at"] = time.strftime("%H:%M:%S")
                    if all(st["stages"][k]["status"] == "done" for k in STAGE_ORDER):
                        st["overall_done"] = True
                    with open(pwf, "w", encoding="utf-8") as f:
                        json.dump(st, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @classmethod
    def _reset_status_for_new_ci(cls, week_label: str, ci_count: int):
        """Persist CI-reset state to disk so new CI detection survives poll cycles."""
        report_path = os.path.join(OUTPUT_DIR, f"{week_label}.md")
        if os.path.isfile(report_path):
            return  # Report already exists, don't overwrite state
        try:
            now_ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            now_hm = time.strftime("%H:%M:%S")
            # Build fresh state: CI done, everything else pending
            st = {
                "pipeline_started_at": now_ts,
                "updated_at": now_hm,
                "current_stage": None,
                "overall_done": False,
                "error": None,
                "stages": {},
            }
            for key in STAGE_ORDER:
                info = STAGE_DEFS.get(key, {})
                st["stages"][key] = {
                    "key": key, "label": info.get("label", key),
                    "emoji": info.get("emoji", ""),
                    "status": "done" if key == "ci_collect" else "pending",
                    "estimated_seconds": info.get("estimated_seconds", 300),
                    "elapsed_seconds": 0,
                    "started_at": now_hm if key == "ci_collect" else None,
                    "done_at": now_hm if key == "ci_collect" else None,
                    "sub_stage": "", "progress": None,
                    "items_count": ci_count if key == "ci_collect" else 0,
                    "samples": [],
                }

            # Write global status file
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False, indent=2)

            # Write/overwrite per-week status file
            pwf = os.path.join(OUTPUT_DIR, f".pipeline_status_{week_label}.json")
            with open(pwf, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def start_server(port=DEFAULT_PORT):
    server = ReusableHTTPServer(("127.0.0.1", port), WidgetHandler)
    socketserver.ThreadingMixIn.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ====== Dashboard-Style Floating Widget ======

class FloatingWidget:
    """桌面悬浮仪表盘 (风格同 Web Dashboard)"""

    WIDTH = 380
    HEIGHT = 520

    def __init__(self, run_pipeline=False):
        self.root = tk.Tk()
        self.root.title("Gaming News")
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}+{sw - self.WIDTH - 16}+24")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.92)

        # Colors — matching web dashboard palette
        self.BG = "#0d1117"
        self.CARD_BG = "#161b22"
        self.BORDER = "#21262d"
        self.FG = "#c9d1d9"
        self.DIM = "#8b949e"
        self.ACCENT = "#58a6ff"
        self.GREEN = "#238636"
        self.YELLOW = "#d2991d"
        self.RED = "#da3633"

        self.root.configure(bg=self.BG)
        self.root.bind("<Button-1>", self._start_drag)
        self.root.bind("<B1-Motion>", self._drag)
        self._drag_x = self._drag_y = 0

        P = 10  # padding

        # ====== TOP BAR ======
        top = tk.Frame(self.root, bg="#161b22", height=32)
        top.pack(fill=tk.X)
        top.pack_propagate(False)
        self.status_dot = tk.Label(top, text="●", font=("", 9), fg=self.DIM, bg="#161b22")
        self.status_dot.pack(side=tk.LEFT, padx=(P, 2), pady=6)
        self.title_label = tk.Label(top, text="游戏设备周刊", font=("Microsoft YaHei", 11, "bold"),
                                     fg="#f0f6fc", bg="#161b22")
        self.title_label.pack(side=tk.LEFT, pady=5)
        self.week_label = tk.Label(top, text="", font=("Microsoft YaHei", 8),
                                    fg=self.ACCENT, bg="#1f2a3a", cursor="hand2",
                                    padx=6, pady=1)
        self.week_label.pack(side=tk.RIGHT, padx=P, pady=4)
        self.week_label.bind("<Button-1>", self._cycle_week)

        # ====== MAIN CONTENT ======
        main = tk.Frame(self.root, bg=self.BG)
        main.pack(fill=tk.BOTH, expand=True, padx=P, pady=(6, 0))

        # -- Progress bar --
        self.progress_canvas = tk.Canvas(main, height=4,
                                          bg="#21262d", highlightthickness=0, bd=0)
        self.progress_canvas.pack(fill=tk.X, pady=(0, 2))
        self.progress_bar = self.progress_canvas.create_rectangle(0, 0, 0, 4, fill=self.ACCENT, width=0)

        # -- Step labels --
        step_frame = tk.Frame(main, bg=self.BG)
        step_frame.pack(fill=tk.X, pady=(0, 8))
        self.step_labels = []
        for key in STAGE_ORDER:
            info = STAGE_DEFS.get(key, {})
            lbl = tk.Label(step_frame, text=info.get("emoji", ""), font=("Segoe UI", 8),
                          fg="#484f58", bg=self.BG)
            lbl.pack(side=tk.LEFT, expand=True)
            self.step_labels.append(lbl)

        # -- Cards row --
        cards_frame = tk.Frame(main, bg=self.BG)
        cards_frame.pack(fill=tk.X, pady=(0, 8))

        def _make_card(parent, val_text, label_text, w):
            card = tk.Frame(parent, bg=self.CARD_BG, bd=1, relief=tk.SOLID,
                           highlightbackground=self.BORDER, highlightthickness=0)
            card.pack(side=tk.LEFT, padx=(0, 4), fill=tk.X, expand=True)
            val = tk.Label(card, text=val_text, font=("Segoe UI", 16, "bold"),
                          fg="#f0f6fc", bg=self.CARD_BG, anchor="center")
            val.pack(pady=(6, 0))
            lbl = tk.Label(card, text=label_text, font=("Segoe UI", 7),
                          fg=self.DIM, bg=self.CARD_BG, anchor="center")
            lbl.pack(pady=(0, 4))
            return val, lbl

        self.card_elapsed_val, self.card_elapsed_lbl = _make_card(cards_frame, "--:--", "已用时", 0)
        self.card_items_val, self.card_items_lbl = _make_card(cards_frame, "0", "条目数", 0)
        self.card_stage_val, self.card_stage_lbl = _make_card(cards_frame, "空闲", "当前阶段", 0)

        # -- Ticker --
        ticker_block = tk.Frame(main, bg=self.CARD_BG, highlightbackground=self.BORDER, highlightthickness=1)
        ticker_block.pack(fill=tk.X, pady=(0, 8))

        ticker_header = tk.Frame(ticker_block, bg=self.CARD_BG)
        ticker_header.pack(fill=tk.X)
        tk.Label(ticker_header, text="●", font=("", 6), fg="#f85149", bg=self.CARD_BG).pack(side=tk.LEFT, padx=(P, 3), pady=(4, 0))
        tk.Label(ticker_header, text="实时动态", font=("Microsoft YaHei", 7), fg=self.DIM, bg=self.CARD_BG).pack(side=tk.LEFT, pady=(4, 0))

        self.ticker_body = tk.Frame(ticker_block, bg=self.CARD_BG)
        self.ticker_body.pack(fill=tk.X, padx=P, pady=(0, 4))
        self.ticker_label = tk.Label(self.ticker_body, text="等待数据...", fg=self.DIM, bg=self.CARD_BG,
                                      anchor="w", justify=tk.LEFT, font=("Microsoft YaHei", 8),
                                      wraplength=self.WIDTH - 40, height=2)
        self.ticker_label.pack(fill=tk.X)

        # -- Schedule timeline --
        sched_header = tk.Frame(main, bg=self.BG)
        sched_header.pack(fill=tk.X)
        tk.Label(sched_header, text="工作流", font=("Microsoft YaHei", 7, "bold"), fg=self.DIM, bg=self.BG).pack(side=tk.LEFT)

        self.schedule_frame = tk.Frame(main, bg=self.BG)
        self.schedule_frame.pack(fill=tk.X, pady=(2, 6))

        self.stage_rows = []  # (frame, icon_lbl, name_lbl, status_lbl)
        for key in STAGE_ORDER:
            info = STAGE_DEFS.get(key, {})
            row = tk.Frame(self.schedule_frame, bg=self.CARD_BG, highlightbackground=self.BORDER, highlightthickness=1)
            row.pack(fill=tk.X, pady=(0, 2))

            icon = tk.Label(row, text="●", font=("", 10), fg=self.DIM, bg=self.CARD_BG, width=2)
            icon.pack(side=tk.LEFT, padx=(6, 2), pady=3)
            name = tk.Label(row, text=info.get("label", key), font=("Microsoft YaHei", 9, "bold"),
                           fg=self.DIM, bg=self.CARD_BG, anchor="w")
            name.pack(side=tk.LEFT, pady=3)
            status_lbl = tk.Label(row, text="等待中", font=("Microsoft YaHei", 7),
                                 fg=self.DIM, bg="#21262d")
            status_lbl.pack(side=tk.RIGHT, padx=(0, 6), pady=3)
            self.stage_rows.append((row, icon, name, status_lbl))

        # ====== ACTION BAR (always visible) ======
        self.action_bar = tk.Frame(self.root, bg="#1a1206", highlightbackground="#d2991d", highlightthickness=1)
        self.action_bar.pack(fill=tk.X, padx=P, pady=(2, 4), side=tk.BOTTOM)
        self.action_bar.pack_propagate(True)

        action_inner = tk.Frame(self.action_bar, bg="#1a1206")
        action_inner.pack(fill=tk.X, padx=P, pady=8)
        self.action_msg = tk.Label(action_inner, text="加载中...", font=("Microsoft YaHei", 10),
                                    fg=self.YELLOW, bg="#1a1206", anchor="w", justify=tk.LEFT)
        self.action_msg.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.action_btn = tk.Label(action_inner, text="等待", font=("Microsoft YaHei", 10, "bold"),
                                    fg="#ffffff", bg="#1f6feb", padx=14, pady=4, cursor="hand2")
        self.action_btn.pack(side=tk.RIGHT)
        self._action_key = None

        # ====== BOTTOM BAR ======
        bottom = tk.Frame(self.root, bg=self.BG, height=22)
        bottom.pack(fill=tk.X, side=tk.BOTTOM)
        bottom.pack_propagate(False)
        bbar = tk.Label(bottom, text="右键菜单   |   — 隐藏   o 最小化   x 关闭",
                       font=("Microsoft YaHei", 6), fg="#484f58", bg=self.BG)
        bbar.pack(side=tk.LEFT, padx=P, pady=2)

        # Right-click menu
        self.root.bind("<Button-3>", self._right_click)

        # State
        self._schedule = {}
        self._ci_samples = []
        self._last_git_pull = 0
        self._git_feedback = ""  # set by _auto_git_pull background thread
        self._git_feedback_until = 0  # show feedback in ticker until this timestamp
        self._ticker_items = []
        self._ticker_idx = 0
        self._ticker_job = None
        self._seen_samples = set()
        self._poll_interval = NORMAL_POLL_INTERVAL_MS
        self._poll_job = None
        self._available_reports = []  # sorted list of week labels from output/
        self._selected_week = ""  # user-selected week, or latest
        self._scan_reports()

        self.poll()
        if run_pipeline:
            self.root.after(1000, self._auto_run)

    # ====== Drag ======
    def _start_drag(self, event):
        self._drag_x = event.x; self._drag_y = event.y

    def _drag(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _right_click(self, event):
        menu = tk.Menu(self.root, tearoff=0, bg="#161b22", fg="#c9d1d9",
                       activebackground="#21262d", activeforeground="#f0f6fc")
        menu.add_command(label="刷新", command=self.poll)
        menu.add_command(label="浏览器打开", command=lambda: webbrowser.open("http://127.0.0.1:8766"))
        menu.add_separator()
        menu.add_command(label="关闭", command=self.root.destroy)
        menu.post(event.x_root, event.y_root)

    def _auto_run(self):
        try:
            week = WidgetHandler.selected_week or WidgetHandler._get_active_week_label()
            data = WidgetHandler._assemble_schedule(week)
            if not data.get("ci_done"):
                return
            steps = data.get("steps", [])
            has_report = any(s.get("done") and s.get("key") == "review_generate" for s in steps)
            if has_report:
                return
            if not data.get("local_running") and not data.get("local_done"):
                self._fetch_api("/run-pipeline", method="POST")
        except Exception:
            pass

    # ====== HTTP ======
    def _fetch(self, path):
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:8766{path}", timeout=3) as r:
                return r.read().decode()
        except Exception:
            return None

    def _fetch_api(self, path, method="GET"):
        import urllib.request
        try:
            req = urllib.request.Request(f"http://127.0.0.1:8766{path}", method=method)
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

    # ====== Polling (direct, no HTTP — same process, no timeout / thread risk) ======
    def poll(self):
        try:
            week = WidgetHandler.selected_week or WidgetHandler._get_active_week_label()

            # ── Side effect: detect new CI ──
            state = WidgetHandler._build_steps(week)
            if state["ci_available"] and not state["report_exists"] and os.path.exists(STATUS_FILE):
                try:
                    ci_mtime = os.path.getmtime(CI_RAW_FILE)
                    with open(STATUS_FILE, "r", encoding="utf-8") as f:
                        gs = json.load(f)
                    pipe_start = gs.get("pipeline_started_at", "")
                    if pipe_start and ci_mtime > 0:
                        from datetime import datetime as dt2
                        if dt2.fromtimestamp(ci_mtime) > dt2.strptime(pipe_start, "%Y-%m-%dT%H:%M:%S"):
                            WidgetHandler._reset_status_for_new_ci(week, state["ci_items_count"])
                except Exception:
                    pass

            # ── Assemble data (same helpers used by HTTP endpoints) ──
            self._schedule = WidgetHandler._assemble_schedule(week)
            status_data = WidgetHandler._assemble_status(week)

            self._ci_samples = self._schedule.get("ci_samples", [])
            self._update_schedule_ui()
            self._update_cards(status_data)

            # Monitor video workflow process
            if WidgetHandler.video_proc is not None:
                rc = WidgetHandler.video_proc.poll()
                if rc is not None:
                    WidgetHandler.video_proc = None
                    schedule_steps = self._schedule.get("steps", [])
                    jy = next((s for s in schedule_steps if s.get("key") == "jianying_draft"), None)
                    if jy and jy.get("running") and rc != 0:
                        self._update_jianying_status_on_disk("error")
        except Exception as e:
            import traceback
            print(f"[DEBUG poll ERROR] week={WidgetHandler.selected_week}: {e}", flush=True)
            traceback.print_exc()

        # Dynamic polling interval
        ci_done = self._schedule.get("ci_done", False)
        steps = self._schedule.get("steps", [])
        running = any(s.get("running") for s in steps)
        if not ci_done and not running:
            self._poll_interval = CI_POLL_INTERVAL_MS
            self._auto_git_pull()
        else:
            self._poll_interval = NORMAL_POLL_INTERVAL_MS
            if not running:
                self._auto_git_pull()

        if not hasattr(self, '_poll_count'):
            self._poll_count = 0
        self._poll_count += 1
        if self._poll_count % 10 == 0:
            self._scan_reports()

        if self._poll_job:
            self.root.after_cancel(self._poll_job)
        self._poll_job = self.root.after(self._poll_interval, self.poll)

    # ====== UI Updates ======
    def _update_cards(self, s: dict):
        elapsed = s.get("elapsed_seconds", 0) or 0
        m, sec = divmod(elapsed, 60)
        self.card_elapsed_val.config(text=f"{m:02d}:{sec:02d}")

        total = 0
        for k, v in s.get("stages", {}).items():
            total += v.get("items_count", 0)
        self.card_items_val.config(text=str(total or s.get("items_so_far", 0) or "0"))

        cur = s.get("current_stage")
        # Fallback: find the latest non-done stage from stages dict
        if not cur:
            stages = s.get("stages", {})
            for key in STAGE_ORDER:
                st = stages.get(key, {})
                if st.get("status") == "running":
                    cur = key
                    break
            if not cur:
                # Show next pending stage, or last done
                for key in STAGE_ORDER:
                    st = stages.get(key, {})
                    if st.get("status") in ("pending", "needs_you"):
                        cur = key
                        break
                if not cur:
                    cur = STAGE_ORDER[-1]  # all done → show last stage
        # Card stage text is set by _update_schedule_ui (single source of truth).
        # Only keep the status-dot / progress / step-label updates here.

        # Status dot
        if s.get("overall_done"):
            self.status_dot.config(fg=self.GREEN)
        elif cur:
            self.status_dot.config(fg=self.ACCENT)
        else:
            self.status_dot.config(fg=self.DIM)

        # Progress bar (use actual canvas width to avoid gaps)
        pct = self._calc_progress(s.get("stages", {}))
        cw = self.progress_canvas.winfo_width()
        if cw < 10:
            cw = self.WIDTH - 24  # fallback before first render
        self.progress_canvas.coords(self.progress_bar, 0, 0, int(cw * pct), 4)
        self.progress_canvas.itemconfig(self.progress_bar,
                                         fill=self.GREEN if s.get("overall_done") else self.ACCENT)

        # Step labels
        stages = s.get("stages", {})
        for i, key in enumerate(STAGE_ORDER):
            st = stages.get(key, {})
            status = st.get("status", "pending")
            if status == "done":
                self.step_labels[i].config(fg=self.GREEN)
            elif status == "running":
                self.step_labels[i].config(fg=self.ACCENT)
            else:
                self.step_labels[i].config(fg="#484f58")

    def _update_schedule_ui(self):
        sch = self._schedule
        steps = sch.get("steps", [])

        # Week label — synced from schedule but display via _update_week_label_display
        schedule_week = sch.get("week_label", "")
        if schedule_week and schedule_week not in WidgetHandler.available_reports:
            # New report detected, rescan
            self._scan_reports()
        self._update_week_label_display()

        # Stage rows
        for i, step in enumerate(steps):
            if i >= len(self.stage_rows):
                break
            row, icon, name, status_lbl = self.stage_rows[i]

            if step.get("done"):
                icon.config(text="✓", fg=self.GREEN)
                name.config(fg=self.GREEN)
                status_lbl.config(text="完成", fg=self.GREEN, bg="#0d1f14")
            elif step.get("running"):
                icon.config(text="●", fg=self.ACCENT)
                name.config(fg=self.FG)
                elapsed = step.get("elapsed_seconds", 0)
                m, s = divmod(elapsed, 60)
                status_lbl.config(text=f"{m}分{s:02d}秒", fg=self.ACCENT, bg="#0d1a33")
            elif step.get("needs_you"):
                icon.config(text="!", fg=self.YELLOW)
                name.config(fg=self.FG)
                status_lbl.config(text="需操作", fg=self.YELLOW, bg="#3d2800")
            elif step.get("error"):
                icon.config(text="✗", fg=self.RED)
                name.config(fg=self.RED)
                status_lbl.config(text="错误", fg=self.RED, bg="#1a0a0a")
            else:
                icon.config(text="●", fg="#484f58")
                name.config(fg=self.DIM)
                em = max(1, (step.get("estimated_seconds", 300) // 60))
                status_lbl.config(text=f"~{em}分", fg=self.DIM, bg="#21262d")

        # Stage card
        running_step = next((s for s in steps if s.get("running")), None)
        needs_you_step = next((s for s in steps if s.get("needs_you")), None)
        all_done = all(s.get("done") for s in steps)
        if running_step:
            self.card_stage_val.config(text=running_step["name"], fg=self.ACCENT)
        elif needs_you_step:
            self.card_stage_val.config(text=needs_you_step["name"], fg=self.YELLOW)
        elif all_done:
            self.card_stage_val.config(text="完成", fg=self.GREEN)
        else:
            # Find last done or first pending
            shown = False
            for s in steps:
                if not s.get("done"):
                    self.card_stage_val.config(text=s["name"], fg=self.DIM)
                    shown = True
                    break
            if not shown:
                self.card_stage_val.config(text="完成", fg=self.GREEN)

        # Ticker
        self._update_ticker(steps)

        # Action bar
        self._update_action_bar(steps)

    def _update_ticker(self, steps: list):
        # Find running stage
        running_step = None
        for s in steps:
            if s.get("running"):
                running_step = s
                break

        if running_step:
            samples = running_step.get("samples", [])
            for t in samples:
                if t and t not in self._seen_samples:
                    self._seen_samples.add(t)
                    self._ticker_items.insert(0, t)
                    if len(self._ticker_items) > 30:
                        self._ticker_items.pop()
            if self._ticker_items and self._ticker_job is None:
                self.ticker_label.config(fg=self.FG)
                self._next_tick()
            elif not self._ticker_items and self._ticker_job is None:
                sub = running_step.get("sub_stage", "")
                self.ticker_label.config(text=sub or running_step.get("name", ""),
                                          fg=self.ACCENT)
        elif self._ci_samples:
            # CI done, show CI samples — refresh when ticker is empty or stale
            if not self._ticker_items:
                for t in self._ci_samples:
                    if t and t not in self._seen_samples:
                        self._seen_samples.add(t)
                        self._ticker_items.insert(0, t)
                        if len(self._ticker_items) > 30:
                            self._ticker_items.pop()
            if self._ticker_items and self._ticker_job is None:
                self.ticker_label.config(fg=self.FG)
                self._next_tick()
            elif not self._ticker_items and self._ticker_job is None:
                self.ticker_label.config(text="CI 数据已就绪", fg=self.GREEN)
        elif not self._ticker_items:
            # Show git feedback briefly if available
            fb = self._git_feedback
            if fb and time.time() < self._git_feedback_until:
                self.ticker_label.config(text=f"Git: {fb}", fg=self.DIM)
            else:
                self._git_feedback = ""
                # Show contextual message based on step states
                all_done = all(s.get("done") for s in steps)
                needs_you_step = next((s for s in steps if s.get("needs_you")), None)
                if all_done:
                    self.ticker_label.config(text="全部阶段完成", fg=self.GREEN)
                elif needs_you_step:
                    self.ticker_label.config(text=f"下一步: {needs_you_step['name']}", fg=self.YELLOW)
                else:
                    self.ticker_label.config(text="等待 CI 数据...", fg=self.DIM)

    def _stop_ticker(self):
        if self._ticker_job:
            self.root.after_cancel(self._ticker_job)
            self._ticker_job = None

    def _next_tick(self):
        if not self._ticker_items:
            self._ticker_job = None
            return
        item = self._ticker_items[self._ticker_idx % len(self._ticker_items)]
        self._ticker_idx += 1
        self.ticker_label.config(text=item, fg=self.FG)
        self._ticker_job = self.root.after(6000, self._next_tick)

    def _update_action_bar(self, steps: list):
        # Find needs_you or running step
        needs_you = next((s for s in steps if s.get("needs_you")), None)
        running = next((s for s in steps if s.get("running")), None)
        all_done = self._schedule.get("overall_done", False) or all(s.get("done") for s in steps)
        ci_done = self._schedule.get("ci_done", False)
        review_done = self._schedule.get("review_done", False)

        week = self._schedule.get("week_label", "?")
        print(f"[DEBUG _update_action_bar] week={week} needs_you={needs_you['key'] if needs_you else None} ci_done={ci_done} report_exists={self._schedule.get('report_exists',False)}", flush=True)

        msg = "加载中..."
        btn_text = "等待"
        btn_color = "#21262d"
        bar_bg = "#161b22"
        bar_border = "#21262d"

        # Check: pipeline done but manual review not completed.
        # Only prompt when NO report exists — for historical weeks the report
        # was already generated (review either completed or not needed).
        rg = next((s for s in steps if s["key"] == "review_generate"), None)
        report_exists = self._schedule.get("report_exists", False)
        pipeline_done_no_review = (
            rg and rg.get("done")
            and not review_done
            and not running
            and not report_exists
        )

        if pipeline_done_no_review:
            self._action_key = "review_generate"
            msg = "管道完成。审核过滤项？"
            btn_text = "打开审核"
            btn_color = "#9e6a03"
            bar_bg = "#1a1206"; bar_border = "#d2991d"
        elif needs_you:
            self._action_key = needs_you["key"]
            bar_bg = "#0d1a33"; bar_border = "#58a6ff"
            if needs_you["key"] == "local_collect":
                msg = "CI 数据就绪。运行本地管道？"
                btn_text = "运行管道"
                btn_color = "#1f6feb"
            elif needs_you["key"] == "online_merge":
                msg = "报告已生成。打开最终报告？"
                btn_text = "打开报告"
                btn_color = "#238636"; bar_bg = "#0d1f14"; bar_border = "#238636"
            elif needs_you["key"] == "jianying_draft":
                msg = "报告就绪。创建剪映草稿？"
                btn_text = "创建草稿"
                btn_color = "#238636"; bar_bg = "#0d1f14"; bar_border = "#238636"
        elif running:
            self._action_key = None
            msg = f">>> {running['name']}: {running.get('sub_stage', '运行中...')}"
            btn_text = "运行中"
            btn_color = "#21262d"
            bar_bg = "#0d1a33"; bar_border = "#58a6ff"
        elif all_done:
            self._action_key = None
            msg = "全部阶段完成！"
            btn_text = "完成"
            btn_color = "#21262d"
            bar_bg = "#0d1f14"; bar_border = "#238636"
        elif not ci_done:
            self._action_key = None
            msg = "等待 CI（已开启自动拉取）..."
            btn_text = "等待"
            btn_color = "#21262d"

        # Always update bar (never hide)
        self.action_bar.configure(bg=bar_bg, highlightbackground=bar_border)
        for child in self.action_bar.winfo_children():
            try: child.configure(bg=bar_bg)
            except: pass
        self.action_msg.config(text=msg, fg=self.YELLOW if bar_border == "#d2991d" else self.ACCENT)
        self.action_btn.config(text=btn_text, bg=btn_color,
                                fg="#ffffff" if btn_color != "#21262d" else self.DIM)
        self.action_btn.unbind("<Button-1>")
        if self._action_key:
            self.action_btn.bind("<Button-1>", lambda e: self._handle_action(self._action_key))
            self.action_btn.config(cursor="hand2")
        else:
            self.action_btn.config(cursor="arrow")

    def _handle_action(self, key: str):
        if key == "local_collect":
            self._fetch_api("/run-pipeline", "POST")
            self.action_btn.config(text="启动中...", bg=self.ACCENT)
            self.root.after(1500, self.poll)
            self.root.after(4000, self.poll)
        elif key == "review_generate":
            proj = os.path.dirname(os.path.abspath(__file__))
            args = [sys.executable, "review_filtered.py"]
            sw = WidgetHandler.selected_week
            if sw:
                args.extend(["--week", sw])
            subprocess.Popen(args, cwd=proj,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)
        elif key == "online_merge":
            self._fetch_api("/run-merge", "POST")
            self.action_btn.config(text="打开中...", bg=self.GREEN)
            self.root.after(1500, self.poll)
        elif key == "jianying_draft":
            self._fetch_api("/run-jianying", "POST")
            self.action_btn.config(text="启动中...", bg=self.GREEN)
            self.root.after(1500, self.poll)

    # ====== Helpers ======
    def _scan_reports(self):
        """Scan output/ for available weekly reports, sync to WidgetHandler.available_reports."""
        import re
        from datetime import datetime, timezone, timedelta

        project_dir = os.path.dirname(os.path.abspath(__file__))
        out = os.path.join(project_dir, "output")
        reports = set()
        if os.path.isdir(out):
            for f in os.listdir(out):
                m = re.match(r"(20\d{2}-W\d{2}(?:-[上下])?)\.md$", f)
                if m:
                    reports.add(m.group(1))

        # Also include the currently active period (from get_week_label logic)
        # Only add the period whose CI is expected — no future weeks.
        now_utc = datetime.now(timezone.utc)
        wd = now_utc.weekday()
        if wd <= 2:
            active_half = "下"
            rw = (now_utc - timedelta(days=3)).isocalendar()
        elif wd <= 4:
            active_half = "上"
            rw = now_utc.isocalendar()
        else:
            active_half = "下"
            rw = (now_utc - timedelta(days=3)).isocalendar()
        active_label = f"{rw[0]}-W{rw[1]:02d}-{active_half}"
        reports.add(active_label)

        WidgetHandler.available_reports = sorted(reports, reverse=True)
        # Default to the most relevant week: prefer one with CI data, then latest with report
        if WidgetHandler.available_reports and not WidgetHandler.selected_week:
            # Check if CI data exists and find matching week
            # Default to week matching CI data's mtime, or latest report
            ci_file = os.path.join(OUTPUT_DIR, ".ci_raw_items.json")
            chosen = None
            if os.path.isfile(ci_file):
                import re as _re
                try:
                    ci_mt = os.path.getmtime(ci_file)
                    best_match = ""
                    best_diff = float("inf")
                    for f in os.listdir(OUTPUT_DIR):
                        m = _re.match(r"(20\d{2}-W\d{2}(?:-[上下])?)\.md$", f)
                        if not m:
                            continue
                        diff = abs(ci_mt - os.path.getmtime(os.path.join(OUTPUT_DIR, f)))
                        if diff < best_diff and diff < 5:
                            best_diff = diff
                            best_match = m.group(1)
                    if best_match and best_match in WidgetHandler.available_reports:
                        chosen = best_match
                except Exception:
                    pass
            # Fallback: prefer the most recent week that has an actual report file,
            # not a future/active week with no data yet (which sorts alphabetically first).
            if chosen is None:
                for r in WidgetHandler.available_reports:
                    if os.path.isfile(os.path.join(OUTPUT_DIR, f"{r}.md")):
                        chosen = r
                        break
            WidgetHandler.selected_week = chosen or WidgetHandler.available_reports[0]
        self._update_week_label_display()

    def _update_week_label_display(self):
        """Update the week label appearance."""
        count = len(WidgetHandler.available_reports)
        if count == 0:
            self.week_label.config(text="无报告", fg=self.DIM, bg="#21262d", cursor="arrow")
        elif count == 1:
            self.week_label.config(text=WidgetHandler.selected_week, fg=self.ACCENT, bg="#1f2a3a", cursor="hand2")
        else:
            self.week_label.config(text=f"{WidgetHandler.selected_week} ▼", fg=self.ACCENT, bg="#1f2a3a", cursor="hand2")

    def _cycle_week(self, event=None):
        """Show dropdown menu to select from available reports."""
        if not WidgetHandler.available_reports:
            return
        # Visual feedback: flash the label to confirm the click registered
        self.week_label.config(bg="#58a6ff")
        self.root.after(200, lambda: self.week_label.config(bg="#1f2a3a"))
        print(f"[DEBUG _cycle_week] creating menu, selected={WidgetHandler.selected_week} available={WidgetHandler.available_reports}", flush=True)
        menu = tk.Menu(self.root, tearoff=0,
                       bg="#161b22", fg="#c9d1d9",
                       activebackground="#1f6feb", activeforeground="#ffffff",
                       font=("Microsoft YaHei", 9))
        for label in WidgetHandler.available_reports:
            is_current = (label == WidgetHandler.selected_week)
            menu.add_command(
                label=f"  {label}  ◀" if is_current else f"  {label}",
                command=lambda w=label: self._select_week(w),
            )
        x = self.week_label.winfo_rootx()
        y = self.week_label.winfo_rooty() + self.week_label.winfo_height()
        menu.post(x, y)

    def _select_week(self, label):
        WidgetHandler.selected_week = label
        print(f"[DEBUG _select_week] label={label} selected_week={WidgetHandler.selected_week}", flush=True)
        self._update_week_label_display()
        self._seen_samples.clear()
        self._ticker_items.clear()
        self._stop_ticker()
        # Cancel pending poll to avoid race with rapid week switching
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        self.poll()

    def _update_jianying_status_on_disk(self, status_val: str):
        """Write jianying_draft stage status directly to status files (called from poll)."""
        try:
            sw = WidgetHandler.selected_week
            targets = [STATUS_FILE]
            if sw:
                pwf = os.path.join(OUTPUT_DIR, f".pipeline_status_{sw}.json")
                if os.path.isfile(pwf):
                    targets.append(pwf)
            for target in targets:
                if os.path.exists(target):
                    with open(target, "r", encoding="utf-8") as f:
                        st = json.load(f)
                    st.setdefault("stages", {}).setdefault("jianying_draft", {})["status"] = status_val
                    st["updated_at"] = time.strftime("%H:%M:%S")
                    with open(target, "w", encoding="utf-8") as f:
                        json.dump(st, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _calc_progress(self, stages: dict) -> float:
        completed = 0.0
        all_done = True
        for key in STAGE_ORDER:
            st = stages.get(key, {})
            status = st.get("status", "pending")
            if status == "done":
                completed += 1.0
            else:
                all_done = False
                if status == "running":
                    prog = st.get("progress")
                    if prog and prog.get("total", 0) > 0:
                        completed += prog["current"] / prog["total"]
                    else:
                        completed += 0.3
        if all_done:
            return 1.0
        return max(0.02, min(0.98, completed / len(STAGE_ORDER))) if completed > 0 else 0.02

    def _auto_git_pull(self):
        now = time.time()
        if now - self._last_git_pull < 180:
            return
        self._last_git_pull = now

        def _run():
            project_dir = os.path.dirname(os.path.abspath(__file__))
            try:
                fetch_result = subprocess.run(
                    ["git", "fetch", "origin", "master"],
                    cwd=project_dir, capture_output=True, text=True, timeout=30,
                    creationflags=_NO_WINDOW)
                if fetch_result.returncode != 0:
                    self._git_feedback = "git fetch 失败"
                    return

                result = subprocess.run(
                    ["git", "rev-list", "--count", "HEAD..origin/master"],
                    cwd=project_dir, capture_output=True, text=True, timeout=10,
                    creationflags=_NO_WINDOW)
                count = int(result.stdout.strip() or "0")
                if count > 0:
                    pull_result = subprocess.run(
                        ["git", "pull", "--rebase", "--autostash", "origin", "master"],
                        cwd=project_dir, capture_output=True, text=True, timeout=60,
                        creationflags=_NO_WINDOW)
                    if pull_result.returncode != 0:
                        self._git_feedback = "git pull 冲突"
                    else:
                        self._git_feedback = f"+{count} commits"
            except subprocess.TimeoutExpired:
                self._git_feedback = "git 超时"
            except Exception:
                self._git_feedback = "git 错误"

        threading.Thread(target=_run, daemon=True).start()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    run_pipeline = "--run" in sys.argv
    port = DEFAULT_PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    # Kill any stale widget already bound to our port
    if sys.platform == "win32":
        try:
            import subprocess as _sp
            out = _sp.check_output(
                f'netstat -ano | findstr "127.0.0.1:{port}" | findstr "LISTENING"',
                shell=True, text=True,
            )
            for line in out.strip().splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[1] == f"127.0.0.1:{port}":
                    _sp.run(["taskkill", "/f", "/pid", parts[4]],
                           capture_output=True, creationflags=_NO_WINDOW)
        except Exception:
            pass

    try:
        server = start_server(port)
    except OSError:
        # Port still in use — another widget is already running, don't start a second one
        import tkinter.messagebox as _mb
        _mb.showwarning("Gaming News", "Widget 已在运行中（端口占用），请关闭后重试。")
        sys.exit(1)

    widget = FloatingWidget(run_pipeline=run_pipeline)
    widget.run()
