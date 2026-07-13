"""口播视频生成器：Markdown 周刊 → AI配音 + 配图 + 字幕 → MP4

依赖: edge-tts (免费微软AI配音), ffmpeg (视频合成)
"""

import os
import re
import sys
import json
import time
import shutil
import tempfile
import hashlib
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from rich.console import Console
from openai import OpenAI

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

console = Console()

# ========== 标题翻译 (via LLM) ==========
_translate_client = None


def _get_translate_client():
    global _translate_client
    if _translate_client is None:
        from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
        if OPENAI_API_KEY and OPENAI_API_KEY != "sk-xxx":
            _translate_client = OpenAI(
                api_key=OPENAI_API_KEY,
                base_url=OPENAI_BASE_URL,
            )
    return _translate_client


def _translate_english_title(title: str) -> str:
    """用 LLM 将英文标题翻译为中文（含游戏/产品专有名词保留）"""
    # 如果中文占多数，跳过
    chinese_chars = sum(1 for c in title if '一' <= c <= '鿿')
    if chinese_chars > len(title) * 0.3:
        return title

    client = _get_translate_client()
    if not client:
        return title

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{
                "role": "user",
                "content": (
                    "将以下游戏硬件新闻标题翻译为简体中文。"
                    "品牌/产品/系统名保留原文不翻译（包括: Steam Deck, Switch, Xbox, "
                    "PlayStation, ROG Ally, AYANEO, GPD, MSI Claw, Legion Go, Valve, "
                    "Nintendo, Sony, AMD, Intel, Quest, PSVR, VR, Proton, BIOS, "
                    "Retroid, Odin, Anbernic, Miyoo, TrimUI, PowKiddy, ONEXPLAYER 等），"
                    "其余英文翻译为中文。只返回译文：\n\n"
                    + title
                ),
            }],
            temperature=0.1,
            max_tokens=200,
        )
        translated = response.choices[0].message.content.strip()
        if translated and len(translated) > 0:
            console.log(f"  [dim]译: {title[:50]} → {translated[:50]}[/dim]")
            return translated
    except Exception as e:
        console.log(f"[yellow]  翻译失败: {e}[/yellow]")

    return title

# ========== 配置 ==========
TEMP_DIR = Path(tempfile.gettempdir()) / "gaming_news_video"
VOICE = "zh-CN-XiaoxiaoNeural"  # 微软晓晓，女声新闻风格
# 备选男声: zh-CN-YunxiNeural
IMAGE_CACHE_DIR = Path(__file__).parent.parent / "storage" / "video_cache"
FPS = 24
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
SUBTITLE_FONT_SIZE = 32
MAX_PARALLEL_TTS = 3
TTS_RATE = "+10%"  # 语速略快，适合口播

# ========== Markdown 解析 ==========

def _download_image(url: str, dest_dir: Path) -> Path | None:
    """下载图片到本地，返回路径"""
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    ext = ".jpg"
    if ".png" in url.lower():
        ext = ".png"
    elif ".webp" in url.lower():
        ext = ".webp"
    dest = dest_dir / f"{url_hash}{ext}"
    if dest.exists():
        return dest
    try:
        resp = requests.get(url, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (compatible; VideoBot/1.0)"
        })
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return dest
    except Exception as e:
        console.log(f"[yellow]  图片下载失败: {url[:60]} - {e}[/yellow]")
        return None


