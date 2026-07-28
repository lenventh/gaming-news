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

class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

from config import OUTPUT_DIR
from pipeline.status import STATUS_FILE, STAGE_ORDER, STAGE_DEFS
from pipeline.filter import _FILTERED_LOG_PATH

DEFAULT_PORT = 8766
SELECTION_FILE = os.path.join(OUTPUT_DIR, ".filtered_selection.json")
CI_RAW_FILE = os.path.join(OUTPUT_DIR, ".ci_raw_items.json")

CI_POLL_INTERVAL_MS = 60000
NORMAL_POLL_INTERVAL_MS = 2000


# ====== Mini HTTP Server ======
class WidgetHandler(BaseHTTPRequestHandler):
    pipeline_proc = None
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
            self._serve_json(STATUS_FILE, {
                "stage": "init", "stage_label": "idle",
                "elapsed_seconds": 0, "items_so_far": 0,
                "samples": [], "sources": {},
                "done": False, "current_stage": None,
                "stages": {}, "overall_done": False,
            })
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

    def _serve_schedule(self):
        # Use selected week from available reports, or fall back to current date
        week_label = WidgetHandler.selected_week
        if not week_label:
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            bj_now = now + timedelta(hours=8)
            iso = bj_now.isocalendar()
            week_label = f"{iso[0]}-W{iso[1]:02d}"

        ci_done = os.path.exists(CI_RAW_FILE)
        st_exists = os.path.exists(STATUS_FILE)
        sel_exists = os.path.exists(SELECTION_FILE)

        pipeline_stages = {}
        local_running = False
        local_done = False
        overall_done = False
        status_stale = False
        if st_exists:
            try:
                with open(STATUS_FILE, "r", encoding="utf-8") as f:
                    st = json.load(f)
                pipeline_stages = st.get("stages", {})
                overall_done = st.get("overall_done", False)
                local_running = bool(pipeline_stages.get("local_collect", {}).get("status") == "running")
                local_done = bool(pipeline_stages.get("local_collect", {}).get("status") == "done")

                updated_at = st.get("updated_at", "")
                if updated_at:
                    from datetime import datetime as dt2
                    now_str = dt2.now().strftime("%H:%M:%S")
                    try:
                        updated_parts = [int(x) for x in updated_at.split(":")]
                        now_parts = [int(x) for x in now_str.split(":")]
                        updated_secs = updated_parts[0]*3600 + updated_parts[1]*60 + updated_parts[2]
                        now_secs = now_parts[0]*3600 + now_parts[1]*60 + now_parts[2]
                        if now_secs - updated_secs > 60:
                            status_stale = True
                    except Exception:
                        pass
            except Exception:
                pass

        review_done = review_count = 0
        if sel_exists:
            try:
                with open(SELECTION_FILE, "r", encoding="utf-8") as f:
                    sel = json.load(f)
                review_done = True; review_count = sel.get("count", 0)
            except Exception:
                pass

        ci_items_count = 0
        ci_samples = []
        ci_mtime = 0
        if ci_done:
            try:
                ci_mtime = os.path.getmtime(CI_RAW_FILE)
                with open(CI_RAW_FILE, "r", encoding="utf-8") as f:
                    ci_data = json.load(f)
                if isinstance(ci_data, list):
                    ci_items_count = len(ci_data)
                    ci_samples = [it.get("title", "") for it in ci_data[:30] if it.get("title")]
            except Exception:
                pass

        # Detect NEW CI run: CI file fresher than pipeline start
        pipeline_started_at = ""
        new_ci_detected = False
        if st_exists and ci_done:
            try:
                pipeline_started_at = st.get("pipeline_started_at", "")
                if pipeline_started_at and ci_mtime > 0:
                    # Compare CI file mtime with pipeline start
                    from datetime import datetime as dt2
                    ci_dt = dt2.fromtimestamp(ci_mtime)
                    pipe_dt = dt2.strptime(pipeline_started_at, "%Y-%m-%dT%H:%M:%S")
                    if ci_dt > pipe_dt:
                        new_ci_detected = True
            except Exception:
                pass

        # Build 5 steps — per-week aware
        selected_week = week_label
        report_path = os.path.join(OUTPUT_DIR, f"{selected_week}.md") if selected_week else ""
        report_exists = os.path.isfile(report_path) if report_path else False

        # Try loading per-week status file first
        per_week_stages = {}
        per_week_overall = False
        if selected_week:
            per_week_file = os.path.join(OUTPUT_DIR, f".pipeline_status_{selected_week}.json")
            if os.path.isfile(per_week_file):
                try:
                    with open(per_week_file, "r", encoding="utf-8") as f:
                        pw = json.load(f)
                    per_week_stages = pw.get("stages", {})
                    per_week_overall = pw.get("overall_done", False)
                except Exception:
                    pass

        steps = []
        for i, key in enumerate(STAGE_ORDER):
            info = STAGE_DEFS.get(key, {})
            est = info.get("estimated_seconds", 300)

            if per_week_stages:
                # Per-week status file exists — show its state
                ps = per_week_stages.get(key, {})
                status = ps.get("status", "pending")
                needs_you = False
                steps.append({
                    "key": key, "name": info.get("label", key),
                    "emoji": info.get("emoji", ""),
                    "done": status == "done",
                    "running": status == "running",
                    "needs_you": needs_you,
                    "error": status == "error",
                    "estimated_seconds": est,
                    "elapsed_seconds": ps.get("elapsed_seconds", 0),
                    "sub_stage": ps.get("sub_stage", ""),
                    "items_count": ps.get("items_count", 0),
                    "samples": ps.get("samples", [])[-5:],
                })
            elif report_exists:
                # Completed report, no per-week file → all done
                steps.append({
                    "key": key, "name": info.get("label", key),
                    "emoji": info.get("emoji", ""),
                    "done": True, "running": False, "needs_you": False, "error": False,
                    "estimated_seconds": est, "elapsed_seconds": est,
                    "sub_stage": "", "items_count": 0, "samples": [],
                })
            else:
                ps = pipeline_stages.get(key, {}) if pipeline_stages else {}
                status = ps.get("status", "pending")

                if status == "pending" and key == "ci_collect" and ci_done:
                    status = "done"

                if status == "running" and status_stale:
                    status = "error"

                needs_you = False
                later_active = False
                for later_key in STAGE_ORDER[i+1:]:
                    ls = pipeline_stages.get(later_key, {})
                    if ls.get("status") in ("running", "done"):
                        later_active = True
                        break

                if not later_active:
                    if key == "local_collect" and ci_done and status == "pending":
                        needs_you = True
                    elif key == "review_generate" and status == "pending":
                        prev = pipeline_stages.get("local_collect", {})
                        if prev.get("status") == "done":
                            needs_you = True
                    elif key == "online_merge" and status == "pending":
                        prev = pipeline_stages.get("review_generate", {})
                        if prev.get("status") == "done":
                            needs_you = True
                    elif key == "jianying_draft" and status == "pending":
                        prev = pipeline_stages.get("online_merge", {})
                        if prev.get("status") == "done":
                            needs_you = True

                steps.append({
                    "key": key, "name": info.get("label", key),
                    "emoji": info.get("emoji", ""),
                    "done": status == "done",
                    "running": status == "running",
                    "needs_you": needs_you,
                    "error": status == "error",
                    "estimated_seconds": est,
                    "elapsed_seconds": ps.get("elapsed_seconds", 0),
                    "sub_stage": ps.get("sub_stage", ""),
                    "items_count": ps.get("items_count", 0),
                    "samples": ps.get("samples", [])[-5:],
                })

        # If new CI data detected, reset local stages to show CI just completed
        if new_ci_detected and not overall_done:
            ci_done = True  # CI is done (new data exists)
            # Reset downstream stages so user can re-run
            for k in ["local_collect", "review_generate", "online_merge", "jianying_draft"]:
                if k in pipeline_stages and pipeline_stages[k].get("status") == "done":
                    pipeline_stages[k]["status"] = "pending"

        self._json({
            "week_label": week_label,
            "ci_done": ci_done,
            "ci_items_count": ci_items_count,
            "ci_samples": ci_samples,
            "local_running": local_running,
            "local_done": local_done,
            "review_done": review_done,
            "review_count": review_count,
            "overall_done": per_week_overall if per_week_stages else (overall_done or report_exists),
            "steps": steps,
        })

    def _serve_filtered(self):
        try:
            if os.path.exists(_FILTERED_LOG_PATH):
                with open(_FILTERED_LOG_PATH, "r", encoding="utf-8") as f:
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
        project_dir = os.path.dirname(os.path.abspath(__file__))
        self.__class__.pipeline_proc = subprocess.Popen(
            [sys.executable, "main.py", "--from-ci"],
            cwd=project_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _save_selection(self, titles):
        from pathlib import Path
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        with open(SELECTION_FILE, "w", encoding="utf-8") as f:
            json.dump({"titles": titles, "count": len(titles)}, f, ensure_ascii=False, indent=2)

    def _run_recover(self):
        try:
            result = subprocess.run(
                [sys.executable, "main.py", "--recover-reviewed"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                capture_output=True, text=True, timeout=600)
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
        subprocess.Popen(
            args, cwd=project_dir,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._update_stage_status("jianying_draft", "done")
        self._json({"ok": True, "msg": "Video workflow started"})

    def _update_stage_status(self, key: str, status_val: str):
        try:
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE, "r", encoding="utf-8") as f:
                    st = json.load(f)
            else:
                st = {}
            stages = st.setdefault("stages", {})
            stage = stages.setdefault(key, {})
            stage["status"] = status_val
            st["updated_at"] = time.strftime("%H:%M:%S")
            if status_val == "done":
                stage["done_at"] = time.strftime("%H:%M:%S")
            # Check if all done
            if all(stages.get(k, {}).get("status") == "done" for k in STAGE_ORDER):
                st["overall_done"] = True
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
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
            resp = json.loads(self._fetch("/schedule") or "{}")
            if resp.get("ci_done") and not resp.get("local_running") and not resp.get("local_done"):
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

    # ====== Polling ======
    def poll(self):
        try:
            s_raw = self._fetch("/status")
            sch_raw = self._fetch("/schedule")
            if sch_raw:
                self._schedule = json.loads(sch_raw)
                self._ci_samples = self._schedule.get("ci_samples", [])
                self._update_schedule_ui()
            if s_raw:
                self._update_cards(json.loads(s_raw))
        except Exception:
            pass

        # Dynamic polling interval
        ci_done = self._schedule.get("ci_done", False)
        steps = self._schedule.get("steps", [])
        running = any(s.get("running") for s in steps)
        if not ci_done and not running:
            self._poll_interval = CI_POLL_INTERVAL_MS
            self._auto_git_pull()
        else:
            self._poll_interval = NORMAL_POLL_INTERVAL_MS
            # Periodically git pull even when ci_done, to detect re-triggered CI
            if not running:
                self._auto_git_pull()

        # Periodically scan for new reports (every 10th poll ~ 20s)
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
        self.card_stage_val.config(
            text=STAGE_DEFS.get(cur, {}).get("label", cur or "空闲"),
            fg=self.ACCENT if s.get("stages", {}).get(cur, {}).get("status") in ("running",) else self.DIM,
        )

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
                self._ticker_label.config(fg=self.FG)
                self._next_tick()
            elif not self._ticker_items and self._ticker_job is None:
                sub = running_step.get("sub_stage", "")
                self.ticker_label.config(text=sub or running_step.get("name", ""),
                                          fg=self.ACCENT)
        elif self._ci_samples and not self._ticker_items:
            # CI done, show CI samples
            for t in self._ci_samples:
                if t and t not in self._seen_samples:
                    self._seen_samples.add(t)
                    self._ticker_items.insert(0, t)
                    if len(self._ticker_items) > 30:
                        self._ticker_items.pop()
            if self._ticker_items and self._ticker_job is None:
                self._ticker_label.config(fg=self.FG)
                self._next_tick()
        elif not self._ticker_items:
            self.ticker_label.config(text="等待数据...", fg=self.DIM)

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

        msg = "加载中..."
        btn_text = "等待"
        btn_color = "#21262d"
        bar_bg = "#161b22"
        bar_border = "#21262d"

        # Check: pipeline done but manual review not completed
        rg = next((s for s in steps if s["key"] == "review_generate"), None)
        pipeline_done_no_review = (rg and rg.get("done") and not review_done and not running)

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
            subprocess.Popen([sys.executable, "review_filtered.py"], cwd=proj,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.root.after(1000, lambda: webbrowser.open("http://127.0.0.1:8765"))
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
        project_dir = os.path.dirname(os.path.abspath(__file__))
        out = os.path.join(project_dir, "output")
        reports = set()
        if os.path.isdir(out):
            for f in os.listdir(out):
                m = re.match(r"(20\d{2}-W\d{2}(?:-[上下])?)\.md$", f)
                if m:
                    reports.add(m.group(1))
        WidgetHandler.available_reports = sorted(reports, reverse=True)
        # Default to latest
        if WidgetHandler.available_reports and not WidgetHandler.selected_week:
            WidgetHandler.selected_week = WidgetHandler.available_reports[0]
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
        self._update_week_label_display()
        if self._schedule:
            self._update_schedule_ui()

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
                result = subprocess.run(
                    ["git", "pull", "--rebase", "origin", "master"],
                    cwd=project_dir, capture_output=True, text=True, timeout=30)
                if "Already up to date" not in result.stdout and result.returncode == 0:
                    pass  # updated data will be picked up on next poll
            except Exception:
                pass

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

    server = start_server(port)
    widget = FloatingWidget(run_pipeline=run_pipeline)
    widget.run()
