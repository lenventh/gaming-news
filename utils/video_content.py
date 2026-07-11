"""B站视频内容深度提取工具

三级策略提取视频中的文字信息：
  L1: B站 AI 字幕（大多数中文视频有，无额外依赖）
  L2: yt-dlp 下载音频 + whisper 转录（需要 yt-dlp + ffmpeg + faster-whisper）
  L3: 仅用标题+简介（无可用工具时兜底）

用法：
  from utils.video_content import extract_video_content
  text = extract_video_content(bvid="BVxxx", page=page)  # page 是 Playwright page 对象
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


def _check_tool(name: str) -> bool:
    """检查命令行工具是否可用"""
    try:
        subprocess.run([name, "--version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def extract_subtitles_via_api(bvid: str, page) -> str:
    """L1: 通过 B站 player API 提取 AI 字幕文字"""
    try:
        # 获取 cid
        result = page.evaluate(f"""
            async () => {{
                try {{
                    const res = await fetch('https://api.bilibili.com/x/web-interface/view?bvid={bvid}');
                    const json = await res.json();
                    if (json.code !== 0 || !json.data) return null;
                    return {{ cid: json.data.cid }};
                }} catch(e) {{ return null; }}
            }}
        """)
        if not result or not result.get("cid"):
            return ""

        cid = result["cid"]

        # 获取字幕列表
        subtitle_result = page.evaluate(f"""
            async () => {{
                try {{
                    const res = await fetch(
                        'https://api.bilibili.com/x/player/wbi/v2?bvid={bvid}&cid={cid}',
                        {{ headers: {{ 'Referer': 'https://www.bilibili.com/video/{bvid}' }} }}
                    );
                    const json = await res.json();
                    if (json.code !== 0 || !json.data?.subtitle?.subtitles) return [];
                    return json.data.subtitle.subtitles.map(s => ({{
                        lan: s.lan || '',
                        lan_doc: s.lan_doc || '',
                        url: s.subtitle_url || '',
                    }}));
                }} catch(e) {{ return []; }}
            }}
        """)
        if not subtitle_result:
            return ""

        # 优先中文
        zh_sub = None
        for s in subtitle_result:
            lan = s.get("lan", "")
            if lan.startswith("ai-zh") or lan == "zh-Hans":
                zh_sub = s
                break
        if not zh_sub:
            for s in subtitle_result:
                if s.get("lan", "").startswith("zh"):
                    zh_sub = s
                    break
        if not zh_sub and subtitle_result:
            zh_sub = subtitle_result[0]

        if not zh_sub or not zh_sub.get("url"):
            return ""

        sub_url = zh_sub["url"]
        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url

        text = page.evaluate(f"""
            async () => {{
                try {{
                    const res = await fetch({json.dumps(sub_url)});
                    const json = await res.json();
                    if (!json.body) return '';
                    return json.body
                        .map(b => b.content || '')
                        .filter(c => c.trim())
                        .join(' ');
                }} catch(e) {{ return ''; }}
            }}
        """)
        return (text or "").strip()

    except Exception:
        return ""


def extract_audio_and_transcribe(bvid: str, title: str = "") -> str:
    """L2: yt-dlp 下载音频 + whisper 转录

    需要: yt-dlp, ffmpeg, faster-whisper (pip install faster-whisper)
    """
    if not _check_tool("yt-dlp"):
        return ""
    if not _check_tool("ffmpeg"):
        return ""

    try:
        import faster_whisper
    except ImportError:
        return ""

    url = f"https://www.bilibili.com/video/{bvid}"

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, f"{bvid}.mp3")

        # yt-dlp 下载音频
        try:
            subprocess.run(
                [
                    "yt-dlp",
                    "-x", "--audio-format", "mp3",
                    "-o", audio_path,
                    "--max-filesize", "50m",  # 限制 50MB
                    "--socket-timeout", "30",
                    url,
                ],
                capture_output=True,
                timeout=120,
                check=True,
            )
        except Exception:
            return ""

        if not os.path.exists(audio_path):
            return ""

        # whisper 转录（使用 tiny 模型，速度快）
        try:
            model = faster_whisper.WhisperModel("tiny", device="cpu", compute_type="int8")
            segments, _ = model.transcribe(audio_path, language="zh", beam_size=5)
            text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
            return text[:2000]
        except Exception:
            return ""

    return ""


def extract_video_content(bvid: str, page, title: str = "") -> str:
    """三级策略提取视频文字内容

    Args:
        bvid: B站视频 BV 号
        page: Playwright page 对象（用于 API 调用）
        title: 视频标题（用于兜底）

    Returns:
        提取到的文字内容（字幕/转录），失败返回空字符串
    """
    # L1: B站 AI 字幕（最快，0 额外依赖）
    text = extract_subtitles_via_api(bvid, page)
    if text and len(text) > 50:
        return text

    # L2: yt-dlp + whisper（较慢，需要依赖）
    text = extract_audio_and_transcribe(bvid, title)
    if text and len(text) > 30:
        return text

    # L3: 兜底
    return ""


def is_transcription_available() -> bool:
    """检查 L2 转录功能是否可用"""
    return _check_tool("yt-dlp") and _check_tool("ffmpeg")
