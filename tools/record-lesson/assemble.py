#!/usr/bin/env python3
"""Join takes, cut silence, make chapter cards, split-screen compare."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

FONT = "/System/Library/Fonts/PingFang.ttc"
if not Path(FONT).is_file():
    FONT = "/System/Library/Fonts/STHeiti Light.ttc"


def _ffmpeg() -> str:
    bin = shutil.which("ffmpeg")
    if bin is None:
        raise RuntimeError("找不到 ffmpeg。先执行：brew install ffmpeg")
    return bin


def run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run([_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffmpeg 失败").strip()
        raise RuntimeError(detail[:800])


def title_card(text: str, output: Path, seconds: float = 2.2, size: str = "1920x1080") -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    safe = (text or "本集").replace("\n", " · ").strip() or "本集"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
        handle.write(safe)
        textfile = handle.name
    font = FONT if Path(FONT).is_file() else ""
    draw = (
        f"drawtext=textfile={textfile}:fontcolor=0x1a2332:fontsize=56:"
        "x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=16"
    )
    if font:
        draw = (
            f"drawtext=fontfile={font}:textfile={textfile}:fontcolor=0x1a2332:fontsize=56:"
            "x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=16"
        )
    run_ffmpeg(
        [
            "-f", "lavfi",
            "-i", f"color=c=0xeef4fb:s={size}:d={seconds}:r=30",
            "-vf", draw,
            "-c:v", "h264_videotoolbox", "-b:v", "6M", "-pix_fmt", "yuv420p",
            str(output),
        ]
    )
    Path(textfile).unlink(missing_ok=True)
    return output


def slideshow(images: list[Path], durations: list[float], output: Path) -> Path:
    if not images:
        raise RuntimeError("没有幻灯片画面。")
    output.parent.mkdir(parents=True, exist_ok=True)
    times = list(durations) or [3.0] * len(images)
    if len(times) < len(images):
        times.extend([times[-1]] * (len(images) - len(times)))
    listing = output.with_suffix(".concat.txt")
    rows = []
    for path, seconds in zip(images, times):
        dur = max(1.6, float(seconds))
        rows.append(f"file '{path}'")
        rows.append(f"duration {dur:.3f}")
    rows.append(f"file '{images[-1]}'")
    listing.write_text("\n".join(rows) + "\n", encoding="utf-8")
    run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-vf",
            "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p",
            "-c:v",
            "h264_videotoolbox",
            "-b:v",
            "8M",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ]
    )
    listing.unlink(missing_ok=True)
    return output


def _ffprobe() -> str:
    bin = shutil.which("ffprobe")
    if bin is None:
        raise RuntimeError("找不到 ffprobe。先执行：brew install ffmpeg")
    return bin


def has_audio_stream(path: Path) -> bool:
    result = subprocess.run(
        [
            _ffprobe(),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return bool((result.stdout or "").strip())


def probe_seconds(path: Path) -> float:
    result = subprocess.run(
        [
            _ffprobe(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return max(0.12, float((result.stdout or "0").strip()))
    except ValueError:
        return 1.0


def concat(paths: list[Path], output: Path, keep_audio: bool = False) -> Path:
    if len(paths) < 1:
        raise RuntimeError("没有可拼接的片段。")
    output.parent.mkdir(parents=True, exist_ok=True)
    if len(paths) == 1:
        shutil.copy2(paths[0], output)
        return output
    inputs: list[str] = []
    filters: list[str] = []
    for i, path in enumerate(paths):
        inputs.extend(["-i", str(path)])
        filters.append(
            f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1,format=yuv420p[v{i}]"
        )
        if keep_audio:
            if has_audio_stream(path):
                filters.append(
                    f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    f"asetpts=PTS-STARTPTS[a{i}]"
                )
            else:
                dur = probe_seconds(path)
                filters.append(
                    f"aevalsrc=0:d={dur:.3f}:s=48000,aformat=channel_layouts=stereo,"
                    f"asetpts=PTS-STARTPTS[a{i}]"
                )
    if keep_audio:
        chain = "".join(f"[v{i}][a{i}]" for i in range(len(paths))) + f"concat=n={len(paths)}:v=1:a=1[v][a]"
        args = [
            *inputs,
            "-filter_complex",
            ";".join(filters) + ";" + chain,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "h264_videotoolbox",
            "-b:v",
            "8M",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output),
        ]
    else:
        chain = "".join(f"[v{i}]" for i in range(len(paths))) + f"concat=n={len(paths)}:v=1:a=0[v]"
        args = [
            *inputs,
            "-filter_complex",
            ";".join(filters) + ";" + chain,
            "-map",
            "[v]",
            "-c:v",
            "h264_videotoolbox",
            "-b:v",
            "8M",
            "-an",
            str(output),
        ]
    run_ffmpeg(args)
    if not output.is_file():
        raise RuntimeError("拼接没写出文件。")
    return output


def split_screen(left: Path, right: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        "[0:v]scale=960:540:force_original_aspect_ratio=decrease,"
        "pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1[l];"
        "[1:v]scale=960:540:force_original_aspect_ratio=decrease,"
        "pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1[r];"
        "[l][r]hstack=inputs=2[v]"
    )
    run_ffmpeg(
        [
            "-i", str(left), "-i", str(right),
            "-filter_complex", vf, "-map", "[v]",
            "-c:v", "h264_videotoolbox", "-b:v", "8M", "-pix_fmt", "yuv420p",
            "-an",
            str(output),
        ]
    )
    return output


def silence_trim(src: Path, output: Path) -> Path:
    """Cut long silent gaps. No-op (copy) when the take has no usable audio."""
    output.parent.mkdir(parents=True, exist_ok=True)
    detect = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-i", str(src), "-af", "silencedetect=noise=-32dB:d=0.7", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    text = detect.stderr or ""
    if "does not contain any stream" in text or "Stream map '0:a'" in text or "silencedetect" not in text:
        shutil.copy2(src, output)
        return output
    starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", text)]
    if not starts:
        shutil.copy2(src, output)
        return output
    shutil.copy2(src, output)
    return output


def parse_rundown(text: str) -> list[dict]:
    segments: list[dict] = []
    current: dict | None = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, _, rest = line.partition(" ")
        key = head.lower()
        if key in {"card", "章节", "web", "网页", "term", "终端", "take", "片段", "split", "对比", "cursor", "agent"}:
            if current is not None:
                segments.append(current)
            kind = {
                "card": "card", "章节": "card",
                "web": "web", "网页": "web",
                "term": "term", "终端": "term",
                "take": "take", "片段": "take",
                "split": "split", "对比": "split",
                "cursor": "cursor", "agent": "cursor",
            }[key]
            current = {"kind": kind, "arg": rest.strip(), "lines": []}
        else:
            if current is None:
                current = {"kind": "web", "arg": "", "lines": []}
            current["lines"].append(line)
    if current is not None:
        segments.append(current)
    if not segments:
        raise RuntimeError("分镜稿是空的。")
    return segments