def _prepare_image_for_video(img_path: Path, dest_dir: Path) -> Path | None:
    """将图片缩放/裁剪到 1920x1080，加暗色遮罩便于显示字幕"""
    dest = dest_dir / f"bg_{img_path.stem}.jpg"
    if dest.exists():
        return dest
    try:
        # 缩放居中裁剪到 1920x1080 + 半透明黑色遮罩
        cmd = [
            "ffmpeg", "-y", "-i", str(img_path),
            "-vf",
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
            f"drawbox=x=0:y=0:w={VIDEO_WIDTH}:h={VIDEO_HEIGHT}:color=black@0.3:t=fill",
            "-q:v", "2",
            str(dest),
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        return dest
    except Exception as e:
        console.log(f"[yellow]  图片处理失败: {e}[/yellow]")
        return None


def parse_weekly_markdown(md_text: str) -> list[dict]:
    """解析周刊 Markdown，提取每条新闻的结构化信息"""
    segments: list[dict] = []
    lines = md_text.split("\n")

    current_category = ""
    current_subcategory = ""
    in_reference = False

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 跳过参考资料区域
        if re.match(r'^##\s+参考资料', line):
            in_reference = True
            i += 1
            continue
        if in_reference:
            i += 1
            continue

        # 板块标题
        m = re.match(r'^##\s+(.+)', line)
        if m and not line.startswith("####"):
            current_category = m.group(1).strip()
            current_subcategory = ""
            i += 1
            continue

        # 子板块标题
        m = re.match(r'^###\s+(.+)', line)
        if m:
            current_subcategory = m.group(1).strip()
            i += 1
            continue

        # 新闻条目
        m = re.match(r'^####\s+\d+\.\s+(.+)', line)
        if m:
            title = m.group(1).strip()
            image_url = ""
            content = ""
            analysis = ""
            source = ""

            i += 1
            # 读取接下来的行
            while i < len(lines) and not re.match(r'^(####|###|##|--)', lines[i]):
                sub = lines[i].strip()

                # 配图
                img_m = re.match(r'!\[配图\]\((.+)\)', sub)
                if img_m:
                    image_url = img_m.group(1)
                    i += 1
                    continue

                # 新闻内容
                ct_m = re.match(r'-\s*新闻内容[：:]\s*(.+)', sub)
                if ct_m:
                    content = ct_m.group(1).strip()
                    i += 1
                    continue

                # 简要分析
                an_m = re.match(r'-\s*简要分析[：:]\s*(.+)', sub)
                if an_m:
                    analysis = an_m.group(1).strip()
                    i += 1
                    continue

                # 来源
                src_m = re.match(r'-\s*来源[：:]\s*(.+)', sub)
                if src_m:
                    source = src_m.group(1).strip()
                    i += 1
                    continue

                i += 1

            # 清理 Markdown 残留
            content = re.sub(r'\[|\]|\*|`|!\[配图\]\(.*?\)', '', content).strip()
            analysis = re.sub(r'\[|\]|\*|`|!\[配图\]\(.*?\)', '', analysis).strip()

            # 英文标题翻译为中文（用于口播配音）
            spoken_title = _translate_english_title(title)
            speak_text = f"{spoken_title}。{content} {analysis}"

            segments.append({
                "title": title,
                "display_title": spoken_title,
                "image_url": image_url,
                "content": content,
                "analysis": analysis,
                "source": source,
                "category": current_category,
                "subcategory": current_subcategory,
                "speak_text": speak_text,
                "char_count": len(speak_text),
            })
            continue

        i += 1

    return segments


# ========== TTS 配音 ==========

def _generate_tts(text: str, output_path: Path, voice: str = VOICE) -> bool:
    """用 edge-tts 生成单段配音"""
    if output_path.exists():
        return True
    try:
        import asyncio
        import edge_tts

        async def _run():
            communicate = edge_tts.Communicate(
                text, voice,
                rate=TTS_RATE,
            )
            await communicate.save(str(output_path))

        asyncio.run(_run())
        return output_path.exists()
    except Exception as e:
        console.log(f"[red]  TTS 失败: {e}[/red]")
        return False


def _tts_batch(segments: list[dict], audio_dir: Path) -> list[dict]:
    """批量生成 TTS 配音，并发处理"""
    console.print(f"\n[yellow]  [TTS]  TTS 配音 ({VOICE}): {len(segments)} 段，并发 {MAX_PARALLEL_TTS}[/yellow]")

    success = 0
    tasks = [(i, seg) for i, seg in enumerate(segments)]
    # 分批提交避免过多并发
    for batch_start in range(0, len(tasks), MAX_PARALLEL_TTS):
        batch = tasks[batch_start:batch_start + MAX_PARALLEL_TTS]
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_TTS) as ex:
            futures = {}
            for i, seg in batch:
                audio_path = audio_dir / f"seg_{i:03d}.mp3"
                seg["audio_path"] = str(audio_path)
                futures[ex.submit(_generate_tts, seg["speak_text"], audio_path)] = i
            for fut in as_completed(futures):
                if fut.result():
                    success += 1
        console.log(f"[dim]    进度: {success}/{len(segments)}[/dim]")

    console.print(f"  TTS 完成: [green]{success}/{len(segments)}[/green]")
    return segments


