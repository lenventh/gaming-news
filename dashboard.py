#!/usr/bin/env python3
"""桌面悬浮仪表盘 — 周刊全流程进度可视化 + 交互操作。

用法:
    python dashboard.py              # 启动仪表盘
    python dashboard.py --run        # 启动并自动运行管道

开机自启: python setup_autostart.py
"""

import json
import os
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from config import OUTPUT_DIR
from pipeline.filter import _FILTERED_LOG_PATH
from pipeline.status import STATUS_FILE

DEFAULT_PORT = 8766
SELECTION_FILE = os.path.join(OUTPUT_DIR, ".filtered_selection.json")
CI_RAW_FILE = os.path.join(OUTPUT_DIR, ".ci_raw_items.json")

# ---------- HTML ----------

PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gaming News Dashboard</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Segoe UI", "Microsoft YaHei", sans-serif; background: #0d1117; color: #c9d1d9; overflow-x: hidden; }

/* ---- Mini Header ---- */
.topbar { display:flex; align-items:center; justify-content:space-between; padding:10px 18px; border-bottom:1px solid #21262d; background:#161b22; }
.topbar .title { font-size:15px; font-weight:600; color:#f0f6fc; }
.topbar .week { font-size:12px; color:#8b949e; background:#21262d; padding:3px 10px; border-radius:10px; }
.topbar .dot { width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:6px; }
.topbar .dot.idle { background:#484f58; }
.topbar .dot.running { background:#58a6ff; animation:pulse 1.5s infinite; }
.topbar .dot.done { background:#238636; }
.topbar .dot.action { background:#d2991d; animation:pulse 1s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }

/* ---- Main ---- */
.main { padding:14px 18px; max-width:800px; margin:0 auto; }

/* Progress bar */
.progress-section { margin-bottom:16px; }
.progress-bar { height:6px; border-radius:3px; background:#21262d; margin:8px 0 4px; overflow:hidden; }
.progress-fill { height:100%; border-radius:3px; background:linear-gradient(90deg,#1f6feb,#58a6ff); transition:width .6s; }
.progress-steps { display:flex; justify-content:space-between; font-size:10px; color:#8b949e; }
.progress-steps span { text-align:center; }
.progress-steps .done { color:#238636; }
.progress-steps .current { color:#58a6ff; font-weight:600; }
.progress-steps .waiting { color:#484f58; }

/* Cards row */
.cards { display:flex; gap:10px; margin-bottom:14px; flex-wrap:wrap; }
.card { flex:1; min-width:80px; padding:12px; border-radius:8px; border:1px solid #21262d; background:#161b22; text-align:center; }
.card .val { font-size:22px; font-weight:700; color:#f0f6fc; }
.card .lbl { font-size:10px; color:#8b949e; margin-top:2px; text-transform:uppercase; letter-spacing:.5px; }

/* Live ticker */
.ticker { margin-bottom:14px; border:1px solid #21262d; border-radius:8px; background:#161b22; overflow:hidden; max-height:140px; }
.ticker-header { padding:6px 12px; font-size:10px; color:#8b949e; text-transform:uppercase; letter-spacing:1px; border-bottom:1px solid #21262d; display:flex; align-items:center; gap:6px; }
.ticker-header .live-dot { width:6px; height:6px; border-radius:50%; background:#f85149; animation:pulse .8s infinite; }
.ticker-body { padding:6px 0; font-size:11px; max-height:110px; overflow-y:auto; }
.ticker-line { padding:4px 12px; color:#8b949e; border-left:2px solid transparent; animation:fadeIn .3s; }
.ticker-line:nth-child(odd) { background:rgba(255,255,255,.01); }
.ticker-line .ts { color:#484f58; margin-right:8px; font-size:10px; }
.ticker-line .src { color:#58a6ff; margin-right:6px; }
@keyframes fadeIn { from{opacity:0;transform:translateX(-8px)} to{opacity:1;transform:translateX(0)} }

/* Schedule timeline */
.schedule { margin-bottom:14px; }
.schedule h3 { font-size:11px; color:#8b949e; text-transform:uppercase; letter-spacing:1px; margin-bottom:8px; }
.sched-item { display:flex; align-items:center; padding:8px 10px; border-radius:6px; border:1px solid #21262d; background:#161b22; margin-bottom:4px; font-size:12px; gap:10px; }
.sched-item .icon { width:20px; text-align:center; font-size:13px; flex-shrink:0; }
.sched-item .info { flex:1; }
.sched-item .info .name { color:#f0f6fc; }
.sched-item .info .detail { color:#8b949e; font-size:10px; margin-top:1px; }
.sched-item .status { font-size:10px; padding:2px 8px; border-radius:8px; flex-shrink:0; }
.sched-item .status.ok { background:#0d1f14; color:#238636; }
.sched-item .status.pending { background:#21262d; color:#8b949e; }
.sched-item .status.running { background:#0d1a33; color:#58a6ff; }
.sched-item .status.needs-you { background:#3d2800; color:#d2991d; }

/* Action bar */
.action-bar { padding:12px 16px; border-radius:10px; border:1px solid #d2991d; background:#1a1206; margin-bottom:14px; display:none; align-items:center; gap:12px; }
.action-bar.show { display:flex; }
.action-bar .msg { flex:1; font-size:13px; color:#d2991d; }
.action-bar .msg strong { color:#f0f6fc; }
.btn { padding:8px 18px; border-radius:7px; border:none; font-size:13px; font-weight:600; cursor:pointer; transition:.2s; white-space:nowrap; }
.btn-primary { background:#1f6feb; color:#fff; }
.btn-primary:hover { background:#388bfd; }
.btn-green { background:#238636; color:#fff; }
.btn-green:hover { background:#2ea043; }
.btn-yellow { background:#9e6a03; color:#fff; }
.btn-yellow:hover { background:#bb8000; }
.btn-outline { background:transparent; color:#8b949e; border:1px solid #30363d; }
.btn-outline:hover { border-color:#58a6ff; color:#58a6ff; }

/* Review panel (inline) */
.review-panel { display:none; margin-top:10px; }
.review-panel.show { display:block; }
.review-header { display:flex; gap:8px; align-items:center; margin-bottom:10px; flex-wrap:wrap; }
.review-header .chip { padding:3px 10px; border-radius:10px; font-size:11px; cursor:pointer; border:1px solid #30363d; background:#161b22; color:#8b949e; transition:.2s; }
.review-header .chip:hover,.review-header .chip.on { border-color:#58a6ff; color:#58a6ff; }
.review-header .chip .n { opacity:.7; }
.review-items { max-height:55vh; overflow-y:auto; display:flex; flex-direction:column; gap:4px; }
.review-item { display:flex; align-items:flex-start; gap:8px; padding:8px 10px; border-radius:6px; border:1px solid #21262d; background:#161b22; cursor:pointer; font-size:12px; transition:.15s; }
.review-item:hover { border-color:#30363d; }
.review-item.sel { border-color:#238636; background:#0d1f14; }
.review-item input[type=checkbox] { margin-top:2px; accent-color:#238636; }
.review-item .ri-body { flex:1; min-width:0; }
.review-item .ri-body .ri-t { color:#f0f6fc; word-break:break-all; }
.review-item .ri-body .ri-m { font-size:10px; color:#8b949e; margin-top:2px; }
.review-item .ri-body .ri-m span { margin-right:8px; }

.toast { position:fixed; top:12px; right:12px; padding:10px 18px; border-radius:8px; font-size:13px; z-index:99; transition:.3s; opacity:0; pointer-events:none; }
.toast.show { opacity:1; }
.toast.ok { background:#238636; }
.toast.err { background:#da3633; color:#fff; }

.hidden { display:none !important; }
</style>
</head>
<body>

<div class="topbar">
  <div><span class="dot idle" id="statusDot"></span><span class="title">Gaming News Dashboard</span></div>
  <span class="week" id="weekLabel">--</span>
</div>

<div class="main">

  <!-- Progress stepper -->
  <div class="progress-section">
    <div class="progress-bar"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
    <div class="progress-steps" id="progressSteps"></div>
  </div>

  <!-- Cards -->
  <div class="cards">
    <div class="card"><div class="val" id="elapsed">--:--</div><div class="lbl">elapsed</div></div>
    <div class="card"><div class="val" id="itemCount">0</div><div class="lbl">collected</div></div>
    <div class="card"><div class="val" id="stageLabel">idle</div><div class="lbl">stage</div></div>
  </div>

  <!-- Live ticker -->
  <div class="ticker" id="tickerBlock">
    <div class="ticker-header"><span class="live-dot"></span> live monitor</div>
    <div class="ticker-body" id="tickerBody"></div>
  </div>

  <!-- Schedule vs reality -->
  <div class="schedule" id="schedule"></div>

  <!-- Action bar -->
  <div class="action-bar" id="actionBar">
    <div class="msg" id="actionMsg"></div>
    <button class="btn btn-yellow" id="actionBtn" onclick="handleAction()">Run</button>
  </div>

  <!-- Review panel (hidden until opened) -->
  <div class="review-panel" id="reviewPanel">
    <h3 style="font-size:13px;color:#f0f6fc;margin-bottom:8px">Review filtered items</h3>
    <div class="review-header" id="reviewFilters"></div>
    <div class="review-items" id="reviewList"></div>
    <div style="display:flex;gap:8px;margin-top:10px;">
      <button class="btn btn-outline" onclick="reviewSelectAll()">Select all</button>
      <button class="btn btn-outline" onclick="reviewClear()">Clear</button>
      <button class="btn btn-green" onclick="reviewSave()">Save & Recover</button>
    </div>
  </div>

</div>

<div class="toast" id="toast"></div>

<script>
// ===== State =====
const STAGES = ['load_ci', 'rss_google', 'browsers', 'collected', 'processing', 'generating', 'done'];
const STAGE_LABELS = {load_ci:'CI Data', rss_google:'RSS+News', browsers:'Browser', collected:'Collected', processing:'Process', generating:'Generate', done:'Done'};
let reviewData = [];
let selectedTitles = new Set();
let currentAction = null;
let tickerLines = [];
let samplesSeen = new Set();

// ===== Poll status =====
setInterval(poll, 2000);
poll();

async function poll() {
  try {
    const [sResp, schResp] = await Promise.all([
      fetch('/status'),
      fetch('/schedule'),
    ]);
    const s = await sResp.json();
    const sch = await schResp.json();

    updateProgress(s);
    updateCards(s);
    updateTicker(s);
    updateSchedule(sch);
    updateActionBar(s, sch);
    updateStatusDot(s);

    document.getElementById('weekLabel').textContent = sch.week_label || '';
  } catch(e) { /* server starting */ }
}

// ===== Progress bar =====
function updateProgress(s) {
  const idx = STAGES.indexOf(s.stage);
  const pct = s.done ? 100 : Math.max(0, Math.min(95, (idx / (STAGES.length-1)) * 100));
  document.getElementById('progressFill').style.width = pct + '%';

  const steps = STAGES.map(st => {
    let cls = 'waiting';
    const si = STAGES.indexOf(st);
    const ci = STAGES.indexOf(s.stage);
    if (s.done || si < ci) cls = 'done';
    else if (si === ci) cls = 'current';
    return '<span class="' + cls + '">' + (STAGE_LABELS[st] || st) + '</span>';
  }).join('');
  document.getElementById('progressSteps').innerHTML = steps;
}

function updateCards(s) {
  document.getElementById('elapsed').textContent = fmtTime(s.elapsed_seconds || 0);
  document.getElementById('itemCount').textContent = s.items_so_far || 0;
  document.getElementById('stageLabel').textContent = s.stage_label || (s.done ? 'done' : 'idle');
}

function updateStatusDot(s) {
  const dot = document.getElementById('statusDot');
  dot.className = 'dot';
  if (s.done) dot.classList.add('done');
  else if (s.stage && s.stage !== 'init') dot.classList.add('running');
  else dot.classList.add('idle');
}

// ===== Live ticker =====
function updateTicker(s) {
  if (s.samples && s.samples.length) {
    const now = new Date().toLocaleTimeString();
    s.samples.forEach(t => {
      if (!t || samplesSeen.has(t)) return;
      samplesSeen.add(t);
      tickerLines.unshift({ts: now, src: s.stage_label || '', text: t});
      if (tickerLines.length > 50) tickerLines.length = 50;
    });
  }
  // Also add stage change events
  if (s.sub_stage && tickerLines[0]?.text !== s.sub_stage) {
    const now = new Date().toLocaleTimeString();
    tickerLines.unshift({ts:now, src:'system', text:'>>> ' + s.sub_stage});
    if (tickerLines.length > 50) tickerLines.length = 50;
  }

  const html = tickerLines.slice(0, 20).map(l =>
    '<div class="ticker-line"><span class="ts">' + l.ts + '</span><span class="src">[' + l.src + ']</span>' + escHtml(l.text) + '</div>'
  ).join('');
  document.getElementById('tickerBody').innerHTML = html || '<div class="ticker-line">waiting for data...</div>';
}

// ===== Schedule timeline =====
function updateSchedule(sch) {
  if (!sch || !sch.steps) return;
  const html = sch.steps.map(step => {
    let icon = step.done ? '&#x2705;' : step.running ? '&#x23F3;' : step.needs_you ? '&#x26A1;' : '&#x23F0;';
    let statusCls = step.done ? 'ok' : step.running ? 'running' : step.needs_you ? 'needs-you' : 'pending';
    let statusText = step.done ? 'done' : step.running ? 'running...' : step.needs_you ? 'needs you' : 'waiting';
    return '<div class="sched-item">'
      + '<span class="icon">' + icon + '</span>'
      + '<div class="info"><div class="name">' + escHtml(step.name) + '</div>'
      + '<div class="detail">' + escHtml(step.detail || '') + '</div></div>'
      + '<span class="status ' + statusCls + '">' + statusText + '</span>'
      + '</div>';
  }).join('');
  document.getElementById('schedule').innerHTML = '<h3>this week</h3>' + html;
}

// ===== Action bar =====
function updateActionBar(s, sch) {
  const bar = document.getElementById('actionBar');
  const msg = document.getElementById('actionMsg');
  const btn = document.getElementById('actionBtn');

  // Pipeline done, review not done
  if (s.done && !sch.review_done && !document.getElementById('reviewPanel').classList.contains('show')) {
    bar.classList.add('show');
    msg.innerHTML = '<strong>Pipeline complete.</strong> Review filtered items?';
    btn.textContent = 'Start Review';
    btn.className = 'btn btn-yellow';
    currentAction = 'review';
    return;
  }

  // CI data ready, local pipeline not started
  if (sch.ci_done && !sch.local_started && !s.done && (!s.stage || s.stage === 'init')) {
    bar.classList.add('show');
    msg.innerHTML = '<strong>CI data ready.</strong> Run local pipeline?';
    btn.textContent = 'Run Pipeline';
    btn.className = 'btn btn-primary';
    currentAction = 'pipeline';
    return;
  }

  // Review saved, ready to recover
  if (sch.review_selected && !sch.local_running) {
    bar.classList.add('show');
    msg.innerHTML = '<strong>' + sch.review_count + ' items selected.</strong> Recover and regenerate?';
    btn.textContent = 'Recover & Generate';
    btn.className = 'btn btn-green';
    currentAction = 'recover';
    return;
  }

  // Recover complete
  if (sch.recover_done && s.done) {
    bar.classList.add('show');
    msg.innerHTML = 'Weekly report is ready.';
    btn.textContent = 'View Report';
    btn.className = 'btn btn-outline';
    currentAction = 'view';
    return;
  }

  // Nothing to do
  if (!sch.ci_done) {
    bar.classList.add('show');
    msg.innerHTML = 'Waiting for CI to finish (Thu/Fri 05:00 Beijing). Check back later.';
    btn.className = 'btn btn-outline';
    btn.textContent = 'Refresh';
    currentAction = 'refresh';
    return;
  }

  bar.classList.remove('show');
  currentAction = null;
}

function handleAction() {
  switch(currentAction) {
    case 'pipeline':
      fetch('/run-pipeline', {method:'POST'});
      document.getElementById('actionBar').classList.remove('show');
      toast('Pipeline starting...', 'ok');
      break;
    case 'review':
      loadReview();
      break;
    case 'recover':
      runRecover();
      break;
    case 'view':
      window.open('/open-report', '_blank');
      break;
    case 'refresh':
      location.reload();
      break;
  }
}

// ===== Review =====
async function loadReview() {
  try {
    const resp = await fetch('/filtered');
    reviewData = await resp.json();
    if (!reviewData.length) { toast('No filtered items', 'err'); return; }

    selectedTitles.clear();
    document.getElementById('actionBar').classList.remove('show');
    document.getElementById('reviewPanel').classList.add('show');

    // Build filter chips
    const byReason = {};
    reviewData.forEach(it => { const r = it.filter_reason || 'unknown'; byReason[r] = (byReason[r]||0)+1; });
    let chips = '<span class="chip on" data-r="" onclick="filterReview(\'\',this)">all ' + reviewData.length + '</span>';
    Object.entries(byReason).sort((a,b)=>b[1]-a[1]).forEach(([r,c]) => {
      chips += '<span class="chip" data-r="'+r+'" onclick="filterReview(\''+r+'\',this)">'+r+' <span class="n">'+c+'</span></span>';
    });
    document.getElementById('reviewFilters').innerHTML = chips;
    renderReviewItems(reviewData);
    document.getElementById('reviewPanel').scrollIntoView({behavior:'smooth'});
  } catch(e) { toast('Failed to load', 'err'); }
}

function filterReview(reason, chip) {
  document.querySelectorAll('#reviewFilters .chip').forEach(c => c.classList.remove('on'));
  if (chip) chip.classList.add('on');
  renderReviewItems(reason ? reviewData.filter(it => it.filter_reason === reason) : reviewData);
}

function renderReviewItems(items) {
  document.getElementById('reviewList').innerHTML = items.map(it => {
    const sel = selectedTitles.has(it.title);
    return '<div class="review-item' + (sel?' sel':'') + '" onclick="toggleReview(\'' + escAttr(it.title) + '\', this)">'
      + '<input type="checkbox" ' + (sel?'checked':'') + ' onclick="event.stopPropagation(); toggleReview(\'' + escAttr(it.title) + '\', this.parentElement)">'
      + '<div class="ri-body"><div class="ri-t">' + escHtml(it.title || 'no title') + '</div>'
      + '<div class="ri-m"><span>' + escHtml(it.source_type||'') + '</span><span>' + escHtml(it.filter_reason||'') + '</span></div>'
      + '</div></div>';
  }).join('');
}

function toggleReview(title, el) {
  if (selectedTitles.has(title)) { selectedTitles.delete(title); el.classList.remove('sel'); }
  else { selectedTitles.add(title); el.classList.add('sel'); }
}

function reviewSelectAll() {
  document.querySelectorAll('.review-item').forEach(el => {
    const t = el.querySelector('.ri-t')?.textContent || '';
    selectedTitles.add(t); el.classList.add('sel');
    el.querySelector('input[type=checkbox]').checked = true;
  });
}

function reviewClear() { selectedTitles.clear(); renderReviewItems(reviewData); }

async function reviewSave() {
  try {
    const resp = await fetch('/save-review', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({titles:[...selectedTitles]})
    });
    if (resp.ok) {
      document.getElementById('reviewPanel').classList.remove('show');
      toast('Saved ' + selectedTitles.size + ' items', 'ok');
      currentAction = 'recover';
      document.getElementById('actionBar').classList.add('show');
      document.getElementById('actionMsg').innerHTML = '<strong>' + selectedTitles.size + ' items selected.</strong> Recover and regenerate?';
      document.getElementById('actionBtn').textContent = 'Recover & Generate';
      document.getElementById('actionBtn').className = 'btn btn-green';
    }
  } catch(e) { toast('Save failed', 'err'); }
}

async function runRecover() {
  try {
    document.getElementById('actionBar').classList.remove('show');
    document.getElementById('progressFill').style.width = '90%';
    const resp = await fetch('/run-recover', {method:'POST'});
    if (resp.ok) {
      toast('Recover complete!', 'ok');
      setTimeout(poll, 1000);
    } else {
      toast('Recover failed', 'err');
    }
  } catch(e) { toast('Error', 'err'); }
}

// ===== Helpers =====
function fmtTime(s) { const m=Math.floor(s/60), sec=s%60; return String(m).padStart(2,'0')+':'+String(sec).padStart(2,'0'); }
function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function escAttr(s) { return String(s).replace(/'/g,"\\'").replace(/"/g,'&quot;'); }
function toast(msg,type) {
  const el=document.getElementById('toast');
  el.textContent=msg; el.className='toast show '+(type||'ok');
  setTimeout(()=>el.className='toast',2500);
}
</script>
</body>
</html>"""


# ---------- Backend ----------

class DashboardHandler(BaseHTTPRequestHandler):
    pipeline_proc: subprocess.Popen | None = None

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._html(PAGE)
        elif self.path == "/status":
            self._json_file(STATUS_FILE, {"stage":"init","stage_label":"idle","elapsed_seconds":0,"items_so_far":0,"samples":[],"sources":{},"done":False})
        elif self.path == "/schedule":
            self._serve_schedule()
        elif self.path == "/filtered":
            self._serve_filtered()
        elif self.path == "/open-report":
            self._open_report()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/run-pipeline":
            self._start_pipeline()
        elif self.path == "/save-review":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            self._save_selection(data.get("titles", []))
            self._json({"ok": True})
        elif self.path == "/run-recover":
            self._run_recover()
        else:
            self.send_error(404)

    def _html(self, content):
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def _json(self, data):
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _json_file(self, path, default):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            else:
                content = json.dumps(default, ensure_ascii=False)
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(content.encode("utf-8"))
        except Exception:
            self.send_error(500)

    def _serve_schedule(self):
        """Build schedule: what should be done vs what is done."""
        now = datetime.now(timezone.utc)
        bj_now = now + timedelta(hours=8)
        wd = bj_now.weekday()  # 0=Mon ... 6=Sun
        iso = bj_now.isocalendar()
        week_label = f"{iso[0]}-W{iso[1]:02d}"

        # Check file states
        ci_done = os.path.exists(CI_RAW_FILE)
        status_exists = os.path.exists(STATUS_FILE)
        review_sel_exists = os.path.exists(SELECTION_FILE)

        # Parse status if exists
        local_running = False
        local_done = False
        if status_exists:
            try:
                with open(STATUS_FILE, "r", encoding="utf-8") as f:
                    st = json.load(f)
                local_running = not st.get("done", False)
                local_done = st.get("done", False)
            except Exception:
                pass

        # Parse selection
        review_done = False
        review_count = 0
        if review_sel_exists:
            try:
                with open(SELECTION_FILE, "r", encoding="utf-8") as f:
                    sel = json.load(f)
                review_done = True
                review_count = sel.get("count", 0)
            except Exception:
                pass

        # Check if recover was run (selection file newer than pipeline status)
        recover_done = False
        if review_done and local_done:
            try:
                sel_mtime = os.path.getmtime(SELECTION_FILE)
                st_mtime = os.path.getmtime(STATUS_FILE)
                recover_done = sel_mtime < st_mtime  # status updated after selection
            except Exception:
                pass

        # Check if any weekly output exists this week
        weekly_out = False
        try:
            for f in os.listdir(OUTPUT_DIR):
                if f.endswith(".md") and (f.startswith(week_label) or f.startswith("2026-W")):
                    weekly_out = True
                    break
        except Exception:
            pass

        # --- Build steps ---
        # Determine schedule based on day of week
        is_report_day = wd >= 3  # Thu-Sun = report window open

        steps = [
            {
                "name": "CI Collection",
                "detail": "RSS + Google News (auto, Thu/Fri 05:00 Beijing)",
                "done": ci_done or weekly_out,
                "running": False,
                "needs_you": False,
            },
            {
                "name": "Local Pipeline",
                "detail": "Browser collectors + process + generate",
                "done": local_done or weekly_out,
                "running": local_running,
                "needs_you": ci_done and not local_done and not local_running,
            },
            {
                "name": "Review & Recover",
                "detail": "Check filtered items, recover false positives",
                "done": recover_done or weekly_out,
                "running": review_done and not recover_done,
                "needs_you": local_done and not review_done,
            },
            {
                "name": "Weekly Report",
                "detail": f"output/{week_label}-*.md",
                "done": weekly_out,
                "running": False,
                "needs_you": False,
            },
        ]

        self._json({
            "week_label": week_label,
            "ci_done": ci_done,
            "local_started": status_exists,
            "local_running": local_running,
            "review_done": review_done,
            "review_selected": review_done,
            "review_count": review_count,
            "recover_done": recover_done,
            "weekly_out": weekly_out,
            "is_report_day": is_report_day,
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
            cwd=project_dir,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._json({"ok": True, "msg": "Pipeline started"})

    def _save_selection(self, titles):
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        with open(SELECTION_FILE, "w", encoding="utf-8") as f:
            json.dump({"titles": titles, "count": len(titles),
                       "saved_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")},
                      f, ensure_ascii=False, indent=2)

    def _run_recover(self):
        try:
            result = subprocess.run(
                [sys.executable, "main.py", "--recover-reviewed"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode == 0:
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": result.stderr[-300:]}, 500)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)

    def _open_report(self):
        try:
            for f in sorted(os.listdir(OUTPUT_DIR), reverse=True):
                if f.endswith(".md"):
                    path = os.path.join(OUTPUT_DIR, f)
                    self._json({"path": path})
                    return
            self._json({"path": None}, 404)
        except Exception:
            self.send_error(500)


def start_server(run_pipeline: bool = False, port: int = DEFAULT_PORT):
    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    url = f"http://127.0.0.1:{port}"

    print(f"\nDashboard: {url}")

    if run_pipeline:
        project_dir = os.path.dirname(os.path.abspath(__file__))
        DashboardHandler.pipeline_proc = subprocess.Popen(
            [sys.executable, "main.py", "--from-ci"],
            cwd=project_dir,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print("Pipeline started in background")

    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard closed")


if __name__ == "__main__":
    run = "--run" in sys.argv
    port = DEFAULT_PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])
    start_server(run_pipeline=run, port=port)
