#!/usr/bin/env python3
"""过滤条目可视化审核工具 — 浏览器页面打勾审核。

用法:
    python review_filtered.py          # 启动审核页面
    python review_filtered.py --stats  # 仅显示过滤统计
    python review_filtered.py --port 8765  # 指定端口

审核流程:
    1. python review_filtered.py
    2. 浏览器中打勾勾选误踢条目
    3. 点击底部 "Save & Close" 按钮
    4. python main.py --recover-reviewed
"""

import json
import os
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from config import OUTPUT_DIR
from pipeline.filter import _FILTERED_LOG_PATH

# 旧的 Markdown 审核文件（保留兼容）
REVIEW_FILE = os.path.join(OUTPUT_DIR, ".filtered_review.md")
# 新的 JSON 勾选结果
SELECTION_FILE = os.path.join(OUTPUT_DIR, ".filtered_selection.json")

DEFAULT_PORT = 8765

# ---------- HTML 页面 ----------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>过滤条目审核</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "Microsoft YaHei", sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }
h1 { color: #58a6ff; margin-bottom: 5px; }
.subtitle { color: #8b949e; font-size: 14px; margin-bottom: 20px; }
.stats { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }
.stat-chip { padding: 4px 12px; border-radius: 16px; font-size: 13px; cursor: pointer; border: 1px solid #30363d; background: #161b22; color: #c9d1d9; transition: .2s; }
.stat-chip:hover { border-color: #58a6ff; }
.stat-chip.active { background: #1f6feb; border-color: #1f6feb; color: #fff; }
.stat-chip .count { font-weight: bold; margin-left: 4px; opacity: .7; }
.actions { display: flex; gap: 10px; margin-bottom: 16px; align-items: center; }
.actions label { font-size: 13px; color: #8b949e; cursor: pointer; user-select: none; }
.actions input[type=text] { padding: 6px 10px; border-radius: 6px; border: 1px solid #30363d; background: #0d1117; color: #c9d1d9; font-size: 13px; width: 200px; }
.actions input[type=text]:focus { border-color: #58a6ff; outline: none; }
.item-list { display: flex; flex-direction: column; gap: 6px; }
.item { display: flex; align-items: flex-start; gap: 10px; padding: 10px 12px; border-radius: 8px; border: 1px solid #21262d; background: #161b22; transition: .15s; }
.item:hover { border-color: #30363d; }
.item.checked { border-color: #238636; background: #0d1f14; }
.item input[type=checkbox] { margin-top: 3px; width: 16px; height: 16px; accent-color: #238636; cursor: pointer; flex-shrink: 0; }
.item-body { flex: 1; min-width: 0; }
.item-title { font-size: 14px; font-weight: 500; color: #f0f6fc; word-break: break-all; }
.item-title a { color: inherit; text-decoration: none; }
.item-title a:hover { color: #58a6ff; }
.item-meta { font-size: 12px; color: #8b949e; margin-top: 3px; display: flex; gap: 12px; flex-wrap: wrap; }
.item-meta .tag { padding: 1px 6px; border-radius: 4px; font-size: 11px; background: #21262d; }
.item-meta .tag.reason { background: #3d2800; color: #d2991d; }
.item-summary { font-size: 12px; color: #8b949e; margin-top: 4px; line-height: 1.4; word-break: break-all; }
.bottom-bar { position: sticky; bottom: 0; margin-top: 24px; padding: 16px; border-radius: 10px; background: #161b22; border: 1px solid #30363d; display: flex; gap: 12px; align-items: center; justify-content: space-between; }
.bottom-bar .count { font-size: 14px; color: #8b949e; }
.bottom-bar .count strong { color: #f0f6fc; }
.btn { padding: 10px 24px; border-radius: 8px; border: none; font-size: 14px; font-weight: 600; cursor: pointer; transition: .2s; }
.btn-save { background: #238636; color: #fff; }
.btn-save:hover { background: #2ea043; }
.btn-reset { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; }
.btn-reset:hover { border-color: #f85149; color: #f85149; }
.toast { position: fixed; top: 16px; right: 16px; padding: 12px 20px; border-radius: 8px; font-size: 14px; z-index: 999; transition: .3s; opacity: 0; pointer-events: none; }
.toast.show { opacity: 1; }
.toast.ok { background: #238636; color: #fff; }
.toast.err { background: #da3633; color: #fff; }
.empty { text-align: center; padding: 60px 20px; color: #8b949e; }
.empty .icon { font-size: 48px; margin-bottom: 12px; }
</style>
</head>
<body>

<h1>过滤条目审核</h1>
<div class="subtitle">TOTAL_FILTERED 条被过滤 · 勾选需要回捞的条目 · 点击底部保存</div>

<div class="stats" id="stats">STAT_CHIPS</div>

<div class="actions">
  <label><input type="checkbox" id="selectAll" onchange="toggleAll(this)"> 全选当前视图</label>
  <input type="text" id="searchBox" placeholder="搜索标题..." oninput="render()">
</div>

<div class="item-list" id="itemList">ITEMS_HTML</div>

<div class="bottom-bar">
  <div class="count">已勾选 <strong id="checkedCount">0</strong> / TOTAL_FILTERED 条</div>
  <div style="display:flex;gap:8px">
    <button class="btn btn-reset" onclick="resetAll()">清空</button>
    <button class="btn btn-save" onclick="save()">Save & Close</button>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const ITEMS = ITEMS_JSON;
const selected = new Set();

function render() {
  const active = document.querySelector('.stat-chip.active');
  const filter = active ? active.dataset.reason : '';
  const query = document.getElementById('searchBox').value.toLowerCase();

  const filtered = ITEMS.filter(it => {
    if (filter && it.filter_reason !== filter) return false;
    if (query && !it.title.toLowerCase().includes(query)) return false;
    return true;
  });

  const html = filtered.map((it, i) => {
    const checked = selected.has(it.title);
    const cls = checked ? 'item checked' : 'item';
    const summary = it.summary ? `<div class="item-summary">${escHtml(it.summary)}</div>` : '';
    const url = it.url ? `<a href="${escHtml(it.url)}" target="_blank">${escHtml(it.title)}</a>` : escHtml(it.title);
    return `<div class="${cls}" onclick="toggle('${escAttr(it.title)}', this)">
      <input type="checkbox" ${checked ? 'checked' : ''} onclick="event.stopPropagation(); toggle('${escAttr(it.title)}', this.parentElement)">
      <div class="item-body">
        <div class="item-title">${url}</div>
        <div class="item-meta">
          <span class="tag">${escHtml(it.source_type)}</span>
          <span class="tag reason">${escHtml(it.filter_reason)}</span>
          <span>${it._filter_name || ''}</span>
        </div>
        ${summary}
      </div>
    </div>`;
  }).join('');

  document.getElementById('itemList').innerHTML = html || '<div class="empty"><div class="icon">&#x1F50D;</div>没有匹配的条目</div>';
  document.getElementById('checkedCount').textContent = selected.size;
}

function toggle(title, el) {
  if (selected.has(title)) {
    selected.delete(title);
    if (el) el.classList.remove('checked');
  } else {
    selected.add(title);
    if (el) el.classList.add('checked');
  }
  document.getElementById('checkedCount').textContent = selected.size;
}

function toggleAll(cb) {
  const active = document.querySelector('.stat-chip.active');
  const filter = active ? active.dataset.reason : '';
  const query = document.getElementById('searchBox').value.toLowerCase();
  ITEMS.forEach(it => {
    if (filter && it.filter_reason !== filter) return;
    if (query && !it.title.toLowerCase().includes(query)) return;
    if (cb.checked) selected.add(it.title); else selected.delete(it.title);
  });
  render();
}

function resetAll() {
  selected.clear();
  render();
  toast('已清空勾选');
}

async function save() {
  const titles = [...selected];
  try {
    const resp = await fetch('/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({titles: titles})
    });
    if (resp.ok) {
      toast('Saved! 现在运行 python main.py --recover-reviewed', 'ok');
    } else {
      toast('Save failed', 'err');
    }
  } catch(e) {
    toast('Save error: ' + e.message, 'err');
  }
}

function toast(msg, type='ok') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show ' + type;
  setTimeout(() => el.className = 'toast', 2500);
}

function filterBy(reason, chip) {
  document.querySelectorAll('.stat-chip').forEach(c => c.classList.remove('active'));
  if (chip) chip.classList.add('active');
  document.getElementById('selectAll').checked = false;
  render();
}

function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function escAttr(s) { return String(s).replace(/'/g,"\\'").replace(/"/g,'&quot;'); }

render();
</script>
</body>
</html>"""


# ---------- HTTP Server ----------

class ReviewHandler(BaseHTTPRequestHandler):
    items: list[dict] = []  # class-level, set before server start

    def log_message(self, format, *args):
        pass  # suppress logs

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._serve_page()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/save":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body)
            titles = data.get("titles", [])
            self._save_selection(titles)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            # 打印到终端
            print(f"\n已保存勾选: {len(titles)} 条")
            print("现在运行: python main.py --recover-reviewed")
        else:
            self.send_error(404)

    def _serve_page(self):
        total = len(ReviewHandler.items)
        # 统计 chips
        by_reason: dict[str, int] = {}
        for it in ReviewHandler.items:
            r = it.get("filter_reason", "unknown")
            by_reason[r] = by_reason.get(r, 0) + 1

        chips = ""
        for reason, count in sorted(by_reason.items(), key=lambda x: -x[1]):
            label = _reason_label(reason)
            chips += f'<span class="stat-chip" data-reason="{reason}" onclick="filterBy(\'{reason}\', this)">{label}<span class="count">{count}</span></span>\n'

        chips += f'<span class="stat-chip active" data-reason="" onclick="filterBy(\'\', this)">全部<span class="count">{total}</span></span>'

        html = HTML_TEMPLATE.replace("ITEMS_JSON", json.dumps(ReviewHandler.items, ensure_ascii=False))
        html = html.replace("TOTAL_FILTERED", str(total))
        html = html.replace("STAT_CHIPS", chips)
        html = html.replace("ITEMS_HTML", "")  # populated by JS

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _save_selection(self, titles: list[str]):
        with open(SELECTION_FILE, "w", encoding="utf-8") as f:
            json.dump({"titles": titles, "count": len(titles),
                       "saved_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")},
                      f, ensure_ascii=False, indent=2)


# ---------- Helpers ----------

def _reason_label(reason: str) -> str:
    labels = {
        "too_short": "内容过短",
        "social_placeholder": "社交媒体占位符",
        "tieba_user_page": "贴吧用户页",
        "url_encoded_garbage": "URL编码垃圾",
        "incomplete_content": "截断/内容待补充",
        "image_only_no_text": "仅图片无文字",
        "no_summary_short_title": "无摘要+短标题",
        "topic_irrelevant": "话题不相关",
    }
    return labels.get(reason, reason)


def _load_filtered_data() -> list[dict]:
    """加载所有过滤条目"""
    try:
        with open(_FILTERED_LOG_PATH, "r", encoding="utf-8") as f:
            log = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    items = []
    for run in log.get("runs", []):
        for it in run.get("items", []):
            it["_filtered_at"] = run.get("filtered_at", "")
            it["_filter_name"] = run.get("filter_name", "")
            items.append(it)
    return items


def start_server(port: int = DEFAULT_PORT):
    """启动审核服务器并打开浏览器"""
    items = _load_filtered_data()
    if not items:
        print("没有找到过滤日志。请先运行一次管道。")
        sys.exit(1)

    ReviewHandler.items = items

    server = HTTPServer(("127.0.0.1", port), ReviewHandler)
    url = f"http://127.0.0.1:{port}"

    console = None
    try:
        from rich.console import Console
        console = Console()
    except ImportError:
        pass

    msg = f"\n{'='*60}\n  过滤条目审核 — {len(items)} 条被过滤\n  浏览器已打开: {url}\n  勾选误踢条目 → 点击 Save & Close\n  然后运行: python main.py --recover-reviewed\n{'='*60}\n"
    if console:
        console.print(f"[bold cyan]{msg}[/bold cyan]")
    else:
        print(msg)

    # 延迟打开浏览器，确保服务器已启动
    def _open():
        webbrowser.open(url)
    threading.Timer(0.5, _open).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n审核服务器已关闭")


def load_checked_items() -> list[str]:
    """读取勾选结果，返回需回捞的标题列表（供 main.py --recover-reviewed 使用）"""

    # 优先读新的 JSON 勾选文件
    if os.path.exists(SELECTION_FILE):
        with open(SELECTION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("titles", [])

    # 兼容旧的 Markdown 审核清单
    if os.path.exists(REVIEW_FILE):
        with open(REVIEW_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        checked = []
        for line in content.split("\n"):
            if "- [x]" in line and "|" in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 5:
                    title = parts[3]
                    if title and title != "勾选":
                        checked.append(title)
        return checked

    return []


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--stats":
        from pipeline.filter import show_filter_stats
        stats = show_filter_stats()
        if not stats:
            print("没有过滤日志。")
        else:
            for reason, count in sorted(stats.items(), key=lambda x: -x[1]):
                print(f"  {_reason_label(reason)} ({reason}): {count}")
            print(f"  合计: {sum(stats.values())} 条")
    else:
        port = DEFAULT_PORT
        if "--port" in sys.argv:
            idx = sys.argv.index("--port")
            if idx + 1 < len(sys.argv):
                port = int(sys.argv[idx + 1])
        start_server(port)
