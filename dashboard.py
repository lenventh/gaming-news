#!/usr/bin/env python3
"""统一仪表盘 — 管道进度 + 审核 + 回捞 合为一个浏览器页面。

用法:
    python dashboard.py              # 启动仪表盘
    python dashboard.py --run        # 启动仪表盘并自动运行管道

桌面快捷方式指向 run_dashboard.bat
"""

import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from config import OUTPUT_DIR
from pipeline.filter import _FILTERED_LOG_PATH, show_filter_stats
from pipeline.status import STATUS_FILE

DEFAULT_PORT = 8766
SELECTION_FILE = os.path.join(OUTPUT_DIR, ".filtered_selection.json")

# ---------- HTML ----------

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>周刊仪表盘</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "Microsoft YaHei", sans-serif; background: #0d1117; color: #c9d1d9; min-height: 100vh; }
.header { padding: 24px 28px 16px; border-bottom: 1px solid #21262d; }
.header h1 { color: #58a6ff; font-size: 20px; }
.header .sub { color: #8b949e; font-size: 13px; margin-top: 4px; }
.main { padding: 24px 28px; max-width: 900px; margin: 0 auto; }

/* Stepper */
.stepper { display: flex; gap: 0; margin-bottom: 28px; }
.step { flex: 1; text-align: center; padding: 14px 8px; border-bottom: 3px solid #21262d; color: #484f58; font-size: 12px; transition: .3s; }
.step .dot { width: 10px; height: 10px; border-radius: 50%; background: #21262d; margin: 0 auto 8px; transition: .3s; }
.step.active { color: #58a6ff; border-color: #58a6ff; }
.step.active .dot { background: #58a6ff; box-shadow: 0 0 10px #58a6ff; }
.step.done { color: #238636; border-color: #238636; }
.step.done .dot { background: #238636; }
.step.error { color: #f85149; border-color: #f85149; }
.step.error .dot { background: #f85149; }

/* Info cards */
.info-row { display: flex; gap: 14px; margin-bottom: 20px; flex-wrap: wrap; }
.card { flex: 1; min-width: 140px; padding: 16px; border-radius: 10px; border: 1px solid #21262d; background: #161b22; }
.card .val { font-size: 28px; font-weight: 700; color: #f0f6fc; }
.card .lbl { font-size: 12px; color: #8b949e; margin-top: 4px; }
.timer { font-variant-numeric: tabular-nums; }

/* Samples */
.samples { margin-bottom: 20px; }
.samples h3 { font-size: 13px; color: #8b949e; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 1px; }
.sample-item { padding: 8px 12px; border-radius: 6px; background: #161b22; border: 1px solid #21262d; margin-bottom: 5px; font-size: 13px; animation: fadeIn .4s; color: #c9d1d9; word-break: break-all; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }

/* Sources */
.sources { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 20px; }
.src-tag { padding: 3px 10px; border-radius: 12px; font-size: 11px; background: #21262d; color: #8b949e; }
.src-tag .n { color: #f0f6fc; font-weight: 600; }

/* Review section */
.review-section { display: none; margin-top: 10px; }
.review-section.show { display: block; }
.filter-bar { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
.filter-chip { padding: 4px 12px; border-radius: 14px; font-size: 12px; cursor: pointer; border: 1px solid #30363d; background: #161b22; color: #8b949e; transition: .2s; }
.filter-chip:hover, .filter-chip.active { border-color: #58a6ff; color: #58a6ff; }
.review-list { max-height: 60vh; overflow-y: auto; display: flex; flex-direction: column; gap: 5px; }
.review-item { display: flex; align-items: flex-start; gap: 10px; padding: 9px 12px; border-radius: 6px; border: 1px solid #21262d; background: #161b22; cursor: pointer; transition: .15s; font-size: 13px; }
.review-item:hover { border-color: #30363d; }
.review-item.selected { border-color: #238636; background: #0d1f14; }
.review-item input[type=checkbox] { margin-top: 2px; accent-color: #238636; }
.review-item .body { flex:1; min-width:0; }
.review-item .body .t { color: #f0f6fc; }
.review-item .body .m { font-size: 11px; color: #8b949e; margin-top: 2px; }
.review-item .body .m span { margin-right: 10px; }
.review-item .body .s { font-size: 11px; color: #8b949e; margin-top: 2px; word-break: break-all; }

/* Buttons */
.btn-row { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
.btn { padding: 10px 22px; border-radius: 8px; border: none; font-size: 14px; font-weight: 600; cursor: pointer; transition: .2s; }
.btn-primary { background: #1f6feb; color: #fff; }
.btn-primary:hover { background: #388bfd; }
.btn-green { background: #238636; color: #fff; }
.btn-green:hover { background: #2ea043; }
.btn-outline { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; }
.btn-outline:hover { border-color: #58a6ff; }

/* Toast */
.toast { position: fixed; top: 16px; right: 16px; padding: 12px 20px; border-radius: 8px; font-size: 14px; z-index: 99; transition: .3s; opacity: 0; pointer-events: none; }
.toast.show { opacity: 1; }
.toast.ok { background: #238636; }
.toast.err { background: #da3633; }

.spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #30363d; border-top-color: #58a6ff; border-radius: 50%; animation: spin .7s linear infinite; margin-right: 6px; vertical-align: middle; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<div class="header">
  <h1 id="title">周刊仪表盘 <span class="spinner" id="spinner"></span></h1>
  <div class="sub" id="subtitle">等待管道启动...</div>
</div>

<div class="main">
  <div class="stepper" id="stepper"></div>

  <div class="info-row">
    <div class="card"><div class="val timer" id="elapsed">00:00</div><div class="lbl">已用时间</div></div>
    <div class="card"><div class="val" id="itemCount">0</div><div class="lbl">已采集条目</div></div>
    <div class="card"><div class="val" id="stageLabel">待机</div><div class="lbl">当前阶段</div></div>
  </div>

  <div class="sources" id="sources"></div>
  <div class="samples" id="samplesBlock" style="display:none"><h3>最新采集</h3><div id="sampleList"></div></div>

  <div class="btn-row" id="actionBtns" style="display:none"></div>

  <div class="review-section" id="reviewSection">
    <h3 style="margin-bottom:10px">过滤条目审核</h3>
    <div class="filter-bar" id="filterBar"></div>
    <div class="review-list" id="reviewList"></div>
    <div class="btn-row">
      <button class="btn btn-outline" onclick="reviewSelectAll()">全选当前</button>
      <button class="btn btn-outline" onclick="reviewClearAll()">清空</button>
      <button class="btn btn-green" onclick="reviewSave()">保存勾选</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const STAGES = [
  {key:'load_ci', label:'加载 CI 数据'},
  {key:'rss_google', label:'RSS + Google News'},
  {key:'browsers', label:'浏览器采集'},
  {key:'collected', label:'采集完成'},
  {key:'processing', label:'去重+分类'},
  {key:'generating', label:'文稿生成'},
  {key:'done', label:'完成'},
];

let currentStage = '';
let reviewData = [];
let selectedTitles = new Set();

// ====== Poll pipeline status ======
async function poll() {
  try {
    const resp = await fetch('/status');
    const s = await resp.json();

    document.getElementById('elapsed').textContent = fmtTime(s.elapsed_seconds || 0);
    document.getElementById('itemCount').textContent = s.items_so_far || 0;
    document.getElementById('stageLabel').textContent = s.stage_label || s.stage || '...';
    document.getElementById('subtitle').textContent = s.sub_stage || (s.done ? '管道完成' : '运行中...');

    // Stepper
    if (s.stage !== currentStage) {
      currentStage = s.stage;
      renderStepper(s.stage, s.error);
    }

    // Samples
    if (s.samples && s.samples.length) {
      document.getElementById('samplesBlock').style.display = 'block';
      document.getElementById('sampleList').innerHTML = s.samples.map(t =>
        '<div class="sample-item">' + escHtml(t) + '</div>'
      ).join('');
    }

    // Sources
    if (s.sources && Object.keys(s.sources).length) {
      document.getElementById('sources').innerHTML = Object.entries(s.sources)
        .sort((a,b) => b[1]-a[1])
        .map(([k,v]) => '<span class="src-tag">' + k + ' <span class="n">' + v + '</span></span>')
        .join('');
    }

    // Done?
    if (s.done) {
      document.getElementById('spinner').style.display = 'none';
      document.getElementById('actionBtns').style.display = 'flex';
      if (!s.error) showReviewBtn();
    }

  } catch(e) {
    document.getElementById('subtitle').textContent = '等待管道启动...';
  }

  if (!document.getElementById('reviewSection').classList.contains('show')) {
    setTimeout(poll, 1500);
  }
}

function renderStepper(current, error) {
  let found = false;
  const html = STAGES.map((st, i) => {
    let cls = '';
    if (error && st.key === 'done') cls = 'error';
    else if (st.key === current) { cls = 'active'; found = true; }
    else if (!found) cls = 'done';
    return '<div class="step ' + cls + '"><div class="dot"></div>' + st.label + '</div>';
  }).join('');
  document.getElementById('stepper').innerHTML = html;
}

function fmtTime(s) {
  const m = Math.floor(s/60), sec = s%60;
  return String(m).padStart(2,'0') + ':' + String(sec).padStart(2,'0');
}

// ====== Review ======
function showReviewBtn() {
  document.getElementById('actionBtns').innerHTML =
    '<button class="btn btn-primary" onclick="loadReview()">审核过滤条目</button>';
}

async function loadReview() {
  try {
    const resp = await fetch('/filtered');
    reviewData = await resp.json();
    if (!reviewData.length) { toast('没有过滤条目', 'err'); return; }

    // Build filter bar
    const byReason = {};
    reviewData.forEach(it => {
      const r = it.filter_reason || 'unknown';
      byReason[r] = (byReason[r]||0) + 1;
    });
    let chips = '<span class="filter-chip active" data-reason="" onclick="filterReview(\'\',this)">全部 ' + reviewData.length + '</span>';
    Object.entries(byReason).sort((a,b)=>b[1]-a[1]).forEach(([r,c]) => {
      chips += '<span class="filter-chip" data-reason="'+r+'" onclick="filterReview(\''+r+'\',this)">'+r+' '+c+'</span>';
    });
    document.getElementById('filterBar').innerHTML = chips;

    selectedTitles.clear();
    renderReviewItems(reviewData);
    document.getElementById('reviewSection').classList.add('show');
    document.getElementById('actionBtns').style.display = 'none';
    document.getElementById('spinner').style.display = 'none';
  } catch(e) { toast('加载过滤数据失败', 'err'); }
}

function filterReview(reason, chip) {
  document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
  if (chip) chip.classList.add('active');
  const filtered = reason ? reviewData.filter(it => it.filter_reason === reason) : reviewData;
  renderReviewItems(filtered);
}

function renderReviewItems(items) {
  document.getElementById('reviewList').innerHTML = items.map(it => {
    const sel = selectedTitles.has(it.title);
    return '<div class="review-item' + (sel?' selected':'') + '" onclick="toggleReview(\'' + escAttr(it.title) + '\', this)">'
      + '<input type="checkbox" ' + (sel?'checked':'') + ' onclick="event.stopPropagation(); toggleReview(\'' + escAttr(it.title) + '\', this.parentElement)">'
      + '<div class="body"><div class="t">' + escHtml(it.title || '无标题') + '</div>'
      + '<div class="m"><span>' + escHtml(it.source_type||'') + '</span><span>' + escHtml(it.filter_reason||'') + '</span></div>'
      + (it.summary ? '<div class="s">' + escHtml(it.summary) + '</div>' : '')
      + '</div></div>';
  }).join('');
}

function toggleReview(title, el) {
  if (selectedTitles.has(title)) { selectedTitles.delete(title); el.classList.remove('selected'); }
  else { selectedTitles.add(title); el.classList.add('selected'); }
}

function reviewSelectAll() {
  const visible = document.querySelectorAll('.review-item');
  visible.forEach(el => {
    const title = el.querySelector('.t')?.textContent || '';
    selectedTitles.add(title);
    el.classList.add('selected');
    el.querySelector('input[type=checkbox]').checked = true;
  });
}

function reviewClearAll() { selectedTitles.clear(); renderReviewItems(reviewData); }

async function reviewSave() {
  try {
    const resp = await fetch('/save-review', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({titles: [...selectedTitles]})
    });
    if (resp.ok) {
      toast('已保存 ' + selectedTitles.size + ' 条勾选。运行回捞...', 'ok');
      // Show recover button
      document.getElementById('reviewSection').classList.remove('show');
      document.getElementById('actionBtns').style.display = 'flex';
      document.getElementById('actionBtns').innerHTML =
        '<button class="btn btn-green" onclick="runRecover()">回捞并重新生成周刊</button>';
    } else {
      toast('保存失败', 'err');
    }
  } catch(e) { toast('保存出错', 'err'); }
}

async function runRecover() {
  document.getElementById('actionBtns').innerHTML = '<button class="btn btn-primary" disabled><span class="spinner"></span>回捞中...</button>';
  try {
    const resp = await fetch('/run-recover', {method:'POST'});
    if (resp.ok) {
      toast('回捞完成! 周刊已更新', 'ok');
      document.getElementById('actionBtns').innerHTML = '<button class="btn btn-outline" onclick="location.reload()">刷新</button>';
    } else {
      toast('回捞失败: ' + (await resp.text()), 'err');
    }
  } catch(e) { toast('回捞出错', 'err'); }
}

// ====== Helpers ======
function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function escAttr(s) { return String(s).replace(/'/g,"\\'").replace(/"/g,'&quot;'); }
function toast(msg, type='ok') {
  const el = document.getElementById('toast');
  el.textContent = msg; el.className = 'toast show ' + type;
  setTimeout(() => el.className = 'toast', 2500);
}

// Start
renderStepper('init');
poll();
</script>
</body>
</html>"""


# ---------- HTTP Handlers ----------

class DashboardHandler(BaseHTTPRequestHandler):
    pipeline_process: subprocess.Popen | None = None

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._serve_html(PAGE)
        elif self.path == "/status":
            self._serve_status()
        elif self.path == "/filtered":
            self._serve_filtered()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/save-review":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            self._save_selection(data.get("titles", []))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        elif self.path == "/run-recover":
            self._run_recover()
        else:
            self.send_error(404)

    def _serve_html(self, html: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _serve_status(self):
        try:
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE, "r", encoding="utf-8") as f:
                    content = f.read()
            else:
                content = '{"stage":"init","stage_label":"等待中","elapsed_seconds":0,"items_so_far":0,"samples":[],"sources":{},"done":false}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
        except Exception:
            self.send_error(500)

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
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(items, ensure_ascii=False).encode("utf-8"))
        except Exception:
            self.send_error(500)

    def _save_selection(self, titles: list[str]):
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
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            else:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(result.stderr[:500].encode())
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())


def start_server(run_pipeline: bool = False, port: int = DEFAULT_PORT):
    """启动仪表盘服务器"""
    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    url = f"http://127.0.0.1:{port}"

    print(f"\n{'='*55}")
    print(f"  周刊仪表盘")
    print(f"  浏览器: {url}")
    if run_pipeline:
        print(f"  自动运行管道...")
    print(f"{'='*55}\n")

    # 如果在管道中运行，启动管道子进程
    if run_pipeline:
        project_dir = os.path.dirname(os.path.abspath(__file__))
        DashboardHandler.pipeline_process = subprocess.Popen(
            [sys.executable, "main.py", "--from-ci"],
            cwd=project_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n仪表盘已关闭")


if __name__ == "__main__":
    run = "--run" in sys.argv
    port = DEFAULT_PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])
    start_server(run_pipeline=run, port=port)
