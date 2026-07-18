"""口播视频交互式工作流 — 本地 Web 界面，6步完成

启动: python video_workflow.py [周刊.md路径]
默认: python video_workflow.py output/2026-W29.md
"""

import os
import re
import sys
import json
import uuid
import time
import shutil
import base64
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

# ========== 剪映草稿集成（可选依赖）==========
_JY_DRAFT_AVAILABLE = False
_JY_DRAFT_ROOT = None
try:
    import pyJianYingDraft as _draft
    # 从已有的 root_meta_info.json 检测草稿目录
    _META_PATH = Path(os.environ.get("LOCALAPPDATA", "")) / \
                 "JianyingPro/User Data/Projects/com.lveditor.draft/root_meta_info.json"
    if _META_PATH.exists():
        _meta = json.loads(_META_PATH.read_text(encoding="utf-8"))
        for entry in _meta.get("all_draft_store", []):
            p = entry.get("draft_root_path", "")
            if p and Path(p).exists():
                _JY_DRAFT_ROOT = p
                break
    if _JY_DRAFT_ROOT:
        _JY_DRAFT_AVAILABLE = True
        console.print(f"[green]剪映草稿目录: {_JY_DRAFT_ROOT}[/green]")
    else:
        console.print("[yellow]未检测到剪映草稿目录，剪映集成不可用[/yellow]")
except ImportError:
    console.print("[yellow]pyJianYingDraft 未安装 (pip install pyJianYingDraft)[/yellow]")
except Exception as e:
    console.print(f"[yellow]剪映草稿检测失败: {e}[/yellow]")

# ========== 配置 ==========
TEMP_DIR = Path(tempfile.gettempdir()) / "gaming_news_workflow"
VIDEO_CACHE = Path(__file__).parent / "storage" / "video_cache"
WORK_DIR = None

# TTS: 火山引擎 seed-tts-2.0（优先）
VOLC_APP_ID = os.environ.get("VOLC_TTS_APP_ID", "4526111713")
VOLC_ACCESS_KEY = os.environ.get("VOLC_TTS_ACCESS_KEY",
    "DJJJscoNvfmZNcRDmTUl--mVmOwfiJso")
VOLC_SPEAKER = os.environ.get("VOLC_TTS_SPEAKER", "saturn_zh_female_cancan_tob")
VOLC_RESOURCE_ID = "seed-tts-2.0"
VOLC_TTS_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"

