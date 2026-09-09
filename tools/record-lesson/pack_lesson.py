#!/usr/bin/env python3
"""Turn a recording folder into notes, exercises, and subtitles."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_timeline(folder: Path, events: list[dict], meta: dict | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"events": events, "meta": meta or {}}
    path = folder / "timeline.json"
    write_json(path, payload)
    return path


def _fmt_ts(secs: float) -> str:
    ms = int(round(max(0.0, secs) * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rest = divmod(rem, 60_000)
    s, milli = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def srt_from_events(events: list[dict], duration: float | None = None) -> str:
    if not events:
        return ""
    lines: list[str] = []
    for i, ev in enumerate(events, 1):
        start = float(ev.get("t") or 0)
        if i < len(events):
            end = float(events[i].get("t") or start + 1.5)
        else:
            end = (duration if duration is not None else start + 2.0)
        if end <= start:
            end = start + 1.2
        text = str(ev.get("say") or ev.get("raw") or ev.get("cmd") or "")
        if not text:
            continue
        lines.append(f"{i}\n{_fmt_ts(start)} --> {_fmt_ts(end)}\n{text}\n")
    return "\n".join(lines)


def notes_from_events(events: list[dict], meta: dict | None = None) -> str:
    meta = meta or {}
    kind = meta.get("kind") or "lesson"
    title = meta.get("title") or "本集讲义"
    out = [f"# {title}", "", f"类型：{kind}", ""]
    if meta.get("prompt"):
        out += ["## 这一集在做什么", "", str(meta["prompt"]).strip(), ""]
    plan = meta.get("plan") if isinstance(meta.get("plan"), dict) else {}
    narr = plan.get("narration") or []
    if isinstance(narr, str):
        narr = [ln.strip() for ln in narr.splitlines() if ln.strip()]
    if narr:
        out += ["## 口播", ""]
        for line in narr:
            out.append(f"- {line}")
        out.append("")
    out.append("## 时间线")
    out.append("")
    for ev in events:
        t = float(ev.get("t") or 0)
        raw = ev.get("raw") or ev.get("cmd")
        out.append(f"- `{t:6.1f}s`  {raw}")
    cmds = [ev for ev in events if ev.get("cmd") in {"open", "click", "type", "baidu", "search", "run"}]
    if cmds:
        out += ["", "## 关键操作", ""]
        for ev in cmds:
            out.append(f"- {ev.get('raw') or ev.get('cmd')}")
    return "\n".join(out).rstrip() + "\n"


def exercises_from(events: list[dict], meta: dict | None = None) -> str:
    meta = meta or {}
    items: list[str] = []
    n = 1
    prompt = str(meta.get("prompt") or "").strip()
    expect = str(meta.get("expect") or "").strip()
    if prompt:
        check = f"验收：输出里出现「{expect}」。" if expect else "验收：自己跑通，把终端输出留下来。"
        items.append(f"{n}. {prompt.replace(chr(10), ' ')}\n   {check}")
        n += 1
    seen: set[str] = set()
    for ev in events:
        cmd = ev.get("cmd")
        arg = str(ev.get("arg") or "").strip()
        raw = str(ev.get("raw") or "")
        if cmd == "open" and arg and arg not in seen:
            seen.add(arg)
            items.append(f"{n}. 打开 {arg}，看清首页结构和主按钮。")
            n += 1
        elif cmd == "click" and arg:
            items.append(f"{n}. 自己找到并点击「{arg}」，说一下点完发生了什么。")
            n += 1
        elif cmd in {"baidu", "search", "type"} and arg:
            items.append(f"{n}. 搜索「{arg}」，记下前三条结果各自在讲什么。")
            n += 1
        elif cmd == "run" and arg:
            items.append(f"{n}. 在终端执行：`{arg}`，把完整输出贴出来。")
            n += 1
        elif cmd == "fail":
            items.append(f"{n}. 先复现错误：{arg or raw}。写一句话说明错在哪，再改对。")
            n += 1
    if not items:
        items.append("1. 按本集时间线自己做一遍，对照成片检查有没有漏步。")
    return "# 本集作业\n\n" + "\n\n".join(items) + "\n"


def try_whisper(mp4: Path, srt_path: Path) -> Path | None:
    whisper = shutil.which("whisper")
    if whisper is None or not mp4.is_file():
        return None
    result = subprocess.run(
        [whisper, str(mp4), "--language", "zh", "--output_format", "srt", "--output_dir", str(srt_path.parent)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    produced = mp4.with_suffix(".srt")
    if produced.is_file() and produced != srt_path:
        srt_path.write_text(produced.read_text(encoding="utf-8"), encoding="utf-8")
    return srt_path if srt_path.is_file() else None


def probe_duration(mp4: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None or not mp4.is_file():
        return None
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp4)],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def write_pack(folder: Path, events: list[dict], meta: dict | None = None) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    meta = dict(meta or {})
    save_timeline(folder, events, meta)
    mp4 = folder / "lesson.mp4"
    duration = probe_duration(mp4)
    notes = folder / "notes.md"
    exercises = folder / "exercises.md"
    srt = folder / "lesson.srt"
    notes.write_text(notes_from_events(events, meta), encoding="utf-8")
    exercises.write_text(exercises_from(events, meta), encoding="utf-8")
    spoken = srt_from_events(events, duration)
    srt.write_text(spoken, encoding="utf-8")
    whisper_ok = try_whisper(mp4, folder / "speech.srt")
    return {
        "timeline": str(folder / "timeline.json"),
        "notes": str(notes),
        "exercises": str(exercises),
        "srt": str(srt),
        "speech_srt": str(whisper_ok) if whisper_ok else None,
        "duration": duration,
    }
