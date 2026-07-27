#!/usr/bin/env python3
"""桌面悬浮窗 — 周刊进度监控小部件 (半透明 + 置顶 + 可拖拽)。

用法:
    python floating_widget.py          # 启动悬浮窗
    python floating_widget.py --run    # 启动悬浮窗 + 自动运行管道
"""

import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler

from config import OUTPUT_DIR
from pipeline.status import STATUS_FILE
from pipeline.filter import _FILTERED_LOG_PATH

DEFAULT_PORT = 8766
SELECTION_FILE = os.path.join(OUTPUT_DIR, ".filtered_selection.json")
CI_RAW_FILE = os.path.join(OUTPUT_DIR, ".ci_raw_items.json")


# ====== Mini HTTP Server (status + actions) ======
class WidgetHandler(BaseHTTPRequestHandler):
    pipeline_proc = None

    def log_message(self, f, *a):
        pass

    def do_GET(self):
        if self.path == "/status":
            self._serve_json(STATUS_FILE, {"stage":"init","stage_label":"idle","elapsed_seconds":0,"items_so_far":0,"done":False})
        elif self.path == "/schedule":
            self._serve_schedule()
        elif self.path == "/filtered":
            self._serve_filtered()
        elif self.path == "/open-dashboard":
            webbrowser.open("http://127.0.0.1:8766")
            self._json({"ok":True})
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/run-pipeline":
            self._start_pipeline(); self._json({"ok":True})
        elif self.path == "/run-recover":
            self._run_recover()
        elif self.path == "/save-review":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            self._save_selection(data.get("titles",[]))
            self._json({"ok":True})
        else:
            self.send_error(404)

    def _json(self, data, code=200):
        self.send_response(code); self.send_header("Content-Type","application/json"); self.end_headers()
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
        """Same schedule logic as dashboard.py"""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        bj_now = now + timedelta(hours=8)
        iso = bj_now.isocalendar()
        week_label = f"{iso[0]}-W{iso[1]:02d}"

        ci_done = os.path.exists(CI_RAW_FILE)
        st_exists = os.path.exists(STATUS_FILE)
        sel_exists = os.path.exists(SELECTION_FILE)

        local_running = local_done = False
        if st_exists:
            try:
                with open(STATUS_FILE,"r",encoding="utf-8") as f:
                    st = json.load(f)
                local_running = not st.get("done",False)
                local_done = st.get("done",False)
            except: pass

        review_done = review_count = 0
        if sel_exists:
            try:
                with open(SELECTION_FILE,"r",encoding="utf-8") as f:
                    sel = json.load(f)
                review_done = True; review_count = sel.get("count",0)
            except: pass

        weekly_out = False
        try:
            for f in os.listdir(OUTPUT_DIR):
                if f.startswith(week_label) and f.endswith(".md"):
                    weekly_out = True; break
        except: pass

        self._json({
            "ci_done": ci_done,
            "local_running": local_running,
            "local_done": local_done,
            "review_done": review_done,
            "review_count": review_count,
            "weekly_out": weekly_out,
            "week_label": week_label,
            "steps": [
                {"name":"CI","done":ci_done or weekly_out, "running":False, "needs_you":False},
                {"name":"Pipeline","done":local_done or weekly_out, "running":local_running, "needs_you":ci_done and not local_done and not local_running},
                {"name":"Review","done":weekly_out, "running":review_done, "needs_you":local_done and not review_done},
                {"name":"Report","done":weekly_out, "running":False, "needs_you":False},
            ]
        })

    def _serve_filtered(self):
        try:
            if os.path.exists(_FILTERED_LOG_PATH):
                with open(_FILTERED_LOG_PATH,"r",encoding="utf-8") as f:
                    log = json.load(f)
                items = []
                for run in log.get("runs",[]):
                    for it in run.get("items",[]):
                        it["_filtered_at"] = run.get("filtered_at","")
                        it["_filter_name"] = run.get("filter_name","")
                        items.append(it)
            else:
                items = []
            self._json(items)
        except: self.send_error(500)

    def _start_pipeline(self):
        project_dir = os.path.dirname(os.path.abspath(__file__))
        self.__class__.pipeline_proc = subprocess.Popen(
            [sys.executable, "main.py", "--from-ci"],
            cwd=project_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _save_selection(self, titles):
        from pathlib import Path
        Path(OUTPUT_DIR).mkdir(parents=True,exist_ok=True)
        with open(SELECTION_FILE,"w",encoding="utf-8") as f:
            json.dump({"titles":titles,"count":len(titles)},f,ensure_ascii=False,indent=2)

    def _run_recover(self):
        try:
            result = subprocess.run(
                [sys.executable, "main.py", "--recover-reviewed"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                capture_output=True, text=True, timeout=600)
            self._json({"ok":result.returncode==0})
        except: self.send_error(500)


def start_server(port=DEFAULT_PORT):
    server = HTTPServer(("127.0.0.1", port), WidgetHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ====== Floating Tkinter Widget ======

class FloatingWidget:
    """半透明悬浮窗"""

    WIDTH = 280
    HEIGHT = 245
    STAGES = {
        "init": ("IDLE", "#484f58"),
        "load_ci": ("CI", "#58a6ff"),
        "rss_google": ("RSS+", "#58a6ff"),
        "browsers": ("BROWSER", "#bc8cff"),
        "collected": ("COLLECT", "#238636"),
        "processing": ("PROCESS", "#d2991d"),
        "generating": ("GENERATE", "#d2991d"),
        "done": ("DONE", "#238636"),
    }

    def __init__(self, run_pipeline=False):
        self.root = tk.Tk()
        self.root.title("Gaming News")
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}+{sw-self.WIDTH-20}+40")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.88)

        self.bg = "#0d1117"; self.fg = "#c9d1d9"; self.accent = "#58a6ff"
        self.dim = "#8b949e"; self.green = "#238636"; self.yellow = "#d2991d"
        self.root.configure(bg=self.bg)
        self.root.bind("<Button-1>", self._start_drag)
        self.root.bind("<B1-Motion>", self._drag)
        self.root.bind("<Button-3>", self._right_click)

        PADX = 12
        # -- Top bar: stage label + elapsed time --
        top = tk.Frame(self.root, bg=self.bg)
        top.pack(fill=tk.X, padx=PADX, pady=(12,0))
        self.stage_label = tk.Label(top, text="IDLE", font=("Segoe UI", 13, "bold"),
                                     fg=self.accent, bg=self.bg, anchor="w")
        self.stage_label.pack(side=tk.LEFT)
        self.elapsed_label = tk.Label(top, text="00:00", font=("Segoe UI", 10),
                                       fg=self.dim, bg=self.bg)
        self.elapsed_label.pack(side=tk.RIGHT)

        # -- Progress bar --
        self.progress_canvas = tk.Canvas(self.root, width=self.WIDTH-2*PADX, height=4,
                                          bg="#21262d", highlightthickness=0, bd=0)
        self.progress_canvas.pack(pady=(6,10), padx=PADX)
        self.progress_bar = self.progress_canvas.create_rectangle(0, 0, 0, 4, fill=self.accent, width=0)

        # -- 3-line ticker for sample headlines --
        ticker_frame = tk.Frame(self.root, bg="#161b22", bd=0, highlightbackground="#21262d", highlightthickness=1)
        ticker_frame.pack(fill=tk.X, padx=PADX, pady=(0,6))
        self.ticker_lines = []
        for i in range(2):
            clr = self.fg if i == 0 else self.dim
            lbl = tk.Label(ticker_frame, text="", fg=clr, bg="#161b22", anchor="w",
                           justify=tk.LEFT, font=("Microsoft YaHei", 9), wraplength=self.WIDTH-44,
                           height=3)
            lbl.pack(fill=tk.X, ipadx=10, ipady=6)
            self.ticker_lines.append(lbl)

        # -- Sub-stage / item count --
        info_frame = tk.Frame(self.root, bg=self.bg)
        info_frame.pack(fill=tk.X, padx=PADX, pady=(0, 4))
        self.sub_label = tk.Label(info_frame, text="", font=("Segoe UI", 8),
                                   fg=self.dim, bg=self.bg, anchor="w")
        self.sub_label.pack(side=tk.LEFT)
        self.count_label = tk.Label(info_frame, text="", font=("Segoe UI", 8),
                                     fg=self.dim, bg=self.bg, anchor="e")
        self.count_label.pack(side=tk.RIGHT)

        # -- Action button --
        self.action_btn = tk.Label(self.root, text="", font=("Segoe UI", 10, "bold"),
                                    fg="#0d1117", bg=self.accent, padx=14, pady=5,
                                    cursor="hand2")
        self.action_btn.pack(pady=(4, 2))

        # -- Bottom controls --
        bottom_bar = tk.Frame(self.root, bg=self.bg)
        bottom_bar.pack(fill=tk.X, padx=PADX, pady=(0,6))
        tk.Label(bottom_bar, text="-  hide    o  min    x  close", font=("Segoe UI", 7),
                 fg=self.dim, bg=self.bg, cursor="hand2").pack(side=tk.LEFT)
        bottom_bar.bind("<Button-1>", self._title_click)

        # Drag state
        self._drag_x = 0; self._drag_y = 0
        self._ticker = []; self._ticker_idx = 0
        self._seen = set(); self._schedule = {}
        self._ticker_job = None

        self.poll()
        if run_pipeline:
            self.root.after(1000, self._auto_run)

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _drag(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _right_click(self, event):
        menu = tk.Menu(self.root, tearoff=0, bg="#161b22", fg="#c9d1d9",
                        activebackground="#21262d", activeforeground="#f0f6fc")
        menu.add_command(label="Open Dashboard", command=lambda: webbrowser.open("http://127.0.0.1:8766"))
        menu.add_command(label="Refresh", command=self.poll)
        menu.add_separator()
        menu.add_command(label="Close Widget", command=self.root.destroy)
        menu.post(event.x_root, event.y_root)

    def _title_click(self, event):
        # Minimize: just hide temporarily
        x = event.x
        if x < 20:
            self.root.withdraw()
            self.root.after(5000, self.root.deiconify)  # auto-show after 5s
        elif x < 46:
            self.root.iconify()  # minimize
        else:
            self.root.destroy()

    def _auto_run(self):
        # Check if CI is done and pipeline not started
        try:
            resp = json.loads(self._fetch("/schedule") or "{}")
            if resp.get("ci_done") and not resp.get("local_running") and not resp.get("local_done"):
                self._fetch_api("/run-pipeline", method="POST")
        except: pass

    def _fetch(self, path):
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:8766{path}", timeout=2) as r:
                return r.read().decode()
        except: return None

    def _fetch_api(self, path, method="GET"):
        import urllib.request
        try:
            req = urllib.request.Request(f"http://127.0.0.1:8766{path}", method=method)
            urllib.request.urlopen(req, timeout=5)
        except: pass

    def poll(self):
        try:
            s_raw = self._fetch("/status")
            sch_raw = self._fetch("/schedule")
            if s_raw:
                s = json.loads(s_raw)
                self._update(s)
            if sch_raw:
                self._schedule = json.loads(sch_raw)
        except: pass
        self.root.after(2000, self.poll)

    def _update(self, s: dict):
        stage = s.get("stage", "init")
        elapsed = s.get("elapsed_seconds", 0)
        items = s.get("items_so_far", 0)
        done = s.get("done", False)
        samples = s.get("samples", [])
        sub = s.get("sub_stage", "")

        # Stage label (top-left)
        st_info = self.STAGES.get(stage, self.STAGES["init"])
        m, sec = divmod(elapsed, 60)
        self.stage_label.config(text=st_info[0], fg=st_info[1])

        # Elapsed (top-right)
        self.elapsed_label.config(text=f"{m:02d}:{sec:02d}")

        # Progress bar
        stages_order = ["init","load_ci","rss_google","browsers","collected","processing","generating","done"]
        idx = stages_order.index(stage) if stage in stages_order else 0
        pw = self.WIDTH - 32
        pct = 1.0 if done else max(0.05, min(0.95, idx / (len(stages_order)-1)))
        self.progress_canvas.coords(self.progress_bar, 0, 0, pw * pct, 4)
        self.progress_canvas.itemconfig(self.progress_bar, fill=st_info[1] if pct < 1 else self.green)

        # Sub-stage + count
        self.sub_label.config(text=sub or "")
        self.count_label.config(text=f"{items} items" if items else "")

        # Collect new samples
        if samples:
            for t in samples:
                if t and t not in self._seen:
                    self._seen.add(t)
                    self._ticker.insert(0, t)
                    if len(self._ticker) > 30:
                        self._ticker.pop()

        # Start ticker rotation if not already
        if self._ticker and self._ticker_job is None:
            self._next_tick()

        self._update_action(s, done)

    def _next_tick(self):
        """Rotate ticker: line1 shifts up, new item enters at bottom"""
        if not self._ticker:
            self._ticker_job = None
            return
        item = self._ticker[self._ticker_idx % len(self._ticker)]
        self._ticker_idx += 1
        self.ticker_lines[0].config(text=self.ticker_lines[1].cget("text") or "")
        self.ticker_lines[1].config(text=item)
        self._ticker_job = self.root.after(8000, self._next_tick)

    def _update_action(self, s, done):
        sch = self._schedule

        if s.get("error"):
            self._set_action("ERROR", self.accent, None)
        elif done and sch.get("local_done") and not sch.get("review_done") and not sch.get("weekly_out"):
            self._set_action("Review needed", "#d2991d", self._do_review)
        elif sch.get("ci_done") and not sch.get("local_running") and not sch.get("local_done"):
            self._set_action("Run Pipeline", self.green, self._do_pipeline)
        elif sch.get("local_running"):
            self._set_action("Running...", self.accent, None)
        elif sch.get("weekly_out"):
            self._set_action("Weekly Ready  DONE", self.green, self._do_open_report)
        elif not sch.get("ci_done"):
            day = sch.get("week_label", "")
            self._set_action(f"Waiting CI {day}", self.dim, None)
        else:
            self._set_action("", self.dim, None)

    def _set_action(self, text, color, callback):
        self.action_btn.config(text=text, bg=color, fg="#0d1117" if color != self.dim else self.fg)
        if callback:
            self.action_btn.bind("<Button-1>", lambda e: callback())
            self.action_btn.config(cursor="hand2")
        else:
            self.action_btn.unbind("<Button-1>")
            self.action_btn.config(cursor="arrow")

    def _do_pipeline(self):
        self._fetch_api("/run-pipeline", "POST")
        self.action_btn.config(text="Starting...", bg=self.accent)

    def _do_review(self):
        webbrowser.open("http://127.0.0.1:8766")
        self.action_btn.config(text="Review in browser...", bg="#d2991d")

    def _do_open_report(self):
        webbrowser.open("http://127.0.0.1:8766")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    run_pipeline = "--run" in sys.argv
    port = DEFAULT_PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    # Start background server
    server = start_server(port)

    # Start widget
    widget = FloatingWidget(run_pipeline=run_pipeline)
    widget.run()