# TTS: edge-tts 备用
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
<title>Step 1/5 — 选择新闻条目</title>
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
<h1>🎬 口播视频工作流 — Step 1/5</h1>
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
<title>Step 2/5 — 编辑口播脚本</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/cropperjs/1.6.2/cropper.min.css">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Microsoft YaHei",sans-serif;background:#1a1a2e;color:#eee;padding:20px}
h1{color:#e94560;margin-bottom:4px}
.sub{color:#888;margin-bottom:20px}
.card{background:#16213e;border-radius:10px;padding:20px;margin-bottom:20px}
.card h3{color:#f0c040;margin-bottom:10px}
.card .row{display:flex;gap:16px;align-items:flex-start}
.card .left{flex:1}
.card textarea{width:100%;background:#0f3460;color:#eee;border:1px solid #333;border-radius:6px;
  padding:10px;font-size:15px;resize:vertical;min-height:80px;line-height:1.6}
.card label{display:block;color:#aaa;font-size:13px;margin:8px 0 4px}
.card .img-section{width:220px;flex-shrink:0;text-align:center}
.card .img-preview{width:200px;height:112px;border-radius:6px;object-fit:cover;background:#0f3460;cursor:pointer;border:2px dashed #333}
.card .img-preview:hover{border-color:#e94560}
.card .img-preview.placeholder{display:flex;align-items:center;justify-content:center;color:#666;font-size:13px}
.card .img-url{width:100%;background:#0f3460;color:#ccc;border:1px solid #333;border-radius:4px;padding:6px;font-size:12px}
.card .img-btns{display:flex;gap:6px;margin-top:6px}
.btn-upload{background:#0f3460;color:#ccc;border:1px solid #555;padding:4px 10px;font-size:12px;border-radius:4px;cursor:pointer;flex:1}
.btn-upload:hover{background:#1a4a7a;color:#fff}
.btn-bar{position:sticky;bottom:0;background:#1a1a2e;padding:16px 0;border-top:2px solid #e94560;margin-top:20px}
.btn{background:#e94560;color:#fff;border:none;padding:12px 32px;font-size:18px;border-radius:8px;cursor:pointer}
.btn:hover{background:#ff6b81}
.btn-polish{background:#f0c040;color:#1a1a2e;border:none;padding:2px 10px;font-size:12px;border-radius:4px;cursor:pointer;margin-left:8px}
.btn-polish:hover{background:#ffe066}
.btn-polish:disabled{opacity:0.5;cursor:wait}
.polish-status{font-size:12px;color:#5f5;margin-left:6px}
/* Cropper modal */
.modal-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.85);z-index:999;align-items:center;justify-content:center}
.modal-overlay.active{display:flex}
.modal-box{background:#1a1a2e;border-radius:12px;padding:20px;max-width:90vw;max-height:90vh;display:flex;flex-direction:column}
.modal-box h3{color:#f0c040;margin-bottom:12px}
.cropper-container{max-width:80vw;max-height:60vh}
.crop-controls{display:flex;gap:12px;align-items:center;margin-top:12px;flex-wrap:wrap}
.crop-controls label{color:#aaa;font-size:13px;margin:0}
.crop-controls input[type=range]{width:120px}
.crop-controls button{background:#0f3460;color:#eee;border:1px solid #555;padding:6px 14px;border-radius:4px;cursor:pointer;font-size:13px}
.crop-controls button:hover{background:#1a4a7a}
.crop-controls .btn-save{background:#e94560;border-color:#e94560}
.crop-controls .btn-save:hover{background:#ff6b81}
.ratio-btn{background:#0f3460;color:#ccc;border:1px solid #555;padding:4px 10px;font-size:12px;border-radius:4px;cursor:pointer}
.ratio-btn:hover{background:#1a4a7a;color:#fff}
.ratio-btn.active{background:#e94560;color:#fff;border-color:#e94560}
#cropImage{max-width:80vw;max-height:55vh}
</style>
</head>
<body>
<h1>🎬 口播视频工作流 — Step 2/5</h1>
<p class="sub">编辑口播脚本 + 上传/裁剪配图。点击图片区域上传，支持裁剪/缩放/旋转。修改完点击"生成音频"</p>

<!-- 隐藏的文件选择器 -->
<input type="file" id="fileInput" accept="image/*" style="display:none">

<!-- Cropper 弹窗 -->
<div class="modal-overlay" id="cropperModal">
  <div class="modal-box">
    <h3>编辑图片 — 段 #<span id="cropSegLabel"></span></h3>
    <div><img id="cropImage" src="" alt=""></div>
    <div class="crop-controls" style="align-items:center">
      <label>缩放</label>
      <input type="range" id="zoomSlider" min="1" max="300" value="100" style="width:80px">
      <button onclick="rotateCrop(-90)" title="逆时针旋转90°">↺</button>
      <button onclick="rotateCrop(90)" title="顺时针旋转90°">↻</button>
      <button onclick="flipCrop('h')" title="水平翻转">↔</button>
      <button onclick="flipCrop('v')" title="垂直翻转">↕</button>
      <span style="color:#666;margin:0 4px">|</span>
      <label>比例</label>
      <button class="ratio-btn" data-ratio="16/9" onclick="setRatio('16/9', this)" style="background:#e94560;color:#fff">16:9</button>
      <button class="ratio-btn" data-ratio="4/3" onclick="setRatio('4/3', this)">4:3</button>
      <button class="ratio-btn" data-ratio="1/1" onclick="setRatio('1/1', this)">1:1</button>
      <button class="ratio-btn" data-ratio="3/2" onclick="setRatio('3/2', this)">3:2</button>
      <button class="ratio-btn" data-ratio="NaN" onclick="setRatio('free', this)">自由</button>
      <span style="flex:1"></span>
      <button onclick="resetCrop()">重置</button>
      <button onclick="closeCropper()">取消</button>
      <button class="btn-save" onclick="saveCrop()">保存图片</button>
    </div>
  </div>
</div>

<form method="POST" action="/step3">
<script src="https://cdnjs.cloudflare.com/ajax/libs/cropperjs/1.6.2/cropper.min.js"></script>
<script>
// ===== AI 润色 =====
async function polish(idx, btn) {
  const ta = document.getElementById('script_' + idx);
  const instr = document.getElementById('instr_' + idx);
  const status = btn.nextElementSibling;
  btn.disabled = true;
  status.textContent = '润色中...';
  try {
    const r = await fetch('/api/polish', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: ta.value, instruction: instr.value.trim()})
    });
    const data = await r.json();
    if (data.polished) { ta.value = data.polished; status.textContent = '完成!'; }
    else { status.textContent = '失败: ' + (data.error || '未知'); }
  } catch(e) { status.textContent = '网络错误'; }
  btn.disabled = false;
  setTimeout(() => status.textContent = '', 3000);
}

// ===== 图片上传 & 裁剪 =====
let cropper = null, currentCropIdx = -1;

function triggerUpload(idx) {
  currentCropIdx = idx;
  document.getElementById('fileInput').click();
}

document.getElementById('fileInput').addEventListener('change', function() {
  const file = this.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  fetch('/api/upload-image', {method:'POST', body:formData})
    .then(r => r.json())
    .then(data => {
      if (data.url) { openCropper(data.url); }
      else { alert('上传失败: ' + (data.error || '未知错误')); }
    })
    .catch(e => alert('上传失败: ' + e));
  this.value = '';
});

function openCropper(imgUrl) {
  document.getElementById('cropSegLabel').textContent = currentCropIdx;
  const modal = document.getElementById('cropperModal');
  const img = document.getElementById('cropImage');
  img.src = imgUrl;
  modal.classList.add('active');
  if (cropper) cropper.destroy();
  img.onload = function() {
    cropper = new Cropper(img, {
      aspectRatio: 16/9,
      viewMode: 2,
      autoCropArea: 1,
      responsive: true,
      zoomable: true,
      rotatable: true,
      scalable: true,
      zoomOnWheel: true,
    });
    document.getElementById('zoomSlider').value = 100;
  };
}

function rotateCrop(deg) {
  if (!cropper) return;
  const current = cropper.getData().rotate || 0;
  cropper.rotateTo(current + deg);
}

function flipCrop(direction) {
  if (!cropper) return;
  const data = cropper.getData();
  if (direction === 'h') data.scaleX = -(data.scaleX || 1);
  else data.scaleY = -(data.scaleY || 1);
  cropper.setData(data);
}

function setRatio(ratio, btn) {
  if (!cropper) return;
  cropper.setAspectRatio(ratio === 'free' ? NaN : eval(ratio));
  document.querySelectorAll('.ratio-btn').forEach(b => {
    b.style.background = ''; b.style.color = '';
  });
  btn.style.background = '#e94560'; btn.style.color = '#fff';
}

function resetCrop() {
  if (!cropper) return;
  cropper.reset();
  document.getElementById('zoomSlider').value = 100;
}

document.getElementById('zoomSlider').addEventListener('input', function() {
  if (cropper) cropper.zoomTo(parseInt(this.value) / 100);
});

function closeCropper() {
  document.getElementById('cropperModal').classList.remove('active');
  if (cropper) { cropper.destroy(); cropper = null; }
}

function saveCrop() {
  if (!cropper) return;
  const canvas = cropper.getCroppedCanvas({maxWidth:1920, maxHeight:1080});
  if (!canvas) { alert('裁剪失败'); return; }
  const dataUrl = canvas.toDataURL('image/jpeg', 0.9);
  fetch('/api/save-image/' + currentCropIdx, {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({image: dataUrl})
  })
  .then(r => r.json())
  .then(data => {
    if (data.ok) {
      let preview = document.getElementById('preview_' + currentCropIdx);
      if (preview) {
        const newUrl = data.url + '?t=' + Date.now();
        if (preview.tagName === 'DIV') {
          // 替换占位 DIV 为 IMG
          const img = document.createElement('img');
          img.className = 'img-preview';
          img.id = 'preview_' + currentCropIdx;
          img.src = newUrl;
          img.onclick = function() { triggerUpload(currentCropIdx); };
          img.title = '点击上传/更换图片';
          img.onerror = function() { this.style.display='none'; this.nextElementSibling.style.display='flex'; };
          // 插入隐藏的占位 DIV 供 onerror 切换
          const fallback = document.createElement('div');
          fallback.className = 'img-preview placeholder';
          fallback.style.display = 'none';
          fallback.onclick = function() { triggerUpload(currentCropIdx); };
          fallback.textContent = '点击上传图片';
          preview.replaceWith(img);
          img.after(fallback);
        } else {
          preview.src = newUrl;
          preview.style.display = '';
          const fb = preview.nextElementSibling;
          if (fb && fb.classList.contains('placeholder')) fb.style.display = 'none';
        }
        // 清除 URL 输入框，让后续流程优先使用裁剪后的本地文件
        const urlInput = document.querySelector('input[name="img_' + currentCropIdx + '"]');
        if (urlInput) urlInput.value = '';
      }
      closeCropper();
    } else {
      alert('保存失败: ' + (data.error || '未知'));
    }
  })
  .catch(e => alert('保存失败: ' + e));
}
</script>

{% for seg in selected %}
<div class="card">
  <h3>#{{ loop.index }} {{ seg.display_title[:60] }}</h3>
  <div class="row">
    <div class="left">
      <label>口播脚本（TTS配音文本）</label>
      <div style="display:flex;gap:8px;margin-bottom:4px">
        <input type="text" id="instr_{{ seg._idx }}" placeholder="输入润色指令，如：精简到100字、语气更激动、突出爆料信息..."
             style="flex:1;background:#0f3460;color:#eee;border:1px solid #555;border-radius:4px;padding:6px 10px;font-size:13px">
        <button type="button" class="btn-polish" onclick="polish({{ seg._idx }}, this)" style="white-space:nowrap">✨ AI润色</button>
        <span class="polish-status"></span>
      </div>
      <textarea name="script_{{ seg._idx }}" id="script_{{ seg._idx }}" rows="5">{{ seg.speak_text }}</textarea>
      <label>图片URL（可直接替换）</label>
      <input class="img-url" name="img_{{ seg._idx }}" value="{{ seg.image_url }}">
    </div>
    <div class="img-section">
      {% set has_img = seg.image_url or seg.get('_display_img', '') %}
      {% set img_src = seg.image_url or seg.get('_display_img', '') %}
      {% if has_img %}
      <img class="img-preview" id="preview_{{ seg._idx }}" src="{{ img_src }}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" onclick="triggerUpload({{ seg._idx }})" title="点击上传/更换图片">
      <div class="img-preview placeholder" style="display:none" onclick="triggerUpload({{ seg._idx }})" title="点击上传图片">点击上传图片</div>
      {% else %}
      <div class="img-preview placeholder" id="preview_{{ seg._idx }}" onclick="triggerUpload({{ seg._idx }})" title="点击上传图片">点击上传图片</div>
      {% endif %}
      <div class="img-btns">
        <button type="button" class="btn-upload" onclick="triggerUpload({{ seg._idx }})">📷 上传图片</button>
        {% if has_img %}
        <button type="button" class="btn-upload" onclick="var p=document.getElementById('preview_{{ seg._idx }}'); if(p.tagName==='DIV'){p.style.display='none';p.previousElementSibling.style.display='';p.previousElementSibling.src='{{ img_src }}'}else{p.src='{{ img_src }}'}" title="恢复原始图片">↩</button>
        {% endif %}
      </div>
    </div>
  </div>
</div>
{% endfor %}
<div class="btn-bar">
  <button class="btn" type="button" onclick="history.back()" style="background:#0f3460">↩ 返回选择条目</button>
  <button class="btn" type="submit">下一步 → 生成音频</button>
</div>
</form>
</body>
</html>"""


STEP3_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Step 3/5 — 生成音频 + 合成视频</title>
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
<h1>🎬 口播视频工作流 — Step 3/5</h1>
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
<title>Step 4/5 — 编辑字幕</title>
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
<h1>🎬 口播视频工作流 — Step 4/5</h1>
<p class="sub">编辑 SRT 字幕。可以直接改文字、调时间。修改后点击下方按钮保存并查看剪映草稿</p>
<form method="POST" action="/step5">
<textarea class="srt-edit" name="srt_content">{{ srt_content }}</textarea>
<div class="btn-bar">
  <button class="btn" type="button" onclick="location.href='/step2'" style="background:#0f3460">↩ 返回编辑脚本/图片</button>
  <button class="btn btn-green" type="submit" name="action" value="compose">下一步 → 查看剪映草稿</button>
  <button class="btn" type="submit" name="action" value="download">仅下载 SRT 字幕文件</button>
</div>
</form>
<p class="help">
  字幕格式说明：<br>
  1) 数字序号  2) 时间范围 (HH:MM:SS,mmm --> HH:MM:SS,mmm)  3) 字幕文字<br>
  修改字幕文本和时间后，可以在下一步重新生成剪映草稿。
</p>
</body>
</html>"""


STEP5_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Step 5/5 — 剪映草稿</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Microsoft YaHei",sans-serif;background:#1a1a2e;color:#eee;padding:20px;text-align:center}
h1{color:#e94560;margin-bottom:10px}
.status{font-size:20px;margin:40px 0;padding:20px;background:#16213e;border-radius:10px}
.success{color:#5f5}
.btn{background:#e94560;color:#fff;border:none;padding:14px 36px;font-size:20px;border-radius:8px;cursor:pointer;margin:8px}
.btn:hover{opacity:0.85}
.btn-jy{background:#f0c040;color:#1a1a2e}
.info{margin:20px;color:#888;font-size:14px}
.jy-status{margin-top:12px;font-size:14px;color:#5f5}
.path-box{background:#0f3460;padding:16px 24px;border-radius:8px;margin:16px auto;max-width:600px;font-family:Consolas,monospace;font-size:13px;word-break:break-all;text-align:left}
</style>
</head>
<body>
<h1>🎬 口播视频工作流 — Step 5/5</h1>
<div class="status success">
  剪映草稿已就绪！请在剪映中打开编辑和导出
</div>
{% if draft_path %}
<div class="info">草稿路径</div>
<div class="path-box">{{ draft_path }}</div>
{% endif %}
<div class="info">工作目录: {{ work_dir }}</div>
<button class="btn btn-jy" onclick="generateJYDraft()">🎞 重新生成剪映草稿</button>
<span id="jy_result" class="jy-status"></span>
<button class="btn" onclick="location.href='/step4'">↩ 返回编辑字幕</button>
<button class="btn" onclick="location.href='/'">🔄 重新开始</button>

<script>
async function generateJYDraft() {
  const btn = event.target;
  const result = document.getElementById('jy_result');
  btn.disabled = true;
  btn.textContent = '生成中...';
  result.textContent = '';
  try {
    const r = await fetch('/api/jianying-draft');
    const data = await r.json();
    if (data.success) {
      btn.textContent = '✅ 已生成';
      result.textContent = '草稿路径: ' + data.message;
    } else {
      btn.textContent = '失败';
      result.textContent = '错误: ' + (data.message || data.error);
    }
  } catch(e) {
    btn.textContent = '网络错误';
    result.textContent = String(e);
  }
  btn.disabled = false;
}
</script>
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


def _volc_tts(text: str, out_path: Path) -> bool:
    """火山引擎 seed-tts-2.0 HTTP 流式 TTS"""
    if out_path.exists() and out_path.stat().st_size > 1024:
        return True
    if out_path.exists():
        out_path.unlink()
    try:
        response = requests.post(
            VOLC_TTS_URL,
            headers={
                "X-Api-App-Id": VOLC_APP_ID,
                "X-Api-Access-Key": VOLC_ACCESS_KEY,
                "X-Api-Resource-Id": VOLC_RESOURCE_ID,
                "Content-Type": "application/json",
            },
            json={
                "req_params": {
                    "text": text,
                    "speaker": VOLC_SPEAKER,
                    "audio_params": {
                        "format": "mp3",
                        "sample_rate": 24000,
                        "speech_rate": 10,
                    },
                },
            },
            stream=True,
            timeout=120,
        )
        if response.status_code != 200:
            console.log(f"[red]火山TTS HTTP {response.status_code}[/red]")
            return False

        audio_data = bytearray()
        for line in response.iter_lines():
            if line:
                try:
                    d = json.loads(line.decode("utf-8"))
                    if d.get("data"):
                        audio_data.extend(base64.b64decode(d["data"]))
                except Exception:
                    pass

        if len(audio_data) < 1024:
            console.log(f"[red]火山TTS 音频数据不足 1KB[/red]")
            return False

        out_path.write_bytes(audio_data)
        return True
    except requests.Timeout:
        console.log(f"[red]火山TTS 超时(120s)[/red]")
        if out_path.exists():
            out_path.unlink()
        return False
    except Exception as e:
        console.log(f"[red]火山TTS失败: {e}[/red]")
        if out_path.exists():
            out_path.unlink()
        return False


def _edge_tts(text: str, out_path: Path) -> bool:
    """edge-tts 备用 TTS"""
    if out_path.exists() and out_path.stat().st_size > 1024:
        return True
    if out_path.exists():
        out_path.unlink()
    try:
        import edge_tts
        async def run():
            comm = edge_tts.Communicate(text, VOICE, rate=TTS_RATE)
            await asyncio.wait_for(comm.save(str(out_path)), timeout=120)
        asyncio.run(run())
        return out_path.exists() and out_path.stat().st_size > 1024
    except asyncio.TimeoutError:
        console.log(f"[red]edge-tts超时(120s)[/red]")
        if out_path.exists():
            out_path.unlink()
        return False
    except Exception as e:
        console.log(f"[red]edge-tts失败: {e}[/red]")
        if out_path.exists():
            out_path.unlink()
        return False


def _generate_tts(text: str, out_path: Path) -> bool:
    """TTS 入口：火山引擎优先，edge-tts 备用"""
    if _volc_tts(text, out_path):
        return True
    console.log(f"[yellow]火山TTS失败，回退 edge-tts[/yellow]")
    return _edge_tts(text, out_path)


def _sentence_tts_fallback(text: str, out_path: Path) -> None:
    """逐句生成 TTS 后用 ffmpeg 拼接 — 整段 TTS 失败时的最后兜底"""
    sentences = _split_subs(text)
    if not sentences:
        return

    tmp_dir = out_path.parent / f"_sent_{out_path.stem}"
    tmp_dir.mkdir(exist_ok=True)
    sent_files = []
    for j, sent in enumerate(sentences):
        sp = tmp_dir / f"s{j:03d}.mp3"
        if _generate_tts(sent, sp):
            sent_files.append(sp)
        if j < len(sentences) - 1:
            time.sleep(3)  # 句间延迟，防限流

    if not sent_files:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    if len(sent_files) == 1:
        shutil.copy(sent_files[0], out_path)
    else:
        concat_list = tmp_dir / "concat.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for sf in sent_files:
                f.write(f"file '{sf.as_posix()}'\n")
        try:
            subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_list), "-c", "copy", str(out_path),
            ], capture_output=True, check=True, timeout=60)
        except Exception as e:
            console.log(f"[red]逐句TTS拼接失败: {e}[/red]")

    shutil.rmtree(tmp_dir, ignore_errors=True)


def _generate_tts_with_timing(text: str, out_path: Path, max_retries: int = 3) -> list[tuple[str, int, int]]:
    """生成 TTS 音频 + 字幕时序数据

    优先级：火山 TTS（音质好，无字级时序）→ edge-tts（SentenceBoundary 精确时间戳）
    火山 TTS 返回空列表 → _build_srt 走 _split_subs + 字数比例路径

    Returns:
        [(句子文本, 开始ms, 结束ms), ...] — SentenceBoundary 精确时间戳，或空列表
    """
    import json as _json
    timing_path = Path(str(out_path).replace(".mp3", "_timing.json"))
    if out_path.exists() and timing_path.exists():
        if out_path.stat().st_size > 1024:
            with open(timing_path, "r", encoding="utf-8") as f:
                return [tuple(t) for t in _json.load(f)]
        out_path.unlink()
        timing_path.unlink()

    # 优先火山 TTS（音质好，但无字级时序 → 返回 []，_build_srt 用 _split_subs + 字数比例）
    if _volc_tts(text, out_path):
        timing_path.write_text("[]", encoding="utf-8")
        return []

    # 火山 TTS 失败 → edge-tts SentenceBoundary 精确时序
    console.log("[yellow]火山TTS失败，回退 edge-tts 时序模式[/yellow]")

    import edge_tts
    TTS_TIMEOUT = 120

    async def _stream_to_list(comm) -> list[dict]:
        chunks = []
        async for chunk in comm.stream():
            chunks.append(chunk)
        return chunks

    async def _tts_with_timeout(text: str) -> tuple[list[tuple], bytes]:
        comm = edge_tts.Communicate(text, VOICE, rate=TTS_RATE)
        chunks = await asyncio.wait_for(_stream_to_list(comm), timeout=TTS_TIMEOUT)

        sub = edge_tts.SubMaker()
        audio_data = bytearray()
        for chunk in chunks:
            if chunk["type"] == "audio":
                audio_data.extend(chunk["data"])
            elif chunk["type"] == "SentenceBoundary":
                sub.feed(chunk)

        cues = []
        prev_end = 0
        for cue in sub.cues:
            start_ms = int(cue.start.total_seconds() * 1000)
            end_ms = int(cue.end.total_seconds() * 1000)
            if start_ms < prev_end:
                start_ms = prev_end
            if end_ms <= start_ms:
                end_ms = start_ms + 500
            cues.append((cue.content, start_ms, end_ms))
            prev_end = end_ms
        return cues, bytes(audio_data)

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            cues, audio_data = asyncio.run(_tts_with_timeout(text))

            if len(audio_data) < 1024:
                raise RuntimeError("TTS 音频数据不足 1KB")

            out_path.write_bytes(audio_data)
            timing_path.write_text(_json.dumps(cues, ensure_ascii=False), encoding="utf-8")
            return cues

        except asyncio.TimeoutError:
            last_error = f"超时({TTS_TIMEOUT}s)"
        except Exception as e:
            last_error = str(e)

        for p in (out_path, timing_path):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass

        if attempt < max_retries:
            import time as _time
            wait = 5 * attempt
            console.log(f"[yellow]TTS 第{attempt}次失败({last_error})，{wait}秒后重试...[/yellow]")
            _time.sleep(wait)

    # 3 次重试都失败 → 回退到基础 TTS（整段，无精确断句时间戳）
    console.log(f"[red]TTS 3次重试均失败({last_error})，回退到基础模式[/red]")
    if _generate_tts(text, out_path):
        timing_path.write_text("[]", encoding="utf-8")
        return []

    # 基础 TTS 也失败 → 逐句 TTS + ffmpeg 拼接（最后兜底）
    console.log(f"[yellow]整段TTS失败，回退到逐句模式 (共{len(_split_subs(text))}句)[/yellow]")
    _sentence_tts_fallback(text, out_path)
    if out_path.exists() and out_path.stat().st_size > 1024:
        timing_path.write_text("[]", encoding="utf-8")
        return []
    return []


def _get_audio_dur(path: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe","-v","error","-show_entries","format=duration",
             "-of","default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip())
    except Exception:
        return 0


def _clean_srt_text(text: str) -> str:
    """清理字幕文本：去回车、去末尾多余标点"""
    text = text.replace("\r", "").replace("\n", "")
    while text and text[-1] in "。；，,;":
        text = text[:-1]
    return text.strip()


def _build_srt(segments: list[dict]) -> str:
    """生成 SRT 字幕文件内容

    优先使用 seg._timing（edge-tts SentenceBoundary 自然断句时间戳），
    回退到按字数比例估算。
    """
    lines = []
    seq = 1
    t = 0.0
    for seg in segments:
        ap = Path(seg.get("audio_path", ""))
        dur = _get_audio_dur(ap) if ap.exists() else len(seg["speak_text"]) / 5
        if dur <= 0:
            dur = 3

        def fmt(ms):
            h = ms // 3600000
            m = (ms % 3600000) // 60000
            s = (ms % 60000) // 1000
            x = ms % 1000
            return f"{h:02d}:{m:02d}:{s:02d},{x:03d}"

        seg_start_ms = int(t * 1000)
        seg_end_ms = int((t + dur) * 1000)

        # 优先用 edge-tts SentenceBoundary 精确时间戳
        timing = seg.get("_timing")
        if timing:
            # 拼接全部 timing 文本，用 _split_subs 语义分句，再按字符位置映射时间
            full_text = "".join(t[0] for t in timing)
            subs = _split_subs(full_text)
            # 构建字符位置→毫秒映射
            cp = 0  # 当前字符位置
            ti = 0  # timing 索引
            ti_cp = 0  # timing 块内偏移
            for sub in subs:
                sub_start_cp = cp
                sub_end_cp = cp + len(sub)
                # 查 sub_start_cp 落在哪个 timing 块
                s_ti, s_ti_cp, _ = _find_timing_pos(timing, sub_start_cp)
                # 查 sub_end_cp 落在哪个 timing 块
                e_ti, e_ti_cp, _ = _find_timing_pos(timing, sub_end_cp)
                start = seg_start_ms + timing[s_ti][1] + int((timing[s_ti][2] - timing[s_ti][1]) * (s_ti_cp / max(len(timing[s_ti][0]), 1)))
                end = seg_start_ms + timing[e_ti][1] + int((timing[e_ti][2] - timing[e_ti][1]) * (e_ti_cp / max(len(timing[e_ti][0]), 1)))
                start = max(start, seg_start_ms)
                end = min(end, seg_end_ms)
                if start >= end:
                    end = start + 500
                lines.append(str(seq))
                lines.append(f"{fmt(int(start))} --> {fmt(int(end))}")
                lines.append(_clean_srt_text(sub))
                lines.append("")
                seq += 1
                cp = sub_end_cp
        else:
            # 回退：按朗读字数加权估算（标点不计入朗读，加停顿时间）
            sentences = _split_subs(seg["speak_text"])
            seg_dur_ms = max(seg_end_ms - seg_start_ms, 500)
            PAUSE_MAJOR = 300   # 句号/感叹号/问号 停顿 (ms)
            PAUSE_MINOR = 150   # 逗号/分号/冒号 停顿 (ms)
            PAUSE_SENTENCE = 200  # 每句话说完的句间停顿 (ms)
            spoken_counts = []
            pause_times = []
            for sent in sentences:
                spoken = 0
                pauses = 0
                for c in sent:
                    if c in '，、；,;:：':
                        pauses += PAUSE_MINOR
                    elif c in '。！？.!?':
                        pauses += PAUSE_MAJOR
                    elif not c.isspace():
                        spoken += 1
                spoken_counts.append(max(spoken, 1))
                pause_times.append(pauses + PAUSE_SENTENCE)
            total_spoken = sum(spoken_counts)
            total_pauses = sum(pause_times)
            speech_budget = max(seg_dur_ms - total_pauses, 500 * len(sentences))
            sent_start = seg_start_ms
            for i, sent in enumerate(sentences):
                speech_dur = speech_budget * (spoken_counts[i] / total_spoken)
                sent_dur = speech_dur + pause_times[i]
                sent_end = int(sent_start + sent_dur)
                if i == len(sentences) - 1:
                    sent_end = seg_end_ms
                if sent_end <= sent_start:
                    sent_end = sent_start + 500
                lines.append(str(seq))
                lines.append(f"{fmt(int(sent_start))} --> {fmt(int(sent_end))}")
                lines.append(_clean_srt_text(sent))
                lines.append("")
                seq += 1
                sent_start = sent_end

        t += dur

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

        t += dur

    ass_path.write_text("\n".join(lines), encoding="utf-8")
    return ass_path


def _is_cjk(ch: str) -> bool:
    """判断是否为 CJK 字符（中日韩统一表意文字）"""
    return '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿'


def _find_timing_pos(timing: list, char_pos: int) -> tuple[int, int, tuple]:
    """在 timing 块列表中查找字符位置 char_pos 所属的块

    返回 (timing_index, offset_within_chunk, (text, start_ms, end_ms))
    """
    cp = 0
    for i, (text, s_ms, e_ms) in enumerate(timing):
        if cp + len(text) >= char_pos or i == len(timing) - 1:
            return i, char_pos - cp, (text, s_ms, e_ms)
        cp += len(text)
    # fallback: 最后一个块
    last = timing[-1]
    return len(timing) - 1, len(last[0]), last


def _split_subs(text: str, max_chars: int = 28) -> list[str]:
    """按标点切分为字幕短句

    切分优先级（从高到低）：
    1. CJK 强标点（。！？）— 天然句边界
    2. 普通标点（；，,;!?）— 从句边界
    3. CJK ↔ ASCII 切换点 — 中英文交界
    4. CJK 侧的空格 — 中文旁的空白
    5. 硬上限 max_chars（仅在无更好切点时使用）

    注意：ASCII 序列内部的空格（如英文专名"Deadman All Stars"）不会被切断。
    """
    result = []
    cur = ""
    for c in text:
        cur += c
        if c in "。！？" and len(cur) >= 6:
            result.append(cur.strip())
            cur = ""
        elif c in "；，,;!?" and len(cur) >= 6:
            result.append(cur.strip())
            cur = ""
        elif len(cur) >= max_chars:
            split_at = _find_best_split(cur)
            result.append(cur[:split_at].strip())
            cur = cur[split_at:].lstrip()
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


def _find_best_split(cur: str) -> int:
    """在 cur 中找最佳切分点，全字符串搜索

    关键规则：ASCII 序列内的空格不是合法切分点（保护英文专名）。
    例如 "Deadman All Stars" 中的所有空格都会被跳过。
    """
    n = len(cur)

    # 第一遍：找强标点（。！？）
    for j in range(n - 1, -1, -1):
        if cur[j] in "。！？":
            return j + 1

    # 第二遍：找普通标点
    for j in range(n - 1, -1, -1):
        if cur[j] in "；，,;!?":
            return j + 1

    # 第三遍：找 CJK ↔ ASCII 切换点（中英文交界，最可靠）
    for j in range(n - 1, 0, -1):
        if _is_cjk(cur[j - 1]) != _is_cjk(cur[j]):
            return j

    # 第四遍：找空格，但仅当两侧至少有一侧是 CJK（不切英文短语内部）
    for j in range(n - 1, -1, -1):
        if cur[j] == ' ':
            left_cjk = j > 0 and _is_cjk(cur[j - 1])
            right_cjk = j < n - 1 and _is_cjk(cur[j + 1])
            if left_cjk or right_cjk:
                return j + 1

    # 无可用的自然切点 → 向前找任何空格或边界（包括 ASCII 内部，作为最后手段）
    for j in range(n - 1, -1, -1):
        if cur[j] == ' ':
            return j + 1
        if j > 0 and _is_cjk(cur[j - 1]) != _is_cjk(cur[j]):
            return j

    # 硬切
    return n


def _patch_draft_meta(draft_path: str, draft_name: str, duration_us: int) -> None:
    """修补/创建 draft_meta_info.json

    pyJianYingDraft v0.3.0 复制硬编码模板，draft_name/draft_fold_path/draft_root_path
    均为空，draft_id 硬编码。剪映 10.2 需要这些字段正确才能识别草稿。
    如果 meta 文件不存在，从 pyJianYingDraft 模板创建。
    """
    import shutil as _shutil

    meta_path = os.path.join(draft_path, "draft_meta_info.json")

    # 如果 meta 不存在，从 pyJianYingDraft 模板创建
    if not os.path.exists(meta_path):
        tmpl = _draft.assets.get_asset_path("DRAFT_META_TEMPLATE")
        if os.path.exists(tmpl):
            _shutil.copy(tmpl, meta_path)
        else:
            return

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    now_us = int(time.time() * 1_000_000)
    drive = draft_path[:2]  # e.g. "D:"
    root = os.path.dirname(draft_path)  # e.g. "D:\\\\JianYing\\\\JianyingPro Drafts"

    meta["draft_name"] = draft_name
    meta["draft_fold_path"] = draft_path
    meta["draft_root_path"] = root
    meta["draft_removable_storage_device"] = drive
    meta["draft_id"] = str(uuid.uuid4()).upper()
    meta["draft_new_version"] = ""
    meta["tm_draft_create"] = now_us
    meta["tm_draft_modified"] = now_us
    meta["tm_duration"] = duration_us

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=4)

    # 补充 draft_settings（pyJianYingDraft 不生成此文件）
    settings_path = os.path.join(draft_path, "draft_settings")
    if not os.path.exists(settings_path):
        now_sec = int(time.time())
        with open(settings_path, "w", encoding="utf-8") as f:
            f.write(
                "[General]\n"
                f"draft_create_time={now_sec}\n"
                f"draft_last_edit_time={now_sec}\n"
                "real_edit_seconds=0\n"
                "real_edit_keys=1\n"
            )


def _generate_jianying_draft(segments: list[dict], draft_name: str,
                             srt_path: str = "") -> tuple[bool, str]:
    """根据已生成的素材创建剪映草稿

    调用时机：Step3 build() 完成后（音频+背景图+字幕已就位）
    生成轨道：视频(背景图) + 音频(TTS) + 文字(字幕)

    Args:
        segments: 条目列表，每项含 audio_path/bg_path/speak_text
        draft_name: 草稿名称
        srt_path: SRT 字幕文件路径。若提供则用 import_srt() 导入逐句字幕
                   （文字+时间轴都更精细），否则用 seg.speak_text 整段贴入

    Returns:
        (success, message) — 成功时 message 为草稿文件夹路径
    """
    if not _JY_DRAFT_AVAILABLE:
        console.print("[yellow]剪映集成不可用，跳过草稿生成[/yellow]")
        return False, "剪映集成不可用（pyJianYingDraft 未安装或未检测到草稿目录）"

    try:
        _draft_root = _JY_DRAFT_ROOT
        folder = _draft.DraftFolder(_draft_root)
        draft_path = Path(_draft_root) / draft_name

        script = folder.create_draft(draft_name, 1920, 1080, fps=30, allow_replace=True)

        use_srt = bool(srt_path) and Path(srt_path).exists()

        # 轨道定义（用 SRT 时 import_srt 会自动创建字幕轨道）
        track_specs = [
            _draft.TrackSpec(_draft.TrackType.video, "main"),
            _draft.TrackSpec(_draft.TrackType.audio, "voice"),
        ]
        if not use_srt:
            track_specs.append(_draft.TrackSpec(_draft.TrackType.text, "subtitle"))
        script.append_tracks(track_specs)

        t = 0.0  # 累计时间（秒）
        added = 0

        for seg in segments:
            ap = Path(seg.get("audio_path", ""))
            dur = _get_audio_dur(ap) if ap.exists() else len(seg["speak_text"]) / 5
            if dur <= 0:
                dur = 3

            start_s = t
            end_s = t + dur
            seg_dur = end_s - start_s
            start_str = f"{start_s:.3f}s"
            dur_str = f"{seg_dur:.3f}s"

            # 视频轨道：有图用图，没图用默认占位背景（防止剪映磁吸导致错位）
            bg = seg.get("bg_path", "")
            if not bg or not Path(bg).exists():
                bg = _default_bg()
            try:
                vid = _draft.VideoSegment(bg, _draft.trange(start_str, dur_str))
                script.add_segment(vid, "main")
            except Exception:
                pass

            # 音频轨道：TTS 语音
            if ap.exists():
                try:
                    aud = _draft.AudioSegment(
                        str(ap),
                        _draft.trange(start_str, dur_str),
                        source_timerange=_draft.trange("0s", dur_str),
                        volume=1.0,
                    )
                    script.add_segment(aud, "voice")
                except Exception as e:
                    console.log(f"[yellow]音频添加失败 seg={seg.get('_idx', '?')}: {e}[/yellow]")

            # 字幕：无 SRT 时才用整段 speak_text（回退方案）
            if not use_srt:
                speak_text = seg.get("speak_text", "").strip()
                if speak_text:
                    try:
                        txt = _draft.TextSegment(
                            speak_text[:500],
                            _draft.trange(start_str, dur_str),
                            font=_draft.FontType.SourceHanSansCN_Regular,
                            style=_draft.TextStyle(size=8.0, align=1),
                        )
                        script.add_segment(txt, "subtitle")
                    except Exception:
                        pass

            t += dur
            added += 1

        # 用 SRT 导入逐句字幕（替代整段贴入的文字）
        if use_srt:
            try:
                script.import_srt(
                    srt_path, "subtitle",
                    text_style=_draft.TextStyle(size=8.0, align=1, auto_wrapping=True),
                )
                console.print(f"[green]SRT字幕已导入: {srt_path}[/green]")
            except Exception as e:
                console.print(f"[yellow]SRT导入失败，已跳过字幕: {e}[/yellow]")

        script.save()

        # 修补 pyJianYingDraft 硬编码模板导致的空字段
        draft_path_str = str(draft_path)
        total_duration_us = int(t * 1_000_000)  # 秒 → 微秒
        _patch_draft_meta(draft_path_str, draft_name, total_duration_us)

        console.print(f"[green]剪映草稿已生成: {draft_path_str}[/green]")
        return True, draft_path_str

    except Exception as e:
        msg = f"剪映草稿生成失败: {e}"
        console.print(f"[red]{msg}[/red]")
        return False, msg


def _copy_draft_framework(folder: "_draft.DraftFolder", draft_name: str) -> None:
    """为目标草稿创建完整的空框架目录结构

    pyJianYingDraft 的 create_draft() 只创建 3 个文件，剪映需要更多框架文件/目录
    才能正确渲染多段字幕。此函数从模板复制纯结构文件（不复制任何媒体内容），
    并写入空的 material 引用文件以避免模板 ID 冲突。
    """
    import shutil as _shutil

    _draft_root = _JY_DRAFT_ROOT
    draft_path = Path(_draft_root) / draft_name

    # 找一个框架完整的正常草稿作模板（优先选最近的）
    all_drafts = folder.list_drafts()
    template = None
    candidates = []
    for name in all_drafts:
        if name.startswith("_") or name == draft_name:
            continue
        p = Path(_draft_root) / name
        if (p / "draft_agency_config.json").exists():
            candidates.append((p.stat().st_mtime, name))
    if candidates:
        candidates.sort(reverse=True)
        template = candidates[0][1]

    # 确保目标目录存在
    draft_path.mkdir(parents=True, exist_ok=True)

    # === 从模板复制的纯结构文件（不含素材引用）===
    STRUCT_FILES = [
        "draft_agency_config.json",
        "draft_biz_config.json",
        "performance_opt_info.json",
        "attachment_pc_common.json",
    ]
    if template:
        template_path = Path(_draft_root) / template
        console.print(f"[dim]从模板草稿复制结构: {template}[/dim]")
        for fname in STRUCT_FILES:
            src = template_path / fname
            if src.exists():
                _shutil.copy2(str(src), str(draft_path / fname))
    else:
        console.print("[yellow]未找到模板草稿，使用最小结构[/yellow]")

    # === 必须存在的空目录 ===
    for dname in ["Resources", "Timelines", "adjust_mask", "common_attachment",
                  "matting", "qr_upload", "smart_crop", "subdraft", ".backup",
                  "aigc_material", "color_match"]:
        (draft_path / dname).mkdir(exist_ok=True)

    # === 写入最小空版本（不含任何模板 material ID 引用）===
    (draft_path / "draft_virtual_store.json").write_text(
        '{"draft_materials":[],"draft_virtual_store":[]}', encoding="utf-8")
    (draft_path / "key_value.json").write_text("{}", encoding="utf-8")
    (draft_path / "template-2.tmp").write_text("{}", encoding="utf-8")
    (draft_path / "timeline_layout.json").write_text(
        '{"dockItems":[],"layoutOrientation":1}', encoding="utf-8")

    # draft_meta_info.json 和 draft_settings 由 _patch_draft_meta() 创建


# ========== Flask 路由 ==========

@app.route("/")
def step1():
    """选择条目"""
    # 清理上次工作流的临时文件（裁剪图等），避免旧数据污染新流程
    if WORK_DIR and WORK_DIR.exists():
        for f in WORK_DIR.glob("bg_*.jpg"):
            f.unlink(missing_ok=True)
        for f in WORK_DIR.glob("crop_*.png"):
            f.unlink(missing_ok=True)
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


@app.route("/step2", methods=["GET", "POST"])
def step2():
    """编辑脚本 — POST 选择条目 / GET 返回重新编辑"""
    if request.method == "POST":
        idxs = request.form.getlist("idx")
        selected_idx = set(int(i) for i in idxs)
        selected = [s for s in state["segments"] if s["_idx"] in selected_idx]
        state["selected"] = selected
    else:
        selected = state.get("selected", [])
        # 仅返回重编时补上上次裁剪的图片预览（首次 POST 进入不查，避免旧数据污染）
        if WORK_DIR:
            for seg in selected:
                if not seg.get("image_url"):
                    cropped = WORK_DIR / f"bg_{seg['_idx']:03d}.jpg"
                    if cropped.exists():
                        seg["_display_img"] = f"/api/bg/bg_{seg['_idx']:03d}.jpg?t={int(cropped.stat().st_mtime)}"
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
            {"status": "waiting", "text": "生成字幕文件...", "detail": ""},
            {"status": "waiting", "text": "生成剪映草稿...", "detail": ""},
        ],
    }

    # 在后台线程执行
    def build():
        p = state["_progress"]
        s = state["selected"]
        WORK_DIR.mkdir(parents=True, exist_ok=True)

        # 1) 图片 — URL 优先，Step 2 裁剪的 bg_{idx}.jpg 作兜底
        p["steps"][0]["status"] = "running"
        p["steps"][0]["text"] = f"准备 {len(s)} 张图片..."
        imgs = 0
        for seg in s:
            idx = seg["_idx"]
            img_url = seg.get("image_url", "")
            bg = None
            if img_url:
                local = _download_image(img_url, idx)
                if local:
                    bg = _prepare_bg(local, idx)
            if not bg:
                cropped = WORK_DIR / f"bg_{idx:03d}.jpg"
                if cropped.exists():
                    bg = str(cropped)
            if bg:
                seg["bg_path"] = bg
                imgs += 1
            p["steps"][0]["text"] = f"准备图片: {imgs}/{len(s)}"
        for seg in s:
            if not seg.get("bg_path"):
                seg["bg_path"] = _default_bg()
        p["steps"][0]["status"] = "done"
        p["steps"][0]["text"] = f"图片就绪: {imgs}/{len(s)}"

        # 2) TTS — 文本未变则跳过，变化时才重新生成
        p["steps"][1]["status"] = "running"
        audio_dir = WORK_DIR / "audio"
        audio_dir.mkdir(exist_ok=True)
        done_tts = 0
        skipped_tts = 0
        for i, seg in enumerate(s):
            ap = audio_dir / f"seg_{i:03d}.mp3"
            seg["audio_path"] = str(ap)
            text_hash = hashlib.md5(seg["speak_text"].encode()).hexdigest()
            if ap.exists() and seg.get("_tts_hash") == text_hash:
                timing_path = ap.with_suffix(".timing.json")
                if timing_path.exists():
                    with open(timing_path, encoding="utf-8") as f:
                        seg["_timing"] = json.load(f)
                skipped_tts += 1
                continue
            # 文本已变 → 删除旧音频和时间戳缓存，强制重新生成
            seg["_tts_hash"] = text_hash
            if ap.exists():
                ap.unlink()
            timing_path = ap.with_suffix(".timing.json")
            if timing_path.exists():
                timing_path.unlink()
            console.log(f"[dim]TTS seg_{i:03d}: {seg['char_count']}字...[/dim]")
            p["steps"][1]["text"] = f"TTS: {done_tts + skipped_tts}/{len(s)} — 正在生成 seg_{i:03d}..."
            seg["_timing"] = _generate_tts_with_timing(seg["speak_text"], ap)
            if ap.exists():
                done_tts += 1
            else:
                console.log(f"[red]TTS seg_{i:03d} 失败: 音频未生成[/red]")
            p["steps"][1]["text"] = f"TTS: {done_tts + skipped_tts}/{len(s)}"
            if i < len(s) - 1:
                time.sleep(5)
        total = done_tts + skipped_tts
        p["steps"][1]["status"] = "done"
        detail = f" 新生成 {done_tts} 段"
        if skipped_tts > 0:
            detail += f"，复用 {skipped_tts} 段（文本未变）"
        p["steps"][1]["text"] = f"TTS 完成: {total}/{len(s)}{detail}"

        # 3) SRT + ASS 字幕
        p["steps"][2]["status"] = "running"
        srt_content = _build_srt(s)
        state["_srt_content"] = srt_content
        (WORK_DIR / "subtitles.srt").write_text(srt_content.replace("\r", ""), encoding="utf-8", newline="")
        _build_ass(s, WORK_DIR / "subtitles.ass")
        state["_ass_path"] = str(WORK_DIR / "subtitles.ass")
        total_dur = int(sum(_get_audio_dur(Path(seg['audio_path'])) for seg in s if Path(seg.get('audio_path', '')).exists()))
        p["steps"][2]["status"] = "done"
        p["steps"][2]["text"] = f"字幕已生成，总时长约 {total_dur // 60} 分钟"

        # 4) 剪映草稿
        p["steps"][3]["status"] = "running"
        md_name = Path(state["md_path"]).stem
        _srt = str(TEMP_DIR / md_name / "subtitles.srt")
        draft_ok, draft_msg = _generate_jianying_draft(s, f"{md_name}_周刊", srt_path=_srt)
        if draft_ok:
            state["_jy_draft_path"] = draft_msg
            p["steps"][3]["status"] = "done"
            p["steps"][3]["text"] = f"剪映草稿已生成: {draft_msg}"
            p["steps"][3]["detail"] = "打开剪映 10.x → 草稿列表中找到该草稿，可直接编辑和导出"
        else:
            p["steps"][3]["status"] = "done"
            p["steps"][3]["text"] = f"剪映草稿: {draft_msg}"
            p["steps"][3]["detail"] = ""

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
        srt_path.write_text(srt_content.replace("\r", ""), encoding="utf-8", newline="")
        return send_file(srt_path, as_attachment=True, download_name="subtitles.srt")

    # 保存编辑后的 SRT 到剪映草稿读取的路径（newline="" 避免 Windows 换行符破坏格式）
    md_name = Path(state["md_path"]).stem
    edited_srt = TEMP_DIR / md_name / "subtitles.srt"
    edited_srt.write_text(srt_content.replace("\r", ""), encoding="utf-8", newline="")
    (WORK_DIR / "subtitles_edited.srt").write_text(srt_content.replace("\r", ""), encoding="utf-8", newline="")

    # 自动用编辑后的 SRT 重新生成剪映草稿
    segs = state.get("selected", [])
    draft_path = ""
    if segs:
        _srt = str(edited_srt)
        ok, msg = _generate_jianying_draft(segs, f"{md_name}_周刊", srt_path=_srt)
        if ok:
            state["_jy_draft_path"] = msg
            draft_path = msg

    return render_template_string(
        STEP5_TEMPLATE,
        draft_path=draft_path,
        work_dir=str(WORK_DIR),
    )


def _build_ass_from_srt(srt_text: str, ass_path: str):
    """从 SRT 文本生成 ASS（简化转换）"""
    # 统一换行符（Windows textarea 会提交 \r\n）
    srt_text = srt_text.replace("\r\n", "\n").replace("\r", "\n")
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


@app.route("/api/polish", methods=["POST"])
def api_polish():
    """AI 润色口播脚本 — 支持用户自定义指令"""
    data = request.get_json()
    text = data.get("text", "")
    instruction = data.get("instruction", "")
    if not text.strip():
        return jsonify({"error": "文本为空"})

    try:
        from config import OPENAI_API_KEY, OPENAI_BASE_URL
        from openai import OpenAI
        if not OPENAI_API_KEY or OPENAI_API_KEY == "sk-xxx":
            return jsonify({"error": "API Key 未配置"})

        client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

        system_msg = (
            "你是口播稿润色助手。将以下文字改写为适合语音朗读的口播稿。"
            "默认要求：更口语化自然，像在跟朋友聊天，去掉书面化的长句和套话；"
            "品牌名/产品名/数字/日期保留不变；长度与原文字数相当。"
            "直接返回润色后文本，不要加任何前缀说明。"
        )
        user_msg = text
        if instruction:
            user_msg = f"【润色指令】{instruction}\n\n【原文】{text}"

        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
            max_tokens=1500,
        )
        polished = resp.choices[0].message.content.strip() if resp.choices[0].message.content else ""
        return jsonify({"polished": polished if polished else text})
    except Exception as e:
        return jsonify({"error": str(e)})


# ========== 图片上传 & 裁剪 ==========

@app.route("/api/upload-image", methods=["POST"])
def api_upload_image():
    """上传图片到临时目录，返回 URL 供 Cropper 加载"""
    if "file" not in request.files:
        return jsonify({"error": "未选择文件"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "文件名为空"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        return jsonify({"error": f"不支持的格式: {ext}"}), 400

    upload_dir = WORK_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = upload_dir / filename
    file.save(str(filepath))
    return jsonify({"url": f"/api/uploads/{filename}", "filename": filename})


@app.route("/api/uploads/<filename>")
def api_serve_upload(filename):
    """提供上传的临时图片"""
    upload_dir = WORK_DIR / "uploads"
    return send_file(upload_dir / filename)


@app.route("/api/save-image/<int:idx>", methods=["POST"])
def api_save_image(idx):
    """保存裁剪后的图片为 bg_{idx:03d}.jpg（1920x1080）"""
    data = request.get_json()
    img_b64 = data.get("image", "")
    if not img_b64:
        return jsonify({"error": "无图片数据"}), 400

    import base64
    # 去掉 data:image/...;base64, 前缀
    if "," in img_b64:
        img_b64 = img_b64.split(",", 1)[1]

    try:
        img_bytes = base64.b64decode(img_b64)
    except Exception:
        return jsonify({"error": "Base64 解码失败"}), 400

    # 保存原始裁剪图
    raw_path = WORK_DIR / f"crop_{idx:03d}.png"
    raw_path.write_bytes(img_bytes)

    # 缩放/裁剪到 1920x1080
    dest = WORK_DIR / f"bg_{idx:03d}.jpg"
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(raw_path),
            "-vf",
            f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_W}:{VIDEO_H}",
            "-q:v", "2", str(dest),
        ], capture_output=True, check=True, timeout=30)
        raw_path.unlink()  # 清理临时文件
        return jsonify({"ok": True, "url": f"/api/bg/bg_{idx:03d}.jpg"})
    except Exception as e:
        return jsonify({"error": f"图片处理失败: {e}"}), 500


@app.route("/api/bg/<path:filename>")
def api_serve_bg(filename):
    """提供 WORK_DIR 中的背景图片"""
    return send_file(WORK_DIR / filename)


@app.route("/api/jianying-draft")
def api_jianying_draft():
    """生成/重新生成剪映草稿"""
    segs = state.get("selected", [])
    if not segs:
        return jsonify({"success": False, "error": "尚未选择条目"})
    md_name = Path(state["md_path"]).stem
    _srt = str(TEMP_DIR / md_name / "subtitles.srt")
    ok, msg = _generate_jianying_draft(segs, f"{md_name}_周刊", srt_path=_srt)
    if ok:
        state["_jy_draft_path"] = msg
    return jsonify({"success": ok, "message": msg})


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