# ========== 字幕生成 ==========

def _generate_ass_subtitles(segments: list[dict], ass_path: Path) -> Path:
    """生成 ASS 字幕文件（带音频时长同步）"""
    lines = [
        "[Script Info]",
        "Title: 游戏设备周报",
        "ScriptType: v4.00+",
        f"PlayResX: {VIDEO_WIDTH}",
        f"PlayResY: {VIDEO_HEIGHT}",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, Outline, Shadow, "
        "Bold, Italic, Alignment, MarginL, MarginR, MarginV, Encoding",
        (
            "Style: Default,Microsoft YaHei,36,&H00FFFFFF,&H00000000,&H80000000,"
            "1,0,2,80,80,120,1"
        ),
        (
            "Style: Title,Microsoft YaHei,44,&H00FFFF00,&H00000000,&H80000000,"
            "1,0,2,80,80,80,1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    current_time = 0.0  # 秒

    for i, seg in enumerate(segments):
        audio_path = Path(seg["audio_path"])
        duration = _get_audio_duration(audio_path)
        if duration <= 0:
            duration = len(seg["speak_text"]) / 5.0  # 估算: ~5字/秒

        start_ms = int(current_time * 1000)
        end_ms = int((current_time + duration) * 1000)
        start_str = f"{start_ms//3600000:01d}:{(start_ms%3600000)//60000:02d}:{(start_ms%60000)//1000:02d}.{start_ms%1000//10:02d}"
        end_str = f"{end_ms//3600000:01d}:{(end_ms%3600000)//60000:02d}:{(end_ms%60000)//1000:02d}.{end_ms%1000//10:02d}"

        # 标题字幕（前2秒）
        title_end_ms = min(end_ms, start_ms + 2500)
        title_end_str = (
            f"{title_end_ms//3600000:01d}:{(title_end_ms%3600000)//60000:02d}:"
            f"{(title_end_ms%60000)//1000:02d}.{title_end_ms%1000//10:02d}"
        )
        title_text = seg["title"].replace(",", "，")
        lines.append(
            f"Dialogue: 0,{start_str},{title_end_str},Title,,0,0,0,,{title_text}"
        )

        # 口播内容字幕（逐句切分）
        speak_text = seg["speak_text"].replace(",", "，")
        sentences = _split_into_sentences(speak_text, max_chars=30)
        sentence_duration = (duration - 2.5) / max(len(sentences), 1)
        sent_start = start_ms + 2500

        for sent in sentences:
            sent_end = min(end_ms, sent_start + int(sentence_duration * 1000))
            sent_start_str = (
                f"{sent_start//3600000:01d}:{(sent_start%3600000)//60000:02d}:"
                f"{(sent_start%60000)//1000:02d}.{sent_start%1000//10:02d}"
            )
            sent_end_str = (
                f"{sent_end//3600000:01d}:{(sent_end%3600000)//60000:02d}:"
                f"{(sent_end%60000)//1000:02d}.{sent_end%1000//10:02d}"
            )
            lines.append(
                f"Dialogue: 0,{sent_start_str},{sent_end_str},Default,,0,0,0,,{sent}"
            )
            sent_start = sent_end

        current_time += duration
        # 段间短暂停顿
        current_time += 0.3

    ass_path.write_text("\n".join(lines), encoding="utf-8")
    return ass_path


def _get_audio_duration(audio_path: Path) -> float:
    """获取 MP3 音频时长（秒）"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0


def _split_into_sentences(text: str, max_chars: int = 30) -> list[str]:
    """将文本按标点切分为适合字幕显示的短句"""
    sentences = []
    current = ""
    for char in text:
        current += char
        if char in "。！？；，,;!?":
            if len(current) >= 6:
                sentences.append(current.strip())
                current = ""
            elif len(current) >= max_chars:
                sentences.append(current.strip())
                current = ""
    if current.strip():
        sentences.append(current.strip())
    # 合并过短的句子
    merged = []
    buf = ""
    for s in sentences:
        if len(buf) + len(s) <= max_chars:
            buf += s
        else:
            if buf:
                merged.append(buf)
            buf = s
    if buf:
        merged.append(buf)
    return merged if merged else [text[:max_chars]]


# ========== 视频合成 ==========

def _compose_clip(
    seg: dict, seg_index: int,
    image_dir: Path, work_dir: Path,
) -> Path | None:
    """将单个新闻段合成为视频片段"""
    clip_path = work_dir / f"clip_{seg_index:03d}.mp4"
    if clip_path.exists():
        return clip_path

    audio_path = Path(seg["audio_path"])
    if not audio_path.exists():
        console.log(f"[yellow]  跳过(无音频): {seg['title'][:30]}[/yellow]")
        return None

    duration = _get_audio_duration(audio_path)
    if duration <= 0:
        return None

    # 准备背景图
    bg_path = seg.get("bg_path")
    if not bg_path or not Path(bg_path).exists():
        return None

    try:
        # 图片 + 音频 → 视频片段
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(bg_path),
            "-i", str(audio_path),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-tune", "stillimage",
            "-c:a", "aac", "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            "-t", str(duration),
            "-shortest",
            str(clip_path),
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=60)
        if clip_path.exists():
            seg["duration"] = duration
            return clip_path
    except Exception as e:
        console.log(f"[red]  合成失败 [{seg_index}]: {e}[/red]")

    return None


def _concat_clips(clip_paths: list[Path], output_path: Path) -> bool:
    """合并所有片段为最终视频"""
    concat_list = output_path.parent / "concat_list.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{p.as_posix()}'\n")

    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=120)
        return output_path.exists()
    except Exception as e:
        console.log(f"[red]  合并失败: {e}[/red]")
        return False


def _burn_subtitles(video_path: Path, ass_path: Path, output_path: Path) -> bool:
    """将字幕烧录到视频中"""
    try:
        # Windows: 将路径转为 ffmpeg 能处理的格式
        # C:/Users/... -> C\:/Users/... (冒号前加反斜杠)
        ass_fixed = str(ass_path.resolve()).replace("\\", "/")
        ass_fixed = ass_fixed.replace(":", "\\:")
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vf", f"subtitles='{ass_fixed}'",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "copy",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=300)
        return output_path.exists()
    except Exception as e:
        console.log(f"[red]  字幕烧录失败: {e}[/red]")
        return False


# ========== 主流程 ==========

def build_video(md_path: str, output_path: str | None = None) -> str | None:
    """主入口：周刊 Markdown → 口播视频

    Args:
        md_path: 周刊 Markdown 文件路径
        output_path: 输出 MP4 路径，默认 output/ 目录下

    Returns:
        输出视频路径，失败返回 None
    """
    md_file = Path(md_path)
    if not md_file.exists():
        console.log(f"[red]文件不存在: {md_path}[/red]")
        return None

    if output_path is None:
        output_dir = md_file.parent
        output_path = str(output_dir / f"{md_file.stem}_video.mp4")
    out_path = Path(output_path)

    week_label = md_file.stem
    console.print(f"\n[bold cyan][VIDEO] 口播视频生成: {week_label}[/bold cyan]")

    # 准备临时目录
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    work_dir = TEMP_DIR / week_label
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    audio_dir = work_dir / "audio"
    image_dir = work_dir / "images"
    audio_dir.mkdir()
    image_dir.mkdir()
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 解析 Markdown
    console.print("\n[cyan][-] 解析周刊...[/cyan]")
    md_text = md_file.read_text(encoding="utf-8")
    segments = parse_weekly_markdown(md_text)
    console.print(f"  提取 [green]{len(segments)}[/green] 条新闻")
    if not segments:
        console.log("[red]未提取到新闻条目[/red]")
        return None

    # 2. 下载配图
    console.print(f"\n[cyan][IMG]  下载配图: {len(segments)} 条...[/cyan]")
    img_success = 0
    for i, seg in enumerate(segments):
        img_url = seg.get("image_url", "")
        if img_url:
            local_img = _download_image(img_url, IMAGE_CACHE_DIR)
            if local_img:
                bg_path = _prepare_image_for_video(local_img, image_dir)
                if bg_path:
                    seg["bg_path"] = str(bg_path)
                    img_success += 1
        if (i + 1) % 10 == 0:
            console.log(f"[dim]    图片: {img_success}/{i + 1}[/dim]")
    console.print(f"  图片就绪: [green]{img_success}/{len(segments)}[/green]")

    # 没有配图的条目用纯色背景
    _create_default_bg(image_dir, segments)

    # 3. TTS 配音
    segments = _tts_batch(segments, audio_dir)

    has_audio = sum(1 for s in segments if Path(s.get("audio_path", "")).exists())
    if has_audio == 0:
        console.log("[red]TTS 全部失败，终止[/red]")
        return None

    # 4. 合成视频片段
    console.print(f"\n[cyan][CLIP]  合成视频片段...[/cyan]")
    clip_paths: list[Path] = []
    for i, seg in enumerate(segments):
        clip = _compose_clip(seg, i, image_dir, work_dir)
        if clip:
            clip_paths.append(clip)
        if (i + 1) % 10 == 0:
            console.log(f"[dim]    合成: {len(clip_paths)}/{i + 1}[/dim]")
    console.print(f"  片段合成: [green]{len(clip_paths)}/{len(segments)}[/green]")

    if not clip_paths:
        console.log("[red]未生成任何视频片段[/red]")
        return None

    # 5. 合并 + 字幕
    no_sub_video = work_dir / "no_subs.mp4"
    console.print("\n[cyan][>>] 合并片段...[/cyan]")
    if not _concat_clips(clip_paths, no_sub_video):
        return None

    console.print("[cyan][...] 生成字幕并烧录...[/cyan]")
    ass_path = work_dir / "subtitles.ass"
    _generate_ass_subtitles(segments, ass_path)

    if not _burn_subtitles(no_sub_video, ass_path, out_path):
        # 字幕烧录失败 → 返回无字幕版本
        shutil.copy(no_sub_video, out_path)
        console.log("[yellow]  字幕烧录失败，使用无字幕版本[/yellow]")

    # 6. 统计
    total_duration = sum(s.get("duration", 0) for s in segments)
    console.print(f"\n[bold green]✅ 视频生成完成: {out_path}[/bold green]")
    console.print(
        f"  时长: [cyan]{int(total_duration//60)}分{int(total_duration%60)}秒[/cyan] "
        f"| 片段: {len(clip_paths)} | 大小: {out_path.stat().st_size / 1024 / 1024:.1f}MB"
    )

    shutil.rmtree(work_dir)
    return str(out_path)


def _create_default_bg(image_dir: Path, segments: list[dict]):
    """为没有配图的条目创建默认渐变背景"""
    default_bg = image_dir / "default_bg.jpg"
    if not Path(str(default_bg)).exists():
        try:
            cmd = [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"color=c=0x1a1a2e:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r=1",
                "-frames:v", "1",
                str(default_bg),
            ]
            subprocess.run(cmd, capture_output=True, check=True, timeout=10)
        except Exception:
            pass

    for seg in segments:
        if not seg.get("bg_path"):
            seg["bg_path"] = str(default_bg)


if __name__ == "__main__":
    import sys
    md_input = sys.argv[1] if len(sys.argv) > 1 else "output/2026-W29.md"
    build_video(md_input)
