"""口播视频交互式工作流 — 本地 Web 界面，6步完成

启动: python video_workflow.py [周刊.md路径]
默认: python video_workflow.py output/2026-W29.md
"""

import os
import re
import sys
import json
import time
import shutil
import asyncio
import tempfile
import hashlib
import subprocess
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, render_template_string, request, jsonify, send_file
from rich.console import Console

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

console = Console()

# ========== 配置 ==========
TEMP_DIR = Path(tempfile.gettempdir()) / "gaming_news_workflow"
VIDEO_CACHE = Path(__file__).parent.parent / "storage" / "video_cache"
WORK_DIR = None
VOICE = "zh-CN-XiaoxiaoNeural"
TTS_RATE = "+10%"
VIDEO_W = 1920
VIDEO_H = 1080
MAX_TTS_PARALLEL = 3

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

# 全局状态
state = {
    "md_path": "",
    "segments": [],
    "selected": [],
}


# ========== Flask 页面模板 ==========

STEP1_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Step 1/6 — 选择新闻条目</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Microsoft YaHei",sans-serif;background:#1a1a2e;color:#eee;padding:20px}
h1{color:#e94560;margin-bottom:4px}
.sub{color:#888;margin-bottom:20px;font-size:14px}
.cat{background:#16213e;border-radius:10px;padding:16px;margin-bottom:16px}
.cat h2{color:#0f3460;background:#e94560;display:inline-block;padding:4px 14px;border-radius:6px;font-size:16px}
.item{display:flex;align-items:flex-start;gap:12px;padding:12px 0;border-bottom:1px solid #222}
.item:last-child{border:none}
.item input[type=checkbox]{margin-top:6px;transform:scale(1.3);accent-color:#e94560}
.item .info{flex:1}
.item .title{font-weight:bold;color:#fff;margin-bottom:4px}
.item .meta{font-size:12px;color:#888}
.item .meta span{margin-right:12px}
.preview-img{max-width:120px;max-height:68px;border-radius:6px;object-fit:cover}
.btn-bar{position:sticky;bottom:0;background:#1a1a2e;padding:16px 0;border-top:2px solid #e94560;margin-top:20px}
.btn{background:#e94560;color:#fff;border:none;padding:12px 32px;font-size:18px;border-radius:8px;cursor:pointer}
.btn:hover{background:#ff6b81}
.btn-ghost{background:transparent;border:1px solid #555;margin-left:12px}
.summary{float:right;color:#aaa;font-size:14px;line-height:48px}
</style>
</head>
<body>
<h1>🎬 口播视频工作流 — Step 1/6</h1>
<p class="sub">勾选要制作成视频的新闻条目（默认全选）</p>
<form id="form" method="POST" action="/step2">
{% for cat, items in by_category.items() %}
<div class="cat">
  <h2>{{ cat }} ({{ items|length }} 条)</h2>
  {% for seg in items %}
  <label class="item">
    <input type="checkbox" name="idx" value="{{ seg._idx }}" checked>
    {% if seg.image_url %}
    <img class="preview-img" src="{{ seg.image_url }}" onerror="this.style.display='none'" loading="lazy">
    {% endif %}
    <div class="info">
      <div class="title">{{ seg.display_title[:80] }}</div>
      <div class="meta">
        <span>原文: {{ seg.title[:30] }}</span>
        <span>源: {{ seg.source[:20] }}</span>
        <span>{{ seg.char_count }} 字</span>
        <span>配图: {{ '有' if seg.image_url else '无' }}</span>
      </div>
    </div>
  </label>
  {% endfor %}
</div>
{% endfor %}
<div class="btn-bar">
  <button class="btn" type="submit">下一步 → 编辑脚本</button>
  <span class="summary">已选 <b id="count">{{ total }}</b> / {{ total }} 条</span>
</div>
</form>
<script>
document.querySelectorAll('input[type=checkbox]').forEach(cb=>{
  cb.addEventListener('change',()=>{
    document.getElementById('count').textContent=
      document.querySelectorAll('input[type=checkbox]:checked').length
  })
})
</script>
</body>
</html>"""


STEP2_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Step 2/6 — 编辑口播脚本</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Microsoft YaHei",sans-serif;background:#1a1a2e;color:#eee;padding:20px}
h1{color:#e94560;margin-bottom:4px}
.sub{color:#888;margin-bottom:20px}
.card{background:#16213e;border-radius:10px;padding:20px;margin-bottom:20px}
.card h3{color:#f0c040;margin-bottom:10px}
.card .row{display:flex;gap:16px}
.card .left{flex:1}
.card textarea{width:100%;background:#0f3460;color:#eee;border:1px solid #333;border-radius:6px;
  padding:10px;font-size:15px;resize:vertical;min-height:80px;line-height:1.6}
.card label{display:block;color:#aaa;font-size:13px;margin:8px 0 4px}
.card .img-preview{max-width:200px;max-height:112px;border-radius:6px;object-fit:cover}
.card .img-url{width:100%;background:#0f3460;color:#ccc;border:1px solid #333;border-radius:4px;padding:6px;font-size:12px}
.btn-bar{position:sticky;bottom:0;background:#1a1a2e;padding:16px 0;border-top:2px solid #e94560}
.btn{background:#e94560;color:#fff;border:none;padding:12px 32px;font-size:18px;border-radius:8px;cursor:pointer}
.btn:hover{background:#ff6b81}
</style>
</head>
<body>
<h1>🎬 口播视频工作流 — Step 2/6</h1>
<p class="sub">编辑每条的口播脚本（TTS会逐字朗读此内容）。修改完点击"生成音频"</p>
<form method="POST" action="/step3">
{% for seg in selected %}
<div class="card">
  <h3>#{{ loop.index }} {{ seg.display_title[:60] }}</h3>
  <div class="row">
    <div class="left">
      <label>口播脚本（TTS配音文本）</label>
      <textarea name="script_{{ seg._idx }}" rows="5">{{ seg.speak_text }}</textarea>
      <label>图片URL（可直接替换）</label>
      <input class="img-url" name="img_{{ seg._idx }}" value="{{ seg.image_url }}">
    </div>
    {% if seg.image_url %}
    <img class="img-preview" src="{{ seg.image_url }}" onerror="this.style.display='none'">
    {% endif %}
  </div>
</div>
{% endfor %}
<div class="btn-bar">
  <button class="btn" type="submit">下一步 → 生成音频</button>
</div>
</form>
</body>
</html>"""


STEP3_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Step 3/6 — 生成音频 + 合成视频</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Microsoft YaHei",sans-serif;background:#1a1a2e;color:#eee;padding:20px}
h1{color:#e94560}
#progress{background:#16213e;border-radius:10px;padding:24px;margin:20px 0}
.step{margin:12px 0;padding:10px;border-radius:6px}
.step.done{background:#1a3a1a;color:#5f5}
.step.running{background:#1a1a3a;color:#ff0}
.step.waiting{color:#888}
.spinner{display:inline-block;width:16px;height:16px;border:2px solid #ff0;border-top-color:transparent;border-radius:50%;animation:spin 0.8s linear infinite;margin-right:8px}
@keyframes spin{to{transform:rotate(360deg)}}
pre{background:#0f3460;padding:12px;border-radius:6px;max-height:200px;overflow:auto;font-size:12px;margin:8px 0}
.btn{background:#e94560;color:#fff;border:none;padding:12px 32px;font-size:18px;border-radius:8px;cursor:pointer;margin-top:16px}
.btn:disabled{opacity:0.5;cursor:not-allowed}
</style>
</head>
<body>
<h1>🎬 口播视频工作流 — Step 3/6</h1>
<p>正在生成 TTS 配音并合成视频...</p>
<div id="progress"></div>
<button class="btn" id="next" disabled onclick="location.href='/step4'">下一步 → 字幕编辑</button>

<script>
function poll() {
  fetch('/api/progress').then(r=>r.json()).then(data=>{
    let html = '';
    for (let s of data.steps) {
      html += `<div class="step ${s.status}">`;
      if (s.status==='running') html += '<span class="spinner"></span>';
      html += s.text;
      if (s.detail) html += `<pre>${s.detail}</pre>`;
      html += '</div>';
    }
    document.getElementById('progress').innerHTML = html;
    if (!data.done) setTimeout(poll, 2000);
    else document.getElementById('next').disabled = false;
  });
}
poll();
</script>
</body>
</html>"""


STEP4_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Step 4/6 — 编辑字幕</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Microsoft YaHei",sans-serif;background:#1a1a2e;color:#eee;padding:20px}
h1{color:#e94560;margin-bottom:4px}
.sub{color:#888;margin-bottom:20px}
.srt-edit{width:100%;height:60vh;background:#0f3460;color:#eee;border:1px solid #555;
  border-radius:8px;padding:16px;font-family:Consolas,monospace;font-size:14px;line-height:1.8}
.btn-bar{margin-top:20px}
.btn{background:#e94560;color:#fff;border:none;padding:12px 32px;font-size:18px;border-radius:8px;cursor:pointer;margin-right:12px}
.btn-green{background:#1a8a3a}
.btn:hover{opacity:0.85}
.help{color:#888;font-size:13px;margin-top:10px}
</style>
</head>
<body>
<h1>🎬 口播视频工作流 — Step 4/6</h1>
<p class="sub">编辑 SRT 字幕。可以直接改文字、调时间。修改后点击"生成SRT文件"或直接"烧录字幕"</p>
<form method="POST" action="/step5">
<textarea class="srt-edit" name="srt_content">{{ srt_content }}</textarea>
<div class="btn-bar">
  <button class="btn btn-green" type="submit" name="action" value="compose">下一步 → 合成最终视频</button>
  <button class="btn" type="submit" name="action" value="download">仅下载 SRT 字幕文件</button>
</div>
</form>
<p class="help">
  字幕格式说明：<br>
  1) 数字序号  2) 时间范围 (HH:MM:SS,mmm --> HH:MM:SS,mmm)  3) 字幕文字<br>
  可以直接修改时序和文字，保存后重新合成视频。
</p>
</body>
</html>"""


STEP5_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Step 5/6 — 最终合成</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Microsoft YaHei",sans-serif;background:#1a1a2e;color:#eee;padding:20px;text-align:center}
h1{color:#e94560;margin-bottom:10px}
.status{font-size:20px;margin:40px 0;padding:20px;background:#16213e;border-radius:10px}
.success{color:#5f5}
.btn{background:#e94560;color:#fff;border:none;padding:14px 36px;font-size:20px;border-radius:8px;cursor:pointer;margin:8px}
.btn:hover{opacity:0.85}
.info{margin:20px;color:#888;font-size:14px}
</style>
</head>
<body>
<h1>🎬 口播视频工作流 — Step 5/6</h1>
<div class="status {{ 'success' if success else '' }}">
  {{ status_text }}
</div>
<div class="info">{{ info_text }}</div>
{% if success %}
<button class="btn" onclick="location.href='/api/download-video'">💾 下载最终视频 (MP4)</button>
<button class="btn" onclick="location.href='/'">🔄 重新开始</button>
{% else %}
<button class="btn" onclick="location.href='/step4'">↩ 返回编辑字幕</button>
{% endif %}
</body>
</html>"""


# ========== 核心逻辑 ==========

def _translate_title(title: str) -> str:
    chinese_chars = sum(1 for c in title if '一' <= c <= '鿿')
    if chinese_chars > len(title) * 0.3:
        return title
    try:
        from config import OPENAI_API_KEY, OPENAI_BASE_URL
        from openai import OpenAI
        if not OPENAI_API_KEY or OPENAI_API_KEY == "sk-xxx":
            return title
        client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": (
                "将以下游戏硬件新闻标题翻译为简体中文。"
                "品牌/产品/系统名保留原文不翻译(Steam Deck/Switch/Xbox/PlayStation/"
                "ROG Ally/AYANEO/GPD/MSI/Legion/Valve/Nintendo/Sony/AMD/Intel/Quest/"
                "PSVR/VR/Proton/BIOS/Retroid/Odin/Anbernic/Miyoo/TrimUI/PowKiddy等），"
                "其余英文翻译为中文。只返回译文：\n\n" + title
            )}],
            temperature=0.1, max_tokens=200,
        )
        translated = resp.choices[0].message.content.strip()
        if translated:
            console.log(f"  译: {title[:40]} -> {translated[:40]}")
            return translated
    except Exception as e:
        console.log(f"[yellow]翻译失败: {e}[/yellow]")
    return title


def parse_weekly(md_text: str) -> list[dict]:
    """解析周刊，返回结构化条目"""
    lines = md_text.split("\n")
    segments = []
    current_cat = ""
    in_ref = False
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        # 只跳过末尾的"## 参考资料"（不含其他"参考资料链接"子板块）
        if re.match(r'^##\s+参考资料\s*$', line):
            in_ref = True
            i += 1
            continue
        if in_ref:
            i += 1
            continue

        m = re.match(r'^##\s+(.+)', line)
        if m and not line.startswith("####"):
            current_cat = m.group(1).strip()
            i += 1
            continue

        m = re.match(r'^####\s+\d+\.\s+(.+)', line)
        if m:
            title = m.group(1).strip()
            image_url = ""
            content = ""
            analysis = ""
            source = ""
            i += 1
            while i < len(lines) and not re.match(r'^(####|###|##|--)', lines[i]):
                sub = lines[i].strip()
                im = re.match(r'!\[配图\]\((.+)\)', sub)
                if im:
                    image_url = im.group(1)
                    i += 1
                    continue
                cm = re.match(r'-\s*新闻内容[：:]\s*(.+)', sub)
                if cm:
                    content = cm.group(1).strip()
                    i += 1
                    continue
                am = re.match(r'-\s*简要分析[：:]\s*(.+)', sub)
                if am:
                    analysis = am.group(1).strip()
                    i += 1
                    continue
                sm = re.match(r'-\s*来源[：:]\s*(.+)', sub)
                if sm:
                    source = sm.group(1).strip()
                    i += 1
                    continue
                i += 1

            content = re.sub(r'\[|\]|\*|`|!\[配图\]\(.*?\)', '', content).strip()
            analysis = re.sub(r'\[|\]|\*|`|!\[配图\]\(.*?\)', '', analysis).strip()
            spoken = _translate_title(title)
            speak_text = f"{spoken}。{content} {analysis}"

            segments.append({
                "_idx": len(segments),
                "title": title,
                "display_title": spoken,
                "image_url": image_url,
                "content": content,
                "analysis": analysis,
                "source": source,
                "category": current_cat,
                "speak_text": speak_text,
                "char_count": len(speak_text),
            })
            continue
        i += 1

    return segments


def _download_image(url: str, idx: int) -> str:
    """下载图片到缓存目录"""
    VIDEO_CACHE.mkdir(parents=True, exist_ok=True)
    h = hashlib.md5(url.encode()).hexdigest()[:12]
    ext = ".jpg"
    if ".png" in url.lower():
        ext = ".png"
    elif ".webp" in url.lower():
        ext = ".webp"
    dest = VIDEO_CACHE / f"{h}{ext}"
    if dest.exists():
        return str(dest)
    try:
        r = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        dest.write_bytes(r.content)
        return str(dest)
    except Exception:
        return ""


def _prepare_bg(img_path: str, idx: int) -> str:
    """图片 → 1920x1080 背景"""
    dest = WORK_DIR / f"bg_{idx:03d}.jpg"
    if dest.exists():
        return str(dest)
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", img_path,
            "-vf", f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
                   f"crop={VIDEO_W}:{VIDEO_H},"
                   f"drawbox=x=0:y=0:w={VIDEO_W}:h={VIDEO_H}:color=black@0.3:t=fill",
            "-q:v", "2", str(dest),
        ], capture_output=True, check=True, timeout=20)
        return str(dest)
    except Exception:
        return _default_bg()


def _default_bg() -> str:
    """暗色默认背景"""
    dest = WORK_DIR / "default_bg.jpg"
    if not dest.exists():
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=0x1a1a2e:s={VIDEO_W}x{VIDEO_H}:r=1",
            "-frames:v", "1", str(dest),
        ], capture_output=True, check=True)
    return str(dest)


def _generate_tts(text: str, out_path: Path) -> bool:
    if out_path.exists():
        return True
    try:
        import edge_tts
        async def run():
            comm = edge_tts.Communicate(text, VOICE, rate=TTS_RATE)
            await comm.save(str(out_path))
        asyncio.run(run())
        return out_path.exists()
    except Exception as e:
        console.log(f"[red]TTS失败: {e}[/red]")
        return False


def _get_audio_dur(path: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe","-v","error","-show_entries","format=duration",
             "-of","default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip())
    except Exception:
        return 0


def _build_srt(segments: list[dict]) -> str:
    """生成 SRT 字幕文件内容"""
    lines = []
    seq = 1
    t = 0.0
    for seg in segments:
        ap = Path(seg.get("audio_path", ""))
        dur = _get_audio_dur(ap) if ap.exists() else len(seg["speak_text"]) / 5
        if dur <= 0:
            dur = 3

        start_ms = int(t * 1000)
        end_ms = int((t + dur) * 1000)

        def fmt(ms):
            h = ms // 3600000
            m = (ms % 3600000) // 60000
            s = (ms % 60000) // 1000
            x = ms % 1000
            return f"{h:02d}:{m:02d}:{s:02d},{x:03d}"

        lines.append(str(seq))
        lines.append(f"{fmt(start_ms)} --> {fmt(end_ms)}")
        lines.append(seg["speak_text"])
        lines.append("")
        seq += 1
        t += dur + 0.3

    return "\n".join(lines)


def _build_ass(segments: list[dict], ass_path: Path) -> Path:
    """生成 ASS 字幕"""
    header = f"""[Script Info]
Title: 游戏设备周报
ScriptType: v4.00+
PlayResX: {VIDEO_W}
PlayResY: {VIDEO_H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, Outline, Shadow, Bold, Italic, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,40,&H00FFFFFF,&H00000000,&H80000000,1,0,2,60,60,100,1
Style: Title,Microsoft YaHei,48,&H0000FFFF,&H00000000,&H80000000,1,0,2,60,60,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    t = 0.0
    for i, seg in enumerate(segments):
        ap = Path(seg.get("audio_path", ""))
        dur = _get_audio_dur(ap) if ap.exists() else len(seg["speak_text"]) / 5
        if dur <= 0:
            dur = 3
        start_ms = int(t * 1000)
        end_ms = int((t + dur) * 1000)

        def fmt(ms):
            h = ms // 3600000
            m = (ms % 3600000) // 60000
            s = (ms % 60000) // 1000
            x = ms % 1000 // 10
            return f"{h:d}:{m:02d}:{s:02d}.{x:02d}"

        # 标题字幕
        title_end = min(end_ms, start_ms + 3000)
        title_text = seg.get("display_title", seg["title"]).replace(",", "，")
        lines.append(
            f"Dialogue: 0,{fmt(start_ms)},{fmt(title_end)},Title,,0,0,0,,{title_text}"
        )

        # 内容字幕
        text = seg["speak_text"].replace(",", "，")
        sentences = _split_subs(text)
        seg_dur = (dur - 3.0) / max(len(sentences), 1)
        sent_start = start_ms + 3000
        for sent in sentences:
            sent_end = min(end_ms, sent_start + int(seg_dur * 1000))
            lines.append(
                f"Dialogue: 0,{fmt(sent_start)},{fmt(sent_end)},Default,,0,0,0,,{sent}"
            )
            sent_start = sent_end

        t += dur + 0.3

    ass_path.write_text("\n".join(lines), encoding="utf-8")
    return ass_path


def _split_subs(text: str, max_chars: int = 32) -> list[str]:
    """按标点切分为字幕短句"""
    result = []
    cur = ""
    for c in text:
        cur += c
        if c in "。！？；，,;!?" and len(cur) >= 6:
            result.append(cur.strip())
            cur = ""
        elif len(cur) >= max_chars:
            result.append(cur.strip())
            cur = ""
    if cur.strip():
        result.append(cur.strip())
    merged = []
    buf = ""
    for s in result:
        if len(buf) + len(s) <= max_chars:
            buf += s
        else:
            if buf:
                merged.append(buf)
            buf = s
    if buf:
        merged.append(buf)
    return merged if merged else [text[:max_chars]]


# ========== Flask 路由 ==========

@app.route("/")
def step1():
    """选择条目"""
    md_path = state["md_path"]
    md_text = Path(md_path).read_text(encoding="utf-8")
    segments = parse_weekly(md_text)
    state["segments"] = segments

    by_category = {}
    for seg in segments:
        by_category.setdefault(seg["category"] or "未分类", []).append(seg)

    return render_template_string(
        STEP1_TEMPLATE,
        by_category=by_category,
        total=len(segments),
    )


@app.route("/step2", methods=["POST"])
def step2():
    """编辑脚本"""
    idxs = request.form.getlist("idx")
    selected_idx = set(int(i) for i in idxs)
    selected = [s for s in state["segments"] if s["_idx"] in selected_idx]
    state["selected"] = selected
    return render_template_string(STEP2_TEMPLATE, selected=selected)


@app.route("/step3", methods=["POST"])
def step3_page():
    """保存编辑后的脚本，准备生成"""
    selected = state["selected"]

    # 更新脚本和图片
    for seg in selected:
        key = f"script_{seg['_idx']}"
        if key in request.form:
            seg["speak_text"] = request.form[key]
            seg["char_count"] = len(seg["speak_text"])
        img_key = f"img_{seg['_idx']}"
        if img_key in request.form:
            seg["image_url"] = request.form[img_key]

    state["_progress"] = {
        "done": False,
        "steps": [
            {"status": "waiting", "text": "准备图片...", "detail": ""},
            {"status": "waiting", "text": "生成 TTS 配音...", "detail": ""},
            {"status": "waiting", "text": "合成视频片段...", "detail": ""},
            {"status": "waiting", "text": "生成字幕文件...", "detail": ""},
        ],
    }

    # 在后台线程执行
    def build():
        p = state["_progress"]
        s = state["selected"]
        WORK_DIR.mkdir(parents=True, exist_ok=True)

        # 1) 图片
        p["steps"][0]["status"] = "running"
        p["steps"][0]["text"] = f"下载并处理 {len(s)} 张图片..."
        imgs = 0
        for seg in s:
            img_url = seg.get("image_url", "")
            if img_url:
                local = _download_image(img_url, seg["_idx"])
                if local:
                    bg = _prepare_bg(local, seg["_idx"])
                    if bg:
                        seg["bg_path"] = bg
                        imgs += 1
        for seg in s:
            if not seg.get("bg_path"):
                seg["bg_path"] = _default_bg()
        p["steps"][0]["status"] = "done"
        p["steps"][0]["text"] = f"图片就绪: {imgs}/{len(s)}"

        # 2) TTS
        p["steps"][1]["status"] = "running"
        p["steps"][1]["text"] = f"TTS 配音: {len(s)} 段..."
        audio_dir = WORK_DIR / "audio"
        audio_dir.mkdir(exist_ok=True)
        done_tts = 0
        with ThreadPoolExecutor(max_workers=MAX_TTS_PARALLEL) as ex:
            futures = {}
            for i, seg in enumerate(s):
                ap = audio_dir / f"seg_{i:03d}.mp3"
                seg["audio_path"] = str(ap)
                futures[ex.submit(_generate_tts, seg["speak_text"], ap)] = i
            for fut in futures:
                if fut.result():
                    done_tts += 1
        p["steps"][1]["status"] = "done"
        p["steps"][1]["text"] = f"TTS 完成: {done_tts}/{len(s)}"

        # 3) 视频片段
        p["steps"][2]["status"] = "running"
        clips = []
        for i, seg in enumerate(s):
            cl = WORK_DIR / f"clip_{i:03d}.mp4"
            ap = Path(seg["audio_path"])
            bg = seg.get("bg_path", _default_bg())
            if not ap.exists():
                continue
            try:
                subprocess.run([
                    "ffmpeg", "-y", "-loop", "1", "-i", bg,
                    "-i", str(ap),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-tune", "stillimage", "-c:a", "aac", "-b:a", "128k",
                    "-pix_fmt", "yuv420p",
                    "-t", str(_get_audio_dur(ap)), "-shortest",
                    str(cl),
                ], capture_output=True, check=True, timeout=60)
                if cl.exists():
                    clips.append(cl)
            except Exception as e:
                console.log(f"[red]片段{i}失败: {e}[/red]")
        p["steps"][2]["status"] = "done"
        p["steps"][2]["text"] = f"视频片段: {len(clips)}/{len(s)}"
        state["_clips"] = clips

        # 4) 合并 + 字幕
        p["steps"][3]["status"] = "running"
        # 合并无字幕版本
        no_sub = WORK_DIR / "no_subs.mp4"
        concat = WORK_DIR / "concat.txt"
        with open(concat, "w") as f:
            for cl in clips:
                f.write(f"file '{cl.as_posix()}'\n")
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat), "-c", "copy", str(no_sub),
        ], capture_output=True, check=True)
        state["_no_sub_video"] = str(no_sub)

        # SRT
        srt_content = _build_srt(s)
        state["_srt_content"] = srt_content
        (WORK_DIR / "subtitles.srt").write_text(srt_content, encoding="utf-8")

        # ASS
        _build_ass(s, WORK_DIR / "subtitles.ass")
        state["_ass_path"] = str(WORK_DIR / "subtitles.ass")

        p["steps"][3]["status"] = "done"
        p["steps"][3]["text"] = f"视频+字幕已生成，时长约 {int(sum(_get_audio_dur(Path(seg['audio_path'])) for seg in s if Path(seg.get('audio_path','')).exists())//60)} 分钟"
        p["done"] = True

    threading.Thread(target=build, daemon=True).start()
    return render_template_string(STEP3_TEMPLATE)


@app.route("/api/progress")
def api_progress():
    p = state.get("_progress", {"done": True, "steps": []})
    return jsonify(p)


@app.route("/step4")
def step4():
    srt = state.get("_srt_content", "")
    return render_template_string(STEP4_TEMPLATE, srt_content=srt)


@app.route("/step5", methods=["POST"])
def step5():
    action = request.form.get("action", "compose")
    srt_content = request.form.get("srt_content", "")
    state["_srt_content"] = srt_content

    if action == "download":
        srt_path = WORK_DIR / "subtitles.srt"
        srt_path.write_text(srt_content, encoding="utf-8")
        return send_file(srt_path, as_attachment=True, download_name="subtitles.srt")

    # compose final video with subtitles
    no_sub = state.get("_no_sub_video", "")
    ass_path = state.get("_ass_path", "")

    # save edited SRT
    (WORK_DIR / "subtitles_edited.srt").write_text(srt_content, encoding="utf-8")

    # generate ASS from edited SRT if it changed
    if ass_path and srt_content != state.get("_srt_original", ""):
        # 用修改后的 SRT 重新生成 ASS
        _build_ass_from_srt(srt_content, ass_path)

    final = WORK_DIR / "final.mp4"
    ass_fixed = str(Path(ass_path).resolve()).replace("\\", "/").replace(":", "\\:")
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-i", no_sub or str(WORK_DIR / "no_subs.mp4"),
            "-vf", f"subtitles='{ass_fixed}'",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy",
            str(final),
        ], capture_output=True, check=True, timeout=300)
        state["_final_video"] = str(final)
        size_mb = final.stat().st_size / 1024 / 1024
        return render_template_string(
            STEP5_TEMPLATE, success=True,
            status_text="视频生成成功!",
            info_text=f"文件: {final.name} | 大小: {size_mb:.1f}MB"
        )
    except Exception as e:
        return render_template_string(
            STEP5_TEMPLATE, success=False,
            status_text=f"合成失败: {e}",
            info_text="请返回编辑字幕重试"
        )


def _build_ass_from_srt(srt_text: str, ass_path: str):
    """从 SRT 文本生成 ASS（简化转换）"""
    # 直接用 ASS 格式写回，保持原有样式
    blocks = re.split(r'\n\n+', srt_text.strip())
    lines = [f"""[Script Info]
Title: 游戏设备周报
ScriptType: v4.00+
PlayResX: {VIDEO_W}
PlayResY: {VIDEO_H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, Outline, Shadow, Bold, Italic, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,40,&H00FFFFFF,&H00000000,&H80000000,1,0,2,60,60,100,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""]

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        parts = block.split("\n")
        if len(parts) < 3:
            continue
        # parts[0] = seq, parts[1] = time, parts[2:] = text
        time_str = parts[1]
        text = " ".join(parts[2:]).replace(",", "，")
        start, end = time_str.split(" --> ")
        # SRT format: HH:MM:SS,mmm → ASS format: H:MM:SS.mm
        def srt2ass(t):
            t = t.strip()
            h, m, rest = t.split(":")
            s, ms = rest.split(",")
            return f"{int(h)}:{m}:{s}.{ms[:2]}"
        lines.append(
            f"Dialogue: 0,{srt2ass(start)},{srt2ass(end)},Default,,0,0,0,,{text}"
        )

    Path(ass_path).write_text("\n".join(lines), encoding="utf-8")


@app.route("/api/download-video")
def download_video():
    fp = state.get("_final_video", "")
    if fp and Path(fp).exists():
        return send_file(fp, as_attachment=True, download_name="weekly_video.mp4")
    return "未找到视频文件", 404


def main():
    md_input = sys.argv[1] if len(sys.argv) > 1 else "output/2026-W29.md"
    if not os.path.isabs(md_input):
        md_input = str(Path(__file__).parent / md_input)
    if not Path(md_input).exists():
        console.log(f"[red]文件不存在: {md_input}[/red]")
        sys.exit(1)

    state["md_path"] = md_input

    global WORK_DIR
    WORK_DIR = TEMP_DIR / Path(md_input).stem
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    VIDEO_CACHE.mkdir(parents=True, exist_ok=True)

    port = 5050
    console.print(f"\n[bold green]🎬 口播视频工作流已启动[/bold green]")
    console.print(f"  周刊: {md_input}")
    console.print(f"  打开浏览器: [bold cyan]http://localhost:{port}[/bold cyan]")
    console.print(f"  按 Ctrl+C 停止\n")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
