#!/usr/bin/env python3
"""过滤条目审核页面 — 浏览器打勾审核。

用法:
    python review_filtered.py          # 启动审核页面 (端口 8765)
    python review_filtered.py --port 8765
"""

import json
import os
import sys
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from config import OUTPUT_DIR
from pipeline.filter import _FILTERED_LOG_PATH

DEFAULT_PORT = 8765
SELECTION_FILE = os.path.join(OUTPUT_DIR, ".filtered_selection.json")

HEAD = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>Review</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Microsoft YaHei",sans-serif;background:#0d1117;color:#c9d1d9;padding:16px}
h1{color:#58a6ff;font-size:18px;margin-bottom:4px}
.sub{color:#8b949e;font-size:12px;margin-bottom:12px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.chip{padding:3px 10px;border-radius:10px;font-size:11px;text-decoration:none;border:1px solid #30363d;background:#161b22;color:#8b949e}
.chip.on{border-color:#58a6ff;color:#f0f6fc;background:#1f6feb}
table{width:100%;border-collapse:collapse;font-size:12px}
td{padding:6px 8px;border-bottom:1px solid #21262d;vertical-align:middle}
tr:hover{background:rgba(88,166,255,.03)}
td.cb{width:30px;text-align:center}
td.cb input{accent-color:#238636;width:14px;height:14px;cursor:pointer}
td.t{color:#f0f6fc;word-break:break-all}
td.m{color:#8b949e;font-size:10px;white-space:nowrap}
.btn{padding:8px 18px;border-radius:7px;border:none;font-size:13px;font-weight:600;cursor:pointer;transition:.2s}
.btn-g{background:#238636;color:#fff}.btn-g:hover{background:#2ea043}
.btn-o{background:transparent;color:#8b949e;border:1px solid #30363d}.btn-o:hover{border-color:#58a6ff}
.bar{position:sticky;bottom:0;margin-top:12px;padding:12px;border-radius:8px;border:1px solid #30363d;background:#161b22;display:flex;gap:10px;align-items:center;justify-content:space-between}
.bar .info{font-size:12px;color:#8b949e}
.toast{position:fixed;top:12px;right:12px;padding:10px 18px;border-radius:8px;font-size:13px;background:#238636;z-index:99;display:none}
</style></head><body>"""


class ReviewHandler(BaseHTTPRequestHandler):
    filtered_items: list[dict] = []

    def log_message(self, f, *a):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        reason = parse_qs(parsed.query).get("reason", [""])[0]

        items = self.filtered_items
        if reason:
            items = [i for i in items if i.get("filter_reason") == reason]

        self._page(items, reason)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        params = parse_qs(body)
        titles = params.get("titles", [])

        with open(SELECTION_FILE, "w", encoding="utf-8") as f:
            json.dump({"titles": titles, "count": len(titles)}, f, ensure_ascii=False, indent=2)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>Saved</title>
<style>body{{font-family:"Microsoft YaHei",sans-serif;background:#0d1117;color:#c9d1d9;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:12px}}
h1{{color:#238636}} .cmd{{background:#161b22;padding:10px 18px;border-radius:6px;color:#f0f6fc;font-family:monospace;font-size:13px}}</style></head>
<body><h1>Saved {len(titles)} items</h1>
<p>Now run in terminal:</p>
<div class="cmd">python main.py --recover-reviewed</div>
<p style="color:#8b949e;margin-top:8px">Or click the Recover button in the floating widget</p>
</body></html>""".encode("utf-8"))

    def _page(self, items, active_reason):
        # Build reason chips
        by_reason = {}
        for it in self.filtered_items:
            r = it.get("filter_reason", "unknown")
            by_reason[r] = by_reason.get(r, 0) + 1

        labels = {
            "too_short": "content too short", "social_placeholder": "social placeholder",
            "tieba_user_page": "tieba user page", "url_encoded_garbage": "url garbage",
            "incomplete_content": "incomplete", "image_only_no_text": "image only",
            "no_summary_short_title": "no summary", "topic_irrelevant": "off topic",
        }

        chips_html = ""
        total = len(self.filtered_items)
        cls = "chip on" if not active_reason else "chip"
        chips_html += f'<a class="{cls}" href="/">all {total}</a>\n'
        for reason, count in sorted(by_reason.items(), key=lambda x: -x[1]):
            cls = "chip on" if reason == active_reason else "chip"
            label = labels.get(reason, reason)
            chips_html += f'<a class="{cls}" href="/?reason={reason}">{label} {count}</a>\n'

        # Build table rows
        rows = ""
        for idx, it in enumerate(items):
            title = (it.get("title") or "no title").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            source = it.get("source_type", "?")
            reason = it.get("filter_reason", "?")
            summary = (it.get("summary") or "")[:80].replace("&", "&amp;").replace("<", "&lt;")
            name = f"item_{idx}"
            rows += f'<tr><td class="cb"><input type="checkbox" name="titles" value="{title}" form="reviewForm"></td><td class="t">{title}</td><td class="m">{source}</td><td class="m">{reason}</td><td class="m">{summary}</td></tr>\n'

        html = HEAD
        html += f'<h1>Filter Review ({len(items)} / {total} items)</h1>'
        html += f'<div class="sub">Check items to recover, then click Save below</div>'
        html += f'<div class="chips">{chips_html}</div>'

        html += f'<form id="reviewForm" method="POST" action="/">'
        html += f'<table><tbody>{rows}</tbody></table>'
        html += f'<div class="bar">'
        html += f'<div class="info"><span id="count">0</span> / {len(items)} selected</div>'
        html += f'<div>'
        html += f'<button type="button" class="btn btn-o" onclick="document.querySelectorAll(\'input[type=checkbox]\').forEach(c=>c.checked=!c.checked);updateCount()">Toggle All</button> '
        html += f'<button type="submit" class="btn btn-g">Save & Exit</button>'
        html += f'</div></div></form>'

        html += f'<div class="toast" id="toast"></div>'
        html += f'<script>function updateCount(){{var n=document.querySelectorAll(\'input[type=checkbox]:checked\').length;document.getElementById(\'count\').textContent=n}};document.querySelectorAll(\'input[type=checkbox]\').forEach(c=>c.addEventListener(\'change\',updateCount));updateCount();</script>'
        html += f'</body></html>'

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


def start_server(port=DEFAULT_PORT):
    items = []
    try:
        if os.path.exists(_FILTERED_LOG_PATH):
            with open(_FILTERED_LOG_PATH, "r", encoding="utf-8") as f:
                log = json.load(f)
            for run in log.get("runs", []):
                for it in run.get("items", []):
                    it["_filtered_at"] = run.get("filtered_at", "")
                    it["_filter_name"] = run.get("filter_name", "")
                    items.append(it)
    except Exception:
        pass

    if not items:
        print("No filtered items found. Run the pipeline first.")
        sys.exit(1)

    ReviewHandler.filtered_items = items
    server = HTTPServer(("127.0.0.1", port), ReviewHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"Review page: {url}  ({len(items)} items)")
    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped")


def load_checked_items() -> list[str]:
    """读取勾选结果"""
    if os.path.exists(SELECTION_FILE):
        with open(SELECTION_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("titles", [])

    # fallback: old markdown review file
    md_file = os.path.join(OUTPUT_DIR, ".filtered_review.md")
    if os.path.exists(md_file):
        with open(md_file, "r", encoding="utf-8") as f:
            content = f.read()
        checked = []
        for line in content.split("\n"):
            if "- [x]" in line and "|" in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 5:
                    title = parts[3]
                    if title and title != "selected":
                        checked.append(title)
        return checked
    return []


if __name__ == "__main__":
    port = DEFAULT_PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])
    start_server(port)
