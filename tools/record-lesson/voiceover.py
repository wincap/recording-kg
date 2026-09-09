#!/usr/bin/env python3
"""Mix macOS TTS narration onto a silent lesson video."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import assemble as asm
import pack_lesson as pack


HERE = Path(__file__).resolve().parent
VENV_PY = HERE / ".venv" / "bin" / "python3"
EDGE_SAY = HERE / "_edge_say.py"

NEURAL = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",
    "xiaoyi": "zh-CN-XiaoyiNeural",
    "yunxi": "zh-CN-YunxiNeural",
    "yunyang": "zh-CN-YunyangNeural",
}

# Newer macOS neural-ish voices. Tingting is the old robotic one — last resort only.
MAC_VOICES = (
    "Shelley (中文（中国大陆）)",
    "Eddy (中文（中国大陆）)",
    "Sandy (中文（中国大陆）)",
    "Flo (中文（中国大陆）)",
    "Reed (中文（中国大陆）)",
)


def pick_mac_voice() -> str:
    result = subprocess.run(["say", "-v", "?"], capture_output=True, text=True)
    listing = result.stdout or ""
    for name in MAC_VOICES:
        if name in listing:
            return name
    return "Tingting"


def edge_ready() -> bool:
    return VENV_PY.is_file() and EDGE_SAY.is_file()


def pick_engine(name: str | None = None) -> tuple[str, str]:
    wanted = (name or os.environ.get("LESSON_VOICE") or "xiaoxiao").strip().lower()
    if wanted in NEURAL and edge_ready():
        return "edge", NEURAL[wanted]
    if wanted == "tingting":
        return "say", "Tingting"
    if edge_ready():
        return "edge", NEURAL["xiaoxiao"]
    return "say", pick_mac_voice()


def probe_duration(path: Path) -> float:
    value = pack.probe_duration(path)
    if value is None:
        raise RuntimeError(f"读不出时长：{path}")
    return value


def has_audio(path: Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None or not path.is_file():
        return False
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return bool((result.stdout or "").strip())


def narration_lines(plan: dict | None) -> list[str]:
    plan = plan or {}
    raw = plan.get("narration") or []
    if isinstance(raw, str):
        raw = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    lines = [str(x).strip() for x in raw if str(x).strip()]
    if lines:
        return lines
    title = str(plan.get("title") or "").strip()
    beats = [str(b.get("beat") or "").strip() for b in (plan.get("outline") or []) if isinstance(b, dict)]
    beats = [b for b in beats if b]
    if title and beats:
        return [f"这一集讲{title}。"] + [f"{b}。" if not b.endswith(("。", "！", "？")) else b for b in beats]
    return []


def speak_edge(text: str, dest: Path, voice: str) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(VENV_PY),
            str(EDGE_SAY),
            text.strip(),
            voice,
            "-6%",
            "-3Hz",
            str(dest),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest.is_file() or dest.stat().st_size < 200:
        detail = (result.stderr or result.stdout or "edge-tts 失败").strip()
        raise RuntimeError("神经口播失败：" + detail[-400:])
    return dest


def speak_line(text: str, dest: Path, engine: str, voice: str) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if engine == "edge":
        return speak_edge(text, dest.with_suffix(".mp3"), voice)
    src = dest.with_suffix(".txt")
    src.write_text(text.strip(), encoding="utf-8")
    out = dest.with_suffix(".aiff")
    result = subprocess.run(
        ["say", "-v", voice, "-f", str(src), "-o", str(out)],
        capture_output=True,
        text=True,
    )
    src.unlink(missing_ok=True)
    if result.returncode != 0 or not out.is_file():
        detail = (result.stderr or result.stdout or "say 失败").strip()
        raise RuntimeError("系统朗读失败：" + detail[:300])
    return out


def build_track(lines: list[str], work: Path, engine: str, voice: str) -> tuple[Path, list[dict]]:
    clips: list[Path] = []
    cues: list[dict] = []
    t = 0.4
    gap = 0.55
    for i, line in enumerate(lines):
        clip = speak_line(line, work / f"line-{i:02d}", engine, voice)
        dur = probe_duration(clip)
        cues.append({"t": round(t, 2), "end": round(t + dur, 2), "text": line})
        clips.append(clip)
        t += dur + gap
    if not clips:
        raise RuntimeError("没有可朗读的口播。")
    inputs: list[str] = []
    parts: list[str] = []
    inputs.extend(["-f", "lavfi", "-t", "0.4", "-i", "anullsrc=r=44100:cl=mono"])
    idx = 1
    parts.append("[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=mono[s0]")
    concat_refs = ["[s0]"]
    for i, clip in enumerate(clips):
        inputs.extend(["-i", str(clip)])
        parts.append(f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=mono[a{i}]")
        concat_refs.append(f"[a{i}]")
        idx += 1
        if i < len(clips) - 1:
            inputs.extend(["-f", "lavfi", "-t", str(gap), "-i", "anullsrc=r=44100:cl=mono"])
            parts.append(f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=mono[g{i}]")
            concat_refs.append(f"[g{i}]")
            idx += 1
    n = len(concat_refs)
    chain = (
        "".join(concat_refs)
        + f"concat=n={n}:v=0:a=1[c];"
        + "[c]highpass=f=80,lowpass=f=12000,"
        + "acompressor=threshold=-18dB:ratio=2.2:attack=20:release=220,"
        + "loudnorm=I=-16:TP=-1.5:LRA=10,"
        + "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a]"
    )
    mixed = work / "voiceover.m4a"
    asm.run_ffmpeg(
        [
            *inputs,
            "-filter_complex",
            ";".join(parts) + ";" + chain,
            "-map",
            "[a]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(mixed),
        ]
    )
    return mixed, cues

def mux(video: Path, audio: Path, output: Path) -> Path:
    vid = probe_duration(video)
    aud = probe_duration(audio)
    pad_v = max(0.0, aud - vid)
    pad_a = max(0.0, vid - aud)
    vf = f"[0:v]tpad=stop_mode=clone:stop_duration={pad_v:.3f},format=yuv420p[v]"
    af = f"[1:a]apad=pad_dur={pad_a:.3f}[a]"
    asm.run_ffmpeg(
        [
            "-i",
            str(video),
            "-i",
            str(audio),
            "-filter_complex",
            vf + ";" + af,
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
            "128k",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    return output


def srt_from_cues(cues: list[dict]) -> str:
    lines = []
    for i, cue in enumerate(cues, 1):
        start = float(cue.get("t") or 0)
        end = float(cue.get("end") or start + 1.5)
        text = str(cue.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"{i}\n{pack._fmt_ts(start)} --> {pack._fmt_ts(end)}\n{text}\n")
    return "\n".join(lines)


def attach(video: Path, plan: dict | None, log=None, replace: bool = False) -> dict | None:
    emit = log or (lambda m: print(m, flush=True))
    if not video.is_file():
        return None
    silent = video.with_name("lesson-silent.mp4")
    if not silent.is_file():
        shutil.copy2(video, silent)
    if has_audio(video) and not replace:
        emit("成片里已经有音轨，不重复配音。")
        return None
    lines = narration_lines(plan)
    if not lines:
        emit("这一集没有口播稿，画面还是静音。")
        return None
    engine, voice = pick_engine()
    label = "晓晓神经音色" if engine == "edge" else voice
    emit(f"配口播（{label}）…")
    work = Path(tempfile.mkdtemp(prefix="lesson-vo-"))
    try:
        try:
            track, cues = build_track(lines, work, engine, voice)
        except Exception as exc:
            if engine != "edge":
                raise
            emit(f"神经音色没通，改用系统声：{exc}")
            engine, voice = "say", pick_mac_voice()
            label = voice
            track, cues = build_track(lines, work, engine, voice)
        tmp = video.with_name("lesson-vo-tmp.mp4")
        mux(silent, track, tmp)
        tmp.replace(video)
        srt_body = srt_from_cues(cues)
        (video.parent / "speech.srt").write_text(srt_body, encoding="utf-8")
        (video.parent / "lesson.srt").write_text(srt_body, encoding="utf-8")
        (video.parent / "voiceover.json").write_text(
            json.dumps({"engine": engine, "voice": voice, "label": label, "cues": cues}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        emit(f"口播已配上，约 {probe_duration(video):.0f}s。")
        return {"engine": engine, "voice": voice, "cues": cues, "srt": str(video.parent / "speech.srt")}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def render_talk(title: str, lines: list[str], dest: Path, log=None, frames: list[Path] | None = None) -> dict:
    emit = log or (lambda m: print(m, flush=True))
    text = [str(x).strip() for x in (lines or []) if str(x).strip()]
    if not text:
        raise RuntimeError("这一章还没有解说词。")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    engine, voice = pick_engine()
    label = "晓晓神经音色" if engine == "edge" else voice
    emit(f"解说配音（{label}）…")
    work = Path(tempfile.mkdtemp(prefix="lesson-talk-"))
    try:
        try:
            track, cues = build_track(text, work, engine, voice)
        except Exception as exc:
            if engine != "edge":
                raise
            emit(f"神经音色没通，改用系统声：{exc}")
            engine, voice = "say", pick_mac_voice()
            label = voice
            track, cues = build_track(text, work, engine, voice)
        dur = max(3.0, probe_duration(track) + 0.4)
        card = work / "card.mp4"
        ready = [Path(p) for p in (frames or []) if Path(p).is_file()]
        pngs = [p for p in ready if p.suffix.lower() == ".png"]
        svgs = [p for p in ready if p.suffix.lower() == ".svg"]
        if not pngs and svgs:
            import ppt_deck

            pngs = ppt_deck.raster_frames(svgs, work / "frames")
        if pngs:
            emit(f"幻灯片 {len(pngs)} 页对口播…")
            each = dur / len(pngs)
            asm.slideshow(pngs, [each] * len(pngs), card)
        else:
            asm.title_card(title or "本集", card, seconds=dur)
        tmp = dest.with_name(dest.stem + "-tmp.mp4")
        mux(card, track, tmp)
        tmp.replace(dest)
        srt_body = srt_from_cues(cues)
        dest.with_suffix(".srt").write_text(srt_body, encoding="utf-8")
        dest.with_name(dest.stem + ".script.txt").write_text("\n".join(text), encoding="utf-8")
        dest.with_name(dest.stem + ".voiceover.json").write_text(
            json.dumps({"engine": engine, "voice": voice, "label": label, "cues": cues}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if dest.stem == "lesson":
            dest.with_name("speech.srt").write_text(srt_body, encoding="utf-8")
            dest.with_name("lesson.srt").write_text(srt_body, encoding="utf-8")
            dest.with_name("script.txt").write_text("\n".join(text), encoding="utf-8")
            dest.with_name("voiceover.json").write_text(
                json.dumps({"engine": engine, "voice": voice, "label": label, "cues": cues}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        emit(f"解说章已出片，约 {probe_duration(dest):.0f}s。")
        return {"engine": engine, "voice": voice, "cues": cues, "video": str(dest)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _rel_frame(folder: Path, frame: Path) -> str:
    folder = folder.resolve()
    path = Path(frame).resolve()
    try:
        return str(path.relative_to(folder)).replace("\\", "/")
    except ValueError:
        dest = folder / "slides" / path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and dest.resolve() != path:
            shutil.copy2(path, dest)
        return f"slides/{dest.name}"


def page_timings(folder: Path, frames: list[Path], audio_dur: float) -> list[dict]:
    ready = [Path(p) for p in (frames or []) if Path(p).is_file()]
    if not ready:
        return []
    each = max(0.4, float(audio_dur) / len(ready))
    pages = []
    for i, frame in enumerate(ready):
        pages.append(
            {
                "src": _rel_frame(folder, frame),
                "at": round(i * each, 3),
                "dur": round(each, 3),
            }
        )
    return pages


def write_talk_assets(
    folder: Path,
    stem: str,
    title: str,
    lines: list[str],
    log=None,
    frames: list[Path] | None = None,
) -> dict:
    """Write PPT pages + voice audio. Do not bake them into a video."""
    emit = log or (lambda m: print(m, flush=True))
    text = [str(x).strip() for x in (lines or []) if str(x).strip()]
    if not text:
        raise RuntimeError("这一章还没有解说词。")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stem = str(stem or "speech").strip() or "speech"
    audio = folder / f"{stem}.m4a"
    engine, voice = pick_engine()
    label = "晓晓神经音色" if engine == "edge" else voice
    emit(f"解说配音（{label}）…")
    work = Path(tempfile.mkdtemp(prefix="lesson-talk-"))
    try:
        try:
            track, cues = build_track(text, work, engine, voice)
        except Exception as exc:
            if engine != "edge":
                raise
            emit(f"神经音色没通，改用系统声：{exc}")
            engine, voice = "say", pick_mac_voice()
            label = voice
            track, cues = build_track(text, work, engine, voice)
        shutil.copy2(track, audio)
        dur = max(1.0, probe_duration(audio))
        ready = [Path(p) for p in (frames or []) if Path(p).is_file()]
        pages = page_timings(folder, ready, dur)
        srt_body = srt_from_cues(cues)
        (folder / f"{stem}.srt").write_text(srt_body, encoding="utf-8")
        (folder / f"{stem}.script.txt").write_text("\n".join(text), encoding="utf-8")
        (folder / f"{stem}.voiceover.json").write_text(
            json.dumps({"engine": engine, "voice": voice, "label": label, "cues": cues}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if stem in {"lesson", "speech"}:
            (folder / "speech.srt").write_text(srt_body, encoding="utf-8")
            (folder / "script.txt").write_text("\n".join(text), encoding="utf-8")
            (folder / "voiceover.json").write_text(
                json.dumps({"engine": engine, "voice": voice, "label": label, "cues": cues}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        emit(f"口播已写好（{len(pages)} 页幻灯片 + 音频），约 {dur:.0f}s。不合成视频。")
        if pages:
            return {
                "id": stem,
                "kind": "slides",
                "title": title or "PPT",
                "audio": audio.name,
                "dur": round(dur, 3),
                "pages": pages,
            }
        return {
            "id": stem,
            "kind": "audio",
            "title": title or "解说",
            "audio": audio.name,
            "dur": round(dur, 3),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
