#!/usr/bin/env python3
"""Persist a multi-chapter course and expose it to the student player."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path


def slugify(title: str) -> str:
    raw = re.sub(r"[^\w\u4e00-\u9fff]+", "-", (title or "").strip(), flags=re.U)
    raw = re.sub(r"-{2,}", "-", raw).strip("-")
    return (raw[:48] or "course")


LAST_NAME = ".last-project"

PART_CLIPS = (
    ("intro.mp4", "intro", "PPT"),
    ("handoff.mp4", "handoff", "接下来动手"),
    ("cursor.mp4", "cursor", "实操"),
    ("closer.mp4", "closer", "对照"),
)
CLIP_TITLE = {cid: title for _, cid, title in PART_CLIPS}


def courses_root(project: Path) -> Path:
    return Path(project) / "recordings" / "courses"


def clip_duration(path: Path) -> float:
    try:
        import assemble

        return float(assemble.probe_seconds(Path(path)))
    except Exception:
        return 0.0


def slide_files(folder) -> list[Path]:
    folder = Path(folder)
    slide_dir = folder / "slides"
    if slide_dir.is_dir():
        frames = sorted(slide_dir.glob("*.png")) or sorted(slide_dir.glob("*.svg"))
        if frames:
            return frames
    return []


def extract_audio(src: Path, dest: Path) -> Path | None:
    src = Path(src)
    dest = Path(dest)
    if not src.is_file() or src.stat().st_size < 1000:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        import assemble

        assemble.run_ffmpeg(["-i", str(src), "-vn", "-c:a", "aac", "-b:a", "192k", str(dest)])
    except Exception:
        return None
    if dest.is_file() and dest.stat().st_size > 200:
        return dest
    return None


def _as_clip(folder: Path, raw, title: str | None = None) -> dict | None:
    if isinstance(raw, dict):
        clip = dict(raw)
        if not clip.get("kind"):
            if clip.get("pages") or (clip.get("audio") and not clip.get("src")):
                clip["kind"] = "slides" if clip.get("pages") else "audio"
            else:
                clip["kind"] = "video"
        return clip
    path = Path(raw)
    if not path.is_file() or path.stat().st_size < 200:
        return None
    stem = path.stem
    kind = "video" if path.suffix.lower() in {".mp4", ".mov"} else "audio"
    clip = {
        "id": stem,
        "kind": kind,
        "title": title or CLIP_TITLE.get(stem, stem),
        "dur": round(clip_duration(path), 3),
    }
    if kind == "video":
        clip["src"] = path.name
    else:
        clip["audio"] = path.name
    return clip


def write_play_timeline(folder, paths, titles: list[str] | None = None) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    clips: list[dict] = []
    for i, raw in enumerate(paths or []):
        title = titles[i] if titles and i < len(titles) else None
        clip = _as_clip(folder, raw, title)
        if clip:
            clips.append(clip)
    dest = folder / "timeline.json"
    dest.write_text(
        json.dumps({"kind": "play", "clips": clips}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return dest


def clip_ready(folder, item: dict) -> bool:
    folder = Path(folder)
    kind = str(item.get("kind") or "video")
    if kind in {"slides", "audio"}:
        audio = folder / str(item.get("audio") or "")
        if not audio.is_file() or audio.stat().st_size < 200:
            return False
        if kind == "audio":
            return True
        pages = item.get("pages") or []
        return any((folder / str(p.get("src") or "")).is_file() for p in pages if isinstance(p, dict))
    src = folder / str(item.get("src") or "")
    return src.is_file() and src.stat().st_size > 1000


def infer_play_timeline(folder) -> dict:
    folder = Path(folder)
    saved = folder / "timeline.json"
    if saved.is_file():
        try:
            data = json.loads(saved.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict) and data.get("clips"):
            return data
    clips: list[dict] = []
    frames = slide_files(folder)
    for stem, title in (("intro", "PPT"), ("speech", "PPT"), ("lesson", "PPT")):
        audio = folder / f"{stem}.m4a"
        if frames and audio.is_file() and audio.stat().st_size > 200:
            each = max(0.4, clip_duration(audio) / len(frames))
            clips.append(
                {
                    "id": stem,
                    "kind": "slides",
                    "title": title,
                    "audio": audio.name,
                    "dur": round(clip_duration(audio), 3),
                    "pages": [
                        {
                            "src": f"slides/{frame.name}",
                            "at": round(i * each, 3),
                            "dur": round(each, 3),
                        }
                        for i, frame in enumerate(frames)
                    ],
                }
            )
            break
    for name, cid, title in PART_CLIPS:
        if cid == "intro" and clips:
            continue
        path = folder / name
        if path.is_file() and path.stat().st_size > 1000:
            clips.append(
                {
                    "id": cid,
                    "kind": "video",
                    "src": name,
                    "title": title,
                    "dur": round(clip_duration(path), 3),
                }
            )
    if not clips:
        path = folder / "lesson.mp4"
        if path.is_file() and path.stat().st_size > 1000:
            clips.append(
                {
                    "id": "lesson",
                    "kind": "video",
                    "src": "lesson.mp4",
                    "title": "本集",
                    "dur": round(clip_duration(path), 3),
                }
            )
    return {"kind": "play", "clips": clips}


def migrate_baked_chapter(folder) -> Path | None:
    """Turn baked intro/handoff/closer mp4 into slides+audio timeline when slides exist."""
    folder = Path(folder)
    frames = slide_files(folder)
    practice = folder / "practice.svg"
    clips: list[dict] = []

    def slides_from_mp4(stem: str, title: str, page_frames: list[Path]) -> dict | None:
        audio = folder / f"{stem}.m4a"
        mp4 = folder / f"{stem}.mp4"
        if not audio.is_file() or audio.stat().st_size < 200:
            if mp4.is_file() and mp4.stat().st_size > 1000:
                if not extract_audio(mp4, audio):
                    return None
            else:
                return None
        dur = clip_duration(audio)
        pages = []
        if page_frames:
            each = max(0.4, dur / len(page_frames)) if dur else 3.0
            for i, frame in enumerate(page_frames):
                pages.append(
                    {
                        "src": f"slides/{frame.name}" if frame.parent.name == "slides" else frame.name,
                        "at": round(i * each, 3),
                        "dur": round(each, 3),
                    }
                )
                if frame.parent != folder / "slides" and frame.name == "practice.svg":
                    pages[-1]["src"] = "practice.svg"
        clip = {
            "id": stem,
            "kind": "slides" if pages else "audio",
            "title": title,
            "audio": audio.name,
            "dur": round(dur, 3),
        }
        if pages:
            clip["pages"] = pages
        return clip

    intro = slides_from_mp4("intro", "PPT", frames) or slides_from_mp4("speech", "PPT", frames)
    if intro:
        clips.append(intro)
    handoff_frames = [practice] if practice.is_file() else frames[-1:]
    handoff = slides_from_mp4("handoff", "接下来动手", handoff_frames)
    if handoff:
        clips.append(handoff)
    cursor = folder / "cursor.mp4"
    if cursor.is_file() and cursor.stat().st_size > 1000:
        clips.append(
            {
                "id": "cursor",
                "kind": "video",
                "src": "cursor.mp4",
                "title": "实操",
                "dur": round(clip_duration(cursor), 3),
            }
        )
    closer = slides_from_mp4("closer", "对照", handoff_frames)
    if closer:
        clips.append(closer)
    if not clips:
        lesson = folder / "lesson.mp4"
        speech = folder / "speech.m4a"
        if frames and (speech.is_file() or lesson.is_file()):
            if not speech.is_file() and lesson.is_file():
                extract_audio(lesson, speech)
            clip = slides_from_mp4("speech", "PPT", frames)
            if clip:
                clips.append(clip)
        elif lesson.is_file() and lesson.stat().st_size > 1000:
            clips.append(
                {
                    "id": "lesson",
                    "kind": "video",
                    "src": "lesson.mp4",
                    "title": "本集",
                    "dur": round(clip_duration(lesson), 3),
                }
            )
    if not clips:
        return None
    return write_play_timeline(folder, clips)


def timeline_ready(folder) -> bool:
    clips = infer_play_timeline(folder).get("clips") or []
    return bool(clips) and all(clip_ready(folder, item) for item in clips)


def last_slug_path(project: Path) -> Path:
    return courses_root(project) / LAST_NAME


def last_slug(project: Path) -> str:
    path = last_slug_path(project)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def remember_slug(project: Path, slug: str) -> None:
    root = courses_root(project)
    root.mkdir(parents=True, exist_ok=True)
    path = last_slug_path(project)
    text = str(slug or "").strip()
    if not text:
        if path.is_file():
            path.unlink()
        return
    path.write_text(text + "\n", encoding="utf-8")


def course_dir(project: Path, plan: dict | None) -> Path:
    plan = plan or {}
    slug = str(plan.get("slug") or slugify(plan.get("course_title") or plan.get("title") or "course"))
    path = courses_root(project) / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def chapter_dir(project: Path, plan: dict | None, n: int | None = None) -> Path:
    packed = plan or {}
    try:
        idx = int(n or packed.get("chapter") or 1)
    except (TypeError, ValueError):
        idx = 1
    folder = course_dir(project, packed) / f"ch-{idx:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def payload_from_plan(plan: dict | None) -> dict:
    plan = plan or {}
    title = str(plan.get("course_title") or plan.get("title") or "未命名课程").strip()
    slug = str(plan.get("slug") or slugify(title))
    data = {
        "course_title": title,
        "slug": slug,
        "audience": plan.get("audience") or "",
        "chapter": plan.get("chapter") or 1,
        "chapters": list(plan.get("chapters") or []),
        "pipeline": plan.get("pipeline") or "",
        "watch": f"/watch?slug={slug}",
    }
    for key in ("confirmed", "gate", "prompt", "expect", "tools", "title", "objective"):
        if key in plan:
            data[key] = plan[key]
    return data


def save_course(project: Path, plan: dict | None) -> dict:
    data = payload_from_plan(plan)
    folder = courses_root(project) / data["slug"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "course.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    remember_slug(project, data["slug"])
    return data


def load_course(project: Path, slug: str) -> dict | None:
    path = courses_root(project) / str(slug or "").strip() / "course.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def infer_pipeline(data: dict | None) -> str:
    data = data or {}
    stage = str(data.get("pipeline") or "").strip().lower()
    if stage in {"outline", "materials", "produce"}:
        return stage
    chapters = data.get("chapters") or []
    if any(ch.get("status") == "ready" for ch in chapters):
        return "produce"
    if any((ch.get("script") or ch.get("slides")) for ch in chapters):
        return "materials"
    return "outline"


def plan_from_saved(data: dict | None) -> dict:
    plan = dict(data or {})
    plan["pipeline"] = infer_pipeline(plan)
    slug = str(plan.get("slug") or slugify(plan.get("course_title") or plan.get("title") or "course"))
    plan["slug"] = slug
    chapters = list(plan.get("chapters") or [])
    plan["chapters"] = chapters
    try:
        n = int(plan.get("chapter") or 1)
    except (TypeError, ValueError):
        n = 1
    cur = next((ch for ch in chapters if int(ch.get("n") or 0) == n), chapters[0] if chapters else {})
    plan["title"] = str(cur.get("title") or plan.get("title") or plan.get("course_title") or "本集")
    plan["objective"] = str(cur.get("goal") or plan.get("objective") or "")
    if cur.get("prompt"):
        plan["prompt"] = cur.get("prompt")
    return plan


def list_courses(project: Path) -> list[dict]:
    root = courses_root(project)
    if not root.is_dir():
        return []
    last = last_slug(project)
    rows = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        data = load_course(project, folder.name)
        if not data:
            continue
        chapters = data.get("chapters") or []
        ready = sum(1 for ch in chapters if ch.get("status") == "ready")
        stamp = (folder / "course.json").stat().st_mtime
        rows.append(
            {
                "slug": data.get("slug") or folder.name,
                "course_title": data.get("course_title") or folder.name,
                "chapters": len(chapters),
                "ready": ready,
                "pipeline": infer_pipeline(data),
                "updated": stamp,
                "last": (data.get("slug") or folder.name) == last,
                "watch": f"/watch?slug={data.get('slug') or folder.name}",
            }
        )
    rows.sort(key=lambda row: (not row.get("last"), -float(row.get("updated") or 0)))
    return rows


def mark_chapter_ready(plan: dict, n: int, rel_video: str) -> dict:
    plan = dict(plan or {})
    chapters = [dict(ch) for ch in (plan.get("chapters") or [])]
    for ch in chapters:
        if int(ch.get("n") or 0) == int(n):
            ch["status"] = "ready"
            ch["video"] = rel_video
    plan["chapters"] = chapters
    return plan


def publish_folder(project: Path, plan: dict | None, src: Path, n: int | None = None) -> dict:
    plan = dict(plan or {})
    try:
        idx = int(n or plan.get("chapter") or 1)
    except (TypeError, ValueError):
        idx = 1
    dest = chapter_dir(project, plan, idx)
    dest.mkdir(parents=True, exist_ok=True)

    def put(item: Path, name: str) -> None:
        if not item.is_file():
            return
        target = dest / name
        if item.resolve() == target.resolve():
            return
        shutil.copy2(item, target)

    if src.is_file() and src.suffix.lower() == ".mp4":
        put(src, src.name if src.name != "lesson.mp4" else "lesson.mp4")
    if src.is_dir():
        for name in (
            "timeline.json",
            "lesson.mp4",
            "intro.mp4",
            "handoff.mp4",
            "cursor.mp4",
            "closer.mp4",
            "intro.m4a",
            "handoff.m4a",
            "closer.m4a",
            "speech.m4a",
            "lesson.m4a",
            "practice.svg",
            "lesson.srt",
            "speech.srt",
            "notes.md",
            "exercises.md",
            "voiceover.json",
            "script.txt",
            "slides.md",
            "deck.pptx",
        ):
            put(src / name, name)
        if not (dest / "timeline.json").is_file():
            parts = [dest / name for name, _, _ in PART_CLIPS]
            if any(p.is_file() and p.stat().st_size > 1000 for p in parts):
                write_play_timeline(dest, parts)
            elif (dest / "lesson.mp4").is_file():
                write_play_timeline(dest, [dest / "lesson.mp4"], ["本集"])
    rel = f"ch-{idx:02d}/timeline.json"
    plan["slug"] = str(plan.get("slug") or slugify(plan.get("course_title") or plan.get("title") or "course"))
    if timeline_ready(dest):
        plan = mark_chapter_ready(plan, idx, rel)
    save_course(project, plan)
    return plan


def public_course(project: Path, slug: str) -> dict | None:
    data = load_course(project, slug)
    if not data:
        return None
    folder = courses_root(project) / (data.get("slug") or slug)
    chapters = []
    for ch in data.get("chapters") or []:
        row = dict(ch)
        n = int(row.get("n") or 0) or 1
        chdir = folder / f"ch-{n:02d}"
        media = infer_play_timeline(chdir)
        prefix = f"/media/courses/{data.get('slug') or slug}/ch-{n:02d}/"
        clips = []
        for item in media.get("clips") or []:
            if not clip_ready(chdir, item):
                continue
            kind = str(item.get("kind") or "video")
            clip = {
                "id": item.get("id") or "clip",
                "kind": kind,
                "title": item.get("title") or CLIP_TITLE.get(str(item.get("id") or ""), str(item.get("id") or "片段")),
                "dur": float(item.get("dur") or 0),
            }
            src = str(item.get("src") or "").lstrip("/")
            if src:
                clip["src"] = prefix + src
            audio = str(item.get("audio") or "").lstrip("/")
            if audio:
                clip["audio"] = prefix + audio
            pages = []
            for page in item.get("pages") or []:
                if not isinstance(page, dict):
                    continue
                pname = str(page.get("src") or "").lstrip("/")
                if not pname or not (chdir / pname).is_file():
                    continue
                pages.append(
                    {
                        "src": prefix + pname,
                        "at": float(page.get("at") or 0),
                        "dur": float(page.get("dur") or 0),
                    }
                )
            if pages:
                clip["pages"] = pages
            clips.append(clip)
        row["timeline"] = clips
        row["ready"] = bool(clips)
        if clips:
            first = clips[0]
            row["src"] = first.get("src") or first.get("audio") or ((first.get("pages") or [{}])[0].get("src") or "")
        else:
            row["src"] = ""
        script = row.get("script") or []
        if isinstance(script, str):
            script = [ln.strip() for ln in script.splitlines() if ln.strip()]
        row["script"] = script
        chapters.append(row)
    data["chapters"] = chapters
    return data
