#!/usr/bin/env python3
"""Load a generated DeepSeek job and edit SVG text in place, then rebuild PPTX."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from deepseek_generate import JOBS, OUT_DIR, build_pptx, rasterize

ET.register_namespace("", "http://www.w3.org/2000/svg")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def job_dir(job_id: str) -> Path:
    if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
        raise FileNotFoundError("invalid job")
    path = (JOBS / job_id).resolve()
    if path.parent != JOBS.resolve() or not path.is_dir():
        raise FileNotFoundError("job not found")
    return path


def list_jobs() -> list[dict]:
    if not JOBS.is_dir():
        return []
    items = []
    for path in JOBS.iterdir():
        svg_dir = path / "svg_output"
        if not path.is_dir() or path.name.startswith("_") or not svg_dir.is_dir():
            continue
        files = sorted(p.name for p in svg_dir.glob("*.svg"))
        lock = {}
        lock_path = path / "lock.json"
        if lock_path.exists():
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        items.append(
            {
                "id": path.name,
                "title": lock.get("title") or path.name,
                "slides": len(files),
                "mtime": path.stat().st_mtime,
                "edit": f"/edit?job={path.name}",
            }
        )
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def _leaf_text(root: ET.Element) -> list[ET.Element]:
    """Own text only: <text>65.8<tspan>%</tspan></text> yields two fields."""
    nodes = []
    for el in root.iter():
        if _local(el.tag) not in {"text", "tspan"}:
            continue
        if not (el.text or "").strip():
            continue
        nodes.append(el)
    return nodes


def extract_texts(svg: str) -> list[dict]:
    root = ET.fromstring(svg)
    out = []
    for i, el in enumerate(_leaf_text(root)):
        text = el.text or ""
        out.append(
            {
                "i": i,
                "text": text,
                "label": text.strip()[:24],
                "fill": el.get("fill") or "",
                "size": el.get("font-size") or "",
            }
        )
    return out


def apply_texts(svg: str, texts: list[str]) -> str:
    root = ET.fromstring(svg)
    nodes = _leaf_text(root)
    for i, el in enumerate(nodes):
        if i < len(texts):
            el.text = texts[i]
    return ET.tostring(root, encoding="unicode")


def load_job(job_id: str) -> dict:
    path = job_dir(job_id)
    svg_dir = path / "svg_output"
    files = sorted(p.name for p in svg_dir.glob("*.svg"))
    lock = {}
    lock_path = path / "lock.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    slides = []
    for name in files:
        svg = (svg_dir / name).read_text(encoding="utf-8")
        slides.append(
            {
                "file": name,
                "preview": f"/api/svg/{job_id}/svg_output/{name}",
                "texts": extract_texts(svg),
            }
        )
    return {
        "ok": True,
        "id": job_id,
        "title": lock.get("title") or job_id,
        "slides": slides,
        "pptx": lock.get("pptx") or "",
        "media": lock.get("media") or {},
        "edit": f"/edit?job={job_id}",
    }


def save_page(job_id: str, file_name: str, texts: list[str] | None = None, svg: str | None = None) -> dict:
    path = job_dir(job_id)
    name = Path(file_name).name
    if not name.endswith(".svg"):
        raise ValueError("not an svg")
    svg_path = (path / "svg_output" / name).resolve()
    if svg_path.parent != (path / "svg_output").resolve() or not svg_path.is_file():
        raise FileNotFoundError("page not found")
    if svg and "<svg" in svg:
        start = svg.find("<svg")
        end = svg.rfind("</svg>")
        if start < 0 or end < start:
            raise ValueError("not an svg")
        svg_path.write_text(svg[start : end + 6], encoding="utf-8")
    else:
        svg_path.write_text(apply_texts(svg_path.read_text(encoding="utf-8"), [str(t) for t in (texts or [])]), encoding="utf-8")
    png_path = path / "png" / (Path(name).stem + ".png")
    rasterize(svg_path, png_path)
    return {"ok": True, "file": name, "preview": f"/api/svg/{job_id}/svg_output/{name}"}


def export_job(job_id: str) -> dict:
    path = job_dir(job_id)
    svg_dir = path / "svg_output"
    png_dir = path / "png"
    pngs = []
    for svg_path in sorted(svg_dir.glob("*.svg")):
        if svg_path.name.startswith("_"):
            continue
        png_path = png_dir / (svg_path.stem + ".png")
        rasterize(svg_path, png_path)
        pngs.append(png_path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", job_id)[:40]
    pptx_name = f"{safe}-edit-{stamp}.pptx"
    dest = OUT_DIR / pptx_name
    lock_path = path / "lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    media = lock.get("media") if isinstance(lock.get("media"), dict) else {}
    media = dict(media)
    media["title"] = lock.get("title") or ""
    media = build_pptx(pngs, dest, media)
    lock["media"] = media
    lock["pptx"] = pptx_name
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    extra = 1 if media.get("video") else 0
    return {"ok": True, "download": f"/download/{pptx_name}", "slides": len(pngs) + extra}
