#!/usr/bin/env python3
"""Build chapter decks with DeepSeek hand-drawn SVG (not PPT Master fill-in)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parents[2]
PPT_STUDIO = ROOT / "tools" / "ppt-studio"
PPT_PY = PPT_STUDIO / ".venv" / "bin" / "python3"


def slides_markdown(title: str, slides: list[dict]) -> str:
    lines = [f"# {title or '本集'}", ""]
    for i, page in enumerate(slides or [], 1):
        lines.append(f"## {i}. {page.get('title') or '要点'}")
        for bullet in page.get("bullets") or []:
            lines.append(f"- {bullet}")
        code = str(page.get("code") or "").strip()
        if code:
            lines.append("```")
            lines.append(code)
            lines.append("```")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_slides(dest: Path, title: str, slides: list[dict]) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(slides_markdown(title, slides), encoding="utf-8")
    return dest


def write_practice_slide(dest: Path, title: str, bullets: list[str] | None = None) -> Path:
    """Local handoff card so PPT can hold on '接下来动手' without a DeepSeek redraw."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    heading = xml_escape((title or "接下来动手").strip() or "接下来动手")
    rows: list[str] = []
    y = 292
    for item in (bullets or [])[:4]:
        text = xml_escape(str(item).strip()[:48])
        if not text:
            continue
        rows.append(f'<circle cx="128" cy="{y - 10}" r="9" fill="#1F6F6C"/>')
        rows.append(
            f'<text x="156" y="{y}" font-family="PingFang SC, Hiragino Sans GB, Arial, sans-serif" '
            f'font-size="28" fill="#1A2430">{text}</text>'
        )
        y += 76
    dest.write_text(
        f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" width="1280" height="720">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#E4ECF3"/>
      <stop offset="100%" stop-color="#D5EDEA"/>
    </linearGradient>
  </defs>
  <rect width="1280" height="720" fill="url(#bg)"/>
  <circle cx="1100" cy="80" r="160" fill="#FFFFFF" opacity="0.22"/>
  <ellipse cx="180" cy="650" rx="220" ry="90" fill="#FFFFFF" opacity="0.18"/>
  <rect x="64" y="44" width="228" height="36" rx="18" fill="#1F6F6C"/>
  <text x="178" y="69" text-anchor="middle" font-family="PingFang SC, Arial, sans-serif" font-size="16" fill="#FFFFFF">PPT 讲完 · 动手</text>
  <text x="64" y="156" font-family="PingFang SC, Hiragino Sans GB, Arial, sans-serif" font-size="46" font-weight="700" fill="#1A2430">{heading}</text>
  <rect x="64" y="196" width="1152" height="456" rx="24" fill="#FFFFFF" fill-opacity="0.55"/>
  {''.join(rows)}
</svg>
''',
        encoding="utf-8",
    )
    return dest


def _as_pages(title: str, slides: list[dict]) -> list[dict]:
    pages: list[dict] = []
    for item in slides or []:
        page = {
            "title": item.get("title") or title,
            "bullets": list(item.get("bullets") or []),
        }
        code = str(item.get("code") or "").strip()
        if code:
            page["code"] = code
        pages.append(page)
    if not pages:
        pages = [{"title": title or "本集", "bullets": ["这一章的要点"]}]
    return pages


def _copy_glob(src_dir: Path | None, dest_dir: Path, pattern: str) -> list[Path]:
    if not src_dir or not src_dir.is_dir():
        return []
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for item in sorted(src_dir.glob(pattern)):
        target = dest_dir / item.name
        shutil.copy2(item, target)
        out.append(target)
    return out


def _replace_slide_dir(dest_dir: Path, svg_dir: Path | None, png_dir: Path | None) -> list[Path]:
    dest_dir = Path(dest_dir)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    pngs = _copy_glob(png_dir, dest_dir, "*.png")
    svgs = _copy_glob(svg_dir, dest_dir, "*.svg")
    return pngs or svgs


def _pump_stderr(proc: subprocess.Popen, log) -> None:
    assert proc.stderr is not None
    for raw in proc.stderr:
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if log:
                log(line)
            continue
        if not log:
            continue
        text = str(event.get("text") or "").strip()
        if text:
            log(text)
            continue
        if event.get("event") == "page" and event.get("file"):
            log(f"DeepSeek 画完 {event.get('file')}（{event.get('index')}/{event.get('total')}）")


def _run_deepseek_lesson(payload: dict, log=None) -> dict | None:
    py = PPT_PY if PPT_PY.is_file() else Path(sys.executable)
    cmd = [str(py), str(PPT_STUDIO / "deepseek_generate.py"), "--lesson"]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(PPT_STUDIO),
    )
    pump = threading.Thread(target=_pump_stderr, args=(proc, log), daemon=True)
    pump.start()
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(payload, ensure_ascii=False))
    proc.stdin.close()
    assert proc.stdout is not None
    out = proc.stdout.read()
    code = proc.wait()
    pump.join(timeout=5)
    if code != 0:
        if log:
            log("DeepSeek 画页失败。")
        return None
    try:
        data = json.loads(out.strip().splitlines()[-1] if out.strip() else "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("pptx"):
        return None
    return data


def write_pptx(dest: Path, title: str, slides: list[dict], log=None) -> Path | None:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pages = _as_pages(title, slides)
    payload = {
        "title": title or "本集",
        "slides": pages,
        "preset": "soft-business",
        "stock": False,
    }
    data = _run_deepseek_lesson(payload, log)
    if not data:
        return None
    src = Path(str(data.get("pptx") or ""))
    if not src.is_file():
        return None
    dest.write_bytes(src.read_bytes())
    _replace_slide_dir(
        dest.parent / "slides",
        Path(str(data.get("svg_dir") or "")),
        Path(str(data.get("png_dir") or "")),
    )
    return dest


def raster_frames(svgs: list[Path], work: Path) -> list[Path]:
    converter = shutil.which("rsvg-convert")
    magick = shutil.which("magick")
    frames: list[Path] = []
    work.mkdir(parents=True, exist_ok=True)
    for i, svg in enumerate(svgs, 1):
        png = work / f"{i:02d}.png"
        if converter:
            cmd = [converter, "-w", "1920", "-h", "1080", str(svg), "-o", str(png)]
        elif magick:
            cmd = [magick, str(svg), "-resize", "1920x1080", str(png)]
        else:
            return []
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not png.is_file():
            return []
        frames.append(png)
    return frames
