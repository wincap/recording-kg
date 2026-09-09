#!/usr/bin/env python3
"""Fill a PPT Master layout roster and export a native .pptx."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
PPT = ROOT / "vendor" / "ppt-master"
SKILL = PPT / "skills" / "ppt-master"
LAYOUTS = SKILL / "templates" / "layouts"
BRANDS = SKILL / "templates" / "brands"
STYLES = SKILL / "templates" / "styles"
PYTHON = PPT / ".venv" / "bin" / "python"
CHECKER = SKILL / "scripts" / "svg_quality_checker.py"
EXPORTER = SKILL / "scripts" / "svg_to_pptx.py"
OUT_DIR = ROOT / "recordings" / "ppt-studio"

PLACEHOLDER = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
ATTR_STRIP = re.compile(
    r'\s+data-pptx-(?:master|master-name|layout|layout-name|layer|placeholder|idx|bounds|carrier|editable)="[^"]*"'
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def catalog() -> dict:
    layouts = load_json(LAYOUTS / "layouts_index.json")
    brands = load_json(BRANDS / "brands_index.json")
    styles = load_json(STYLES / "styles_index.json")
    items = []
    for layout_id, meta in layouts.items():
        folder = LAYOUTS / layout_id / "templates"
        files = sorted(p.name for p in folder.glob("*.svg"))
        items.append(
            {
                "id": layout_id,
                "summary": meta.get("summary", ""),
                "canvas": meta.get("canvas_format", ""),
                "page_count": meta.get("page_count", len(files)),
                "files": files,
                "preview": files[0] if files else "",
            }
        )
    return {
        "layouts": items,
        "brands": [
            {"id": k, "summary": v.get("summary", ""), "color": v.get("primary_color", "#333")}
            for k, v in brands.items()
        ],
        "styles": [
            {"id": k, "summary": v.get("summary", "")}
            for k, v in styles.items()
        ],
    }


def brand_colors(brand_id: str | None) -> dict[str, str]:
    colors = {
        "primary": "#1E293B",
        "accent": "#CBD5E1",
        "bg": "#FFFFFF",
        "surface": "#F4F6F8",
        "border": "#D6DCE3",
        "muted": "#64748B",
        "text": "#1E293B",
    }
    if not brand_id:
        return colors
    spec = BRANDS / brand_id / "templates" / "design_spec.md"
    if not spec.exists():
        return colors
    text = spec.read_text(encoding="utf-8")
    for role, key in (
        ("primary", "primary"),
        ("accent", "accent"),
        ("bg", "bg"),
        ("surface", "surface"),
        ("border", "border"),
        ("muted-text", "muted"),
    ):
        m = re.search(rf"\|\s*{re.escape(role)}\s*\|\s*`?(#[0-9A-Fa-f]{{3,8}})`?", text)
        if m:
            colors[key] = m.group(1)
    colors["text"] = colors["primary"]
    return colors


def layout_files(layout_id: str) -> list[Path]:
    folder = LAYOUTS / layout_id / "templates"
    if not folder.is_dir():
        raise FileNotFoundError(f"没有这套版式：{layout_id}")
    files = sorted(folder.glob("*.svg"))
    if not files:
        raise FileNotFoundError(f"版式是空的：{layout_id}")
    return files


def page_context(deck: dict, page: dict, index: int, total: int) -> dict[str, str]:
    bullets = [str(b).strip() for b in (page.get("bullets") or []) if str(b).strip()]
    title = str(page.get("title") or deck["title"]).strip()
    left, right = bullets[: max(1, (len(bullets) + 1) // 2)], bullets[max(1, (len(bullets) + 1) // 2) :]
    joined = "  ·  ".join(bullets) if bullets else title
    steps = (bullets + ["", "", "", ""])[:4]
    items = (bullets + ["", "", "", "", ""])[:5]
    cards = (bullets + ["", "", ""])[:3]
    return {
        "TITLE": deck["title"],
        "SUBTITLE": deck["subtitle"],
        "BRAND_LINE": deck.get("brand_line") or deck["subtitle"],
        "DATE": datetime.now().date().isoformat(),
        "PAGE_TITLE": title,
        "CONTENT_AREA": joined,
        "KEY_MESSAGE": title,
        "SUPPORT_TEXT": joined or deck["subtitle"],
        "CAPTION": bullets[0] if bullets else deck["subtitle"],
        "FOOTER_NOTE": f"{deck['title']}  {index}/{total}",
        "PAGE_NUM": str(index),
        "LEFT_TITLE": "要点",
        "RIGHT_TITLE": "对照",
        "LEFT_CONTENT": "  ·  ".join(left) or title,
        "RIGHT_CONTENT": "  ·  ".join(right) or deck["subtitle"],
        "TOP_CONTENT": "  ·  ".join(left) or title,
        "BOTTOM_CONTENT": "  ·  ".join(right) or deck["subtitle"],
        "BLOCK_1": cards[0],
        "BLOCK_2": cards[1],
        "BLOCK_3": cards[2],
        "CARD_1": cards[0],
        "CARD_2": cards[1],
        "CARD_3": cards[2],
        "ITEM_1": items[0],
        "ITEM_2": items[1],
        "ITEM_3": items[2],
        "ITEM_4": items[3],
        "ITEM_5": items[4],
        "STEP_1": steps[0],
        "STEP_2": steps[1],
        "STEP_3": steps[2],
        "STEP_4": steps[3],
        "KPI_1": items[0] or "01",
        "KPI_2": items[1] or "02",
        "KPI_3": items[2] or "03",
        "KPI_4": items[3] or "04",
        "QUOTE_TEXT": title,
        "ATTRIBUTION": deck["subtitle"],
        "CLOSING_MESSAGE": title,
        "CTA_TEXT": deck["subtitle"],
        "CONTACT_LINE": deck["subtitle"],
        "CHAPTER_NUM": f"{index:02d}",
        "CHAPTER_TITLE": title,
        "CHAPTER_DESC": joined or deck["subtitle"],
        "SOURCE": deck.get("style") or "PPT Master",
        "Y_AXIS": "纵轴",
        "X_AXIS": "横轴",
        "QUADRANT_1": items[0],
        "QUADRANT_2": items[1],
        "QUADRANT_3": items[2],
        "QUADRANT_4": items[3],
    }


def inject_cover(svg: str) -> str:
    match = re.search(r"<svg[^>]*>", svg)
    if not match:
        return svg
    layer = (
        '<image href="../images/cover.jpg" x="0" y="0" width="1280" height="720" '
        'preserveAspectRatio="xMidYMid slice"/>'
    )
    return svg[: match.end()] + layer + svg[match.end() :]


def fill_svg(raw: str, ctx: dict[str, str], colors: dict[str, str]) -> str:
    raw = ATTR_STRIP.sub("", raw)

    def repl(match: re.Match[str]) -> str:
        value = ctx.get(match.group(1), "")
        return escape(value)

    filled = PLACEHOLDER.sub(repl, raw)
    swaps = {
        "#CBD5E1": colors["accent"],
        "#D6DCE3": colors["border"],
        "#F4F6F8": colors["surface"],
        "#F2F4F6": colors["surface"],
        "#1E293B": colors["text"],
        "#334155": colors["text"],
        "#64748B": colors["muted"],
        "#94A3B8": colors["text"],
        "#FFFFFF": colors["bg"],
    }
    for old, new in swaps.items():
        filled = filled.replace(old, new)
        filled = filled.replace(old.lower(), new)
    return expand_joined_text(filled)


def expand_joined_text(svg: str) -> str:
    pattern = re.compile(r"(<text\b([^>]*)>)([^<]+)(</text>)")

    def repl(match: re.Match[str]) -> str:
        attrs = match.group(2)
        body = match.group(3).strip()
        if " · " in body or "  ·  " in body:
            parts = [p.strip() for p in re.split(r"\s+·\s+", body) if p.strip()]
        elif len(body) > 28:
            parts = wrap_line(body, 22)
        else:
            return match.group(0)
        if len(parts) <= 1:
            return match.group(0)
        found = re.search(r'\bx="([\d.]+)"', attrs)
        x = found.group(1) if found else "0"
        chunks = []
        for i, part in enumerate(parts[:8]):
            dy = "0" if i == 0 else "40"
            chunks.append(f'<tspan x="{x}" dy="{dy}">{part}</tspan>')
        return f"<text{attrs}>{''.join(chunks)}</text>"

    return pattern.sub(repl, svg)


def wrap_line(text: str, width: int) -> list[str]:
    words = list(text)
    if len(text) <= width:
        return [text]
    lines, buf = [], ""
    for ch in words:
        buf += ch
        if len(buf) >= width and ch in "，。；、 ":
            lines.append(buf.strip())
            buf = ""
    if buf.strip():
        lines.append(buf.strip())
    return lines or [text]


def choose_layout(files: list[Path], page: dict, index: int, total: int) -> Path:
    names = {path.name: path for path in files}
    n = len([b for b in (page.get("bullets") or []) if str(b).strip()])
    if index == 1 and "01_title_slide.svg" in names:
        return names["01_title_slide.svg"]
    if n == 3 and "12_three_card.svg" in names:
        return names["12_three_card.svg"]
    if n == 2 and "04_two_content.svg" in names:
        return names["04_two_content.svg"]
    if n <= 1 and "10_hero_statement.svg" in names:
        return names["10_hero_statement.svg"]
    return names.get("02_title_content.svg") or files[min(index - 1, len(files) - 1)]


def write_spec_lock(project: Path, deck: dict) -> None:
    project.joinpath("spec_lock.md").write_text(
        f"""<!-- ppt-master-schema: spec-lock/v1 -->
