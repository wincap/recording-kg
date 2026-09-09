#!/usr/bin/env python3
"""Student coach: ingest assets → DeepSeek → stage lecture payload."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import course
import plan_lesson

SYSTEM = """你是编程课现场答疑助教。学生边看课边提问，或上传报错/代码/截图。
请结合章节上下文与近期学习记录，输出 JSON（不要 markdown 包裹）：
- status: 短标签，如 跟得上 / 有点懵 / 卡住了 / 在提问 / 在复述 / 其他
- understanding: 0-100 整数
- questions: 字符串数组
- blockers: 字符串数组
- tags: 字符串数组
- reply: 一句口语回应（给聊天框）
- lecture_md: 给主画布右侧的 Markdown 讲义（含标题、分点、必要时给修复代码块）
- asset_kind: text | code | image | log
- asset_title: 左侧素材标题，如「学生报错」「截图提问」
- beats: 可选数组 [{at, title, focus}]，at 为秒，focus 为 student|lecture
若素材是截图 OCR 文本，请根据可读文字推断问题；若 OCR 很空，请在讲义里说明需要学生补充描述。
不要输出 JSON 以外的文字。"""

_DATA_URL_RE = re.compile(r"^data:([^;]+);base64,(.+)$", re.S)


def notes_path(project: Path, slug: str) -> Path:
    return course.courses_root(project) / course.slugify(slug) / "student-notes.json"


def assets_dir(project: Path, slug: str) -> Path:
    path = course.courses_root(project) / course.slugify(slug) / "student-assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_notes(project: Path, slug: str) -> dict:
    path = notes_path(project, slug)
    if not path.is_file():
        return {"slug": course.slugify(slug), "entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("slug", course.slugify(slug))
    data.setdefault("entries", [])
    if not isinstance(data["entries"], list):
        data["entries"] = []
    return data


def save_notes(project: Path, slug: str, data: dict) -> Path:
    path = notes_path(project, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def recent_notes_block(notes: dict, limit: int = 5) -> str:
    entries = [e for e in (notes.get("entries") or []) if isinstance(e, dict)]
    if not entries:
        return ""
    lines = ["近期学习记录（从旧到新，最多 %d 条）：" % limit]
    for item in entries[-limit:]:
        st = str(item.get("status") or "")
        qs = "；".join(str(q) for q in (item.get("questions") or [])[:2] if str(q).strip())
        snippet = str(item.get("text") or "").replace("\n", " ").strip()[:160]
        reply = str(item.get("reply") or "").replace("\n", " ").strip()[:120]
        lines.append(
            f"- [{st}] 学生：{snippet or '（无正文）'}"
            + (f"｜问题：{qs}" if qs else "")
            + (f"｜当时回应：{reply}" if reply else "")
        )
    return "\n".join(lines)


def _context_block(course_data: dict | None, chapter_n: int, player: dict) -> str:
    title = ""
    chapter_title = ""
    script_preview = ""
    if course_data:
        title = str(course_data.get("course_title") or course_data.get("title") or "")
        for ch in course_data.get("chapters") or []:
            if int(ch.get("n") or 0) == int(chapter_n or 0):
                chapter_title = str(ch.get("title") or "")
                lines = ch.get("script") or []
                if isinstance(lines, str):
                    lines = [ln for ln in lines.splitlines() if ln.strip()]
                script_preview = "\n".join(str(x) for x in lines[:8])
                break
    bits = [
        f"课程：{title or '未知'}",
        f"章节：第 {chapter_n} 章 {chapter_title}".strip(),
        f"播放位置约 {float(player.get('t') or 0):.0f}s",
        f"当前片段：{player.get('clip_title') or player.get('clip_id') or '未知'}",
        f"素材类型：{player.get('kind') or '未知'}",
    ]
    if player.get("course_code"):
        bits.append("当前课程代码上下文：\n" + str(player.get("course_code"))[:2500])
    if script_preview:
        bits.append("本章口播摘录：\n" + script_preview)
    return "\n".join(bits)


def _guess_asset_kind(text: str, hint: str | None = None) -> str:
    if hint in {"text", "code", "image", "log"}:
        return hint
    raw = text or ""
    if "Traceback" in raw or "Error:" in raw or "Exception" in raw or "HTTPException" in raw:
        return "log"
    if "```" in raw or "def " in raw or "import " in raw or "from " in raw or "app = FastAPI" in raw:
        return "code"
    return "text"


def _default_beats(lecture_md: str) -> list[dict]:
    return [
        {"at": 0, "title": "看学生素材", "focus": "student"},
        {"at": 4, "title": "AI 讲解", "focus": "lecture"},
        {"at": 12, "title": "对照修复", "focus": "lecture"},
    ]


def _ext_for_mime(mime: str) -> str:
    mime = (mime or "").lower()
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(mime, ".bin")


def ocr_image(path: Path) -> str:
    """Best-effort OCR. Prefer tesseract; otherwise empty."""
    path = Path(path)
    if not path.is_file():
        return ""
    bin_path = shutil.which("tesseract")
    if not bin_path:
        return ""
    try:
        result = subprocess.run(
            [bin_path, str(path), "stdout", "-l", "chi_sim+eng", "--psm", "6"],
            capture_output=True,
            text=True,
            timeout=40,
        )
    except Exception:
        return ""
    text = (result.stdout or "").strip()
    if len(text) < 4:
        return ""
    return text[:8000]


def ingest_asset(project: Path, slug: str, asset: dict | None) -> dict:
    """Persist student upload and normalize fields for coaching + stage."""
    asset = asset if isinstance(asset, dict) else {}
    slug = course.slugify(slug)
    kind_hint = str(asset.get("kind") or "").strip().lower()
    title = str(asset.get("title") or "学生素材").strip() or "学生素材"
    content = str(asset.get("content") or asset.get("text") or "").strip()
    data_url = str(asset.get("image_data_url") or "").strip()
    folder = assets_dir(project, slug)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    aid = uuid.uuid4().hex[:8]

    saved_rel = ""
    image_media = ""
    extracted = ""
    display_content = content

    if data_url.startswith("data:"):
        m = _DATA_URL_RE.match(data_url)
        if m:
            mime, b64 = m.group(1), m.group(2)
            raw = base64.b64decode(b64)
            ext = _ext_for_mime(mime)
            name = f"{stamp}-{aid}{ext}"
            dest = folder / name
            dest.write_bytes(raw)
            saved_rel = f"student-assets/{name}"
            image_media = f"/media/courses/{slug}/{saved_rel}"
            kind_hint = "image"
            extracted = ocr_image(dest)
            if extracted:
                display_content = (
                    f"【学生上传截图：{title}】\n"
                    f"OCR 识别文字：\n{extracted}"
                )
            else:
                display_content = (
                    f"【学生上传截图：{title}】\n"
                    "未能自动识别图中文字（本机可装 tesseract 以启用 OCR）。"
                    "请结合文件名与学生补充说明作答。"
                )
                if content and content != title:
                    display_content += "\n学生说明：" + content[:2000]
    elif content and kind_hint in {"code", "log", "text", ""}:
        # Persist text/code uploads for replay
        safe = re.sub(r"[^\w.\u4e00-\u9fff-]+", "_", title)[:40] or "note"
        if not Path(safe).suffix:
            safe += ".txt" if kind_hint != "code" else ".py"
        name = f"{stamp}-{aid}-{safe}"
        dest = folder / name
        dest.write_text(content, encoding="utf-8")
        saved_rel = f"student-assets/{name}"
        display_content = content
        if not kind_hint:
            kind_hint = _guess_asset_kind(content)

    kind = _guess_asset_kind(display_content, kind_hint or None)
    return {
        "kind": kind,
        "title": title,
        "content": display_content,
        "raw_content": content,
        "extracted_text": extracted,
        "path": saved_rel,
        "image_url": image_media,
        "image_data_url": "",  # prefer media URL; keep notes small
    }


def analyze_utterance(
    project: Path,
    *,
    slug: str,
    text: str,
    chapter: int = 1,
    player: dict | None = None,
    asset: dict | None = None,
) -> dict:
    slug = course.slugify(slug)
    if not slug:
        return {"ok": False, "error": "缺少课程 slug。"}
    player = player if isinstance(player, dict) else {}
    ingested = ingest_asset(project, slug, asset)
    raw = str(text or "").strip()
    asset_body = str(ingested.get("content") or "").strip()
    # Prefer student question + processed asset body
    if asset_body and raw and raw not in asset_body and asset_body not in raw:
        prompt_material = f"{raw}\n\n----\n素材正文：\n{asset_body}"
    else:
        prompt_material = raw or asset_body
    if not prompt_material.strip():
        return {"ok": False, "error": "没有可用的提问或素材。"}

    course_data = course.load_course(project, slug) or course.public_course(project, slug)
    notes = load_notes(project, slug)
    history = recent_notes_block(notes, limit=5)
    user = _context_block(course_data, chapter, player)
    if history:
        user += "\n\n" + history
    user += "\n\n学生本次提问/素材：\n" + prompt_material[:12000] + "\n\n请输出 JSON。"

    try:
        parsed = plan_lesson.chat_json(
            SYSTEM,
            [{"role": "user", "content": user}],
            max_tokens=1600,
            loose=True,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:400]}

    status = str(parsed.get("status") or "在提问").strip() or "在提问"
    try:
        understanding = int(parsed.get("understanding") or 50)
    except (TypeError, ValueError):
        understanding = 50
    understanding = max(0, min(100, understanding))
    questions = [str(x).strip() for x in (parsed.get("questions") or []) if str(x).strip()]
    blockers = [str(x).strip() for x in (parsed.get("blockers") or []) if str(x).strip()]
    tags = [str(x).strip() for x in (parsed.get("tags") or []) if str(x).strip()]
    reply = str(parsed.get("reply") or "我在主画面给你拆开讲。").strip()
    lecture_md = str(parsed.get("lecture_md") or "").strip()
    if not lecture_md:
        lecture_md = "## 答疑\n\n" + reply
    asset_kind = _guess_asset_kind(
        prompt_material, str(parsed.get("asset_kind") or ingested.get("kind") or "")
    )
    asset_title = str(parsed.get("asset_title") or ingested.get("title") or "学生素材").strip()
    beats = parsed.get("beats") if isinstance(parsed.get("beats"), list) else _default_beats(lecture_md)
    clean_beats = []
    for item in beats:
        if not isinstance(item, dict):
            continue
        clean_beats.append(
            {
                "at": float(item.get("at") or 0),
                "title": str(item.get("title") or "讲解"),
                "focus": "student" if item.get("focus") == "student" else "lecture",
            }
        )
    if not clean_beats:
        clean_beats = _default_beats(lecture_md)
    dur = max(16.0, max(b["at"] for b in clean_beats) + 8.0)

    stage_content = ingested.get("raw_content") or prompt_material
    if asset_kind == "image" and ingested.get("extracted_text"):
        stage_content = ingested["extracted_text"]
    elif asset_kind == "image":
        stage_content = ingested.get("content") or prompt_material

    entry = {
        "ts": int(time.time()),
        "chapter": int(chapter or 1),
        "text": raw or prompt_material[:500],
        "status": status,
        "understanding": understanding,
        "questions": questions,
        "blockers": blockers,
        "tags": tags,
        "reply": reply,
        "lecture_md": lecture_md,
        "asset_kind": asset_kind,
        "asset_title": asset_title,
        "asset_path": ingested.get("path") or "",
        "beats": clean_beats,
        "tutor_dur": round(dur, 2),
        "player": {
            "t": float(player.get("t") or 0),
            "clip_id": player.get("clip_id") or "",
            "clip_title": player.get("clip_title") or "",
            "kind": player.get("kind") or "",
        },
        "image_url": ingested.get("image_url") or "",
    }
    notes["entries"].append(entry)
    notes["updated_at"] = entry["ts"]
    path = save_notes(project, slug, notes)
    try:
        rel = str(path.relative_to(project))
    except ValueError:
        rel = str(path)
    return {
        "ok": True,
        "entry": entry,
        "summary": summarize_notes(notes),
        "path": rel,
        "ingest": {
            "path": ingested.get("path") or "",
            "kind": asset_kind,
            "ocr": bool(ingested.get("extracted_text")),
        },
        "tutor": {
            "kind": "temp_timeline",
            "dur": entry["tutor_dur"],
            "beats": clean_beats,
            "asset": {
                "kind": asset_kind,
                "title": asset_title,
                "content": stage_content[:20000],
                "image_url": entry.get("image_url") or "",
                "image_data_url": "",
            },
            "lecture_md": lecture_md,
            "reply": reply,
        },
    }


def summarize_notes(notes: dict) -> dict:
    entries = notes.get("entries") or []
    questions: list[str] = []
    statuses: dict[str, int] = {}
    understandings: list[int] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        st = str(item.get("status") or "其他")
        statuses[st] = statuses.get(st, 0) + 1
        try:
            understandings.append(int(item.get("understanding")))
        except (TypeError, ValueError):
            pass
        for q in item.get("questions") or []:
            q = str(q).strip()
            if q and q not in questions:
                questions.append(q)
    avg = round(sum(understandings) / len(understandings)) if understandings else None
    return {
        "count": len(entries),
        "avg_understanding": avg,
        "statuses": statuses,
        "open_questions": questions[-12:],
    }