# Execution Lock

## canvas
- viewBox: 0 0 1280 720
- format: {deck.get("canvas") or "ppt169"}

## communication
- primary_language: zh-Hans
- audience: {deck.get("audience") or "学员"}
- objective: {deck["title"]}
- core_message: {deck["subtitle"]}
- consumption_mode: live

## mode
- mode: free

## visual_style
- visual_style: {deck.get("style") or "workshop-teaching"}

## colors
- bg: #FFFFFF
- primary: #1E293B
- accent: #CBD5E1
- text: #1E293B

## typography
- font_family: Arial, Microsoft YaHei, sans-serif
- title_family: Arial, Microsoft YaHei, sans-serif
- body_family: Arial, Microsoft YaHei, sans-serif
- body: 22
- title: 36

## pptx_structure
- mode: flat
""",
        encoding="utf-8",
    )


def generate(payload: dict) -> dict:
    if not PYTHON.exists():
        raise RuntimeError("PPT Master 环境还没装好，先在 vendor/ppt-master 里装依赖。")

    layout_id = str(payload.get("layout") or "presentation_core")
    brand_id = payload.get("brand") or None
    style_id = payload.get("style") or "workshop-teaching"
    title = str(payload.get("title") or "").strip() or "未命名课程"
    subtitle = str(payload.get("subtitle") or "").strip() or "本地生成"
    audience = str(payload.get("audience") or "").strip() or "学员"
    pages = payload.get("pages") or []
    if not isinstance(pages, list) or not pages:
        pages = [
            {"title": title, "bullets": [subtitle]},
            {"title": "要点", "bullets": ["在页面里改课纲后再生成"]},
        ]

    files = layout_files(layout_id)
    colors = brand_colors(brand_id)
    brand_line = brand_id or style_id
    deck = {
        "title": title,
        "subtitle": subtitle,
        "audience": audience,
        "style": style_id,
        "brand_line": brand_line,
        "canvas": payload.get("canvas") or "",
    }

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", title).strip("-")[:40] or "lesson"
    project = PPT / "projects" / f"studio_{slug}_{stamp}"
    if project.exists():
        shutil.rmtree(project)
    svg_dir = project / "svg_output"
    svg_dir.mkdir(parents=True)
    write_spec_lock(project, deck)

    ai_info = None
    if payload.get("ai_image", True):
        spec = importlib.util.spec_from_file_location(
            "course_image_gen", ROOT / "tools" / "image-gen" / "generate.py"
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("找不到文生图工具")
        image_gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(image_gen)
        cover = project / "images" / "cover.jpg"
        prompt = str(payload.get("image_prompt") or "").strip() or (
            f"{title}, {subtitle}, 16:9 educational illustration, classroom, no watermark, no text overlay"
        )
        ai_info = image_gen.generate_image(prompt, cover)

    used = []
    total = len(pages)
    for i, page in enumerate(pages):
        row = page if isinstance(page, dict) else {"title": str(page)}
        src = choose_layout(files, row, i + 1, total)
        ctx = page_context(deck, row, i + 1, total)
        filled = fill_svg(src.read_text(encoding="utf-8"), ctx, colors)
        if i == 0 and ai_info:
            filled = inject_cover(filled)
        dest = svg_dir / f"{i + 1:02d}_{src.stem}.svg"
        dest.write_text(filled, encoding="utf-8")
        used.append(src.name)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pptx_name = f"{slug}-{stamp}.pptx"
    dest_pptx = OUT_DIR / pptx_name
    check = subprocess.run(
        [str(PYTHON), str(CHECKER), str(project), "--quick-generate", "--stage", "final", "--json"],
        cwd=str(PPT),
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        raise RuntimeError((check.stdout or check.stderr or "版式检查未通过").strip()[-1200:])

    cmd = [
        str(PYTHON),
        str(EXPORTER),
        str(project),
        "--quick-generate",
        "-o",
        str(dest_pptx),
    ]
    result = subprocess.run(cmd, cwd=str(PPT), capture_output=True, text=True)
    if result.returncode != 0 or not dest_pptx.exists():
        raise RuntimeError((result.stderr or result.stdout or "导出失败").strip()[-1200:])

    return {
        "ok": True,
        "pptx": str(dest_pptx),
        "download": f"/download/{pptx_name}",
        "slides": total,
        "layout": layout_id,
        "brand": brand_id,
        "style": style_id,
        "used_files": used,
        "svg_dir": str(svg_dir),
        "ai_image": ai_info,
        "log": (result.stdout or "")[-800:],
    }
