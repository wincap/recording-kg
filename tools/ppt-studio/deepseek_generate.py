#!/usr/bin/env python3
"""DeepSeek designs slides as SVG; we rasterize and pack a PPTX. No PPT Master."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ENV_FILE = HERE / ".env"
JOBS = HERE / "jobs"
OUT_DIR = ROOT / "recordings" / "ppt-studio"
RSVG = shutil.which("rsvg-convert") or "/opt/homebrew/bin/rsvg-convert"

PX_W, PX_H = 1920, 1080

Emit = Callable[[str, dict], None]

SYSTEM = """你直接画 16:9 幻灯片，输出一份完整 SVG。
画布：<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" width="1280" height="720">
字体：Microsoft YaHei, PingFang SC, Hiragino Sans GB, Arial, sans-serif。
中文直接写汉字。XML 只转义 & < > " '，不要 HTML 实体。

设计要求：
- 浅色底必须配深色字（标题接近 #1A2430）。白字只能写在深色块上。
- 装饰用半透明圆/椭圆；强调色只给数字、结论、图表。
- 可以渐变、半透明、阴影。排版干净，不要堆满。
- 只输出一个 <svg>…</svg>，不要 markdown，不要解释。"""

PALETTES = {
    "soft-business": {"bg": "#E4ECF3", "ink": "#1A2430", "accent": "#C45A1E", "muted": "#3D4F5E"},
    "campus": {"bg": "#F3EDE1", "ink": "#1A1F2E", "accent": "#6E1D2C", "muted": "#5C5346"},
    "glass": {"bg": "#0A0E27", "ink": "#E8EEF4", "accent": "#3DDDFC", "muted": "#8BA0B5"},
}

LESSON_RULES = """这是一节课的幻灯片，不是行业研报。
- 只画当前页任务里的内容，不要编造销量、车企、渗透率。
- 标题必须完整可见：长标题缩小字号或拆成两行，禁止单行溢出裁切。
- 有代码就画深色等宽代码块（如 JetBrains Mono / Menlo），代码完整可抄，禁止省略号、「如下」、灰色占位。
- 不要三列空卡片各塞一句灰字。玻璃卡可以，但每张卡要有短标题加两行说明。
- 浅色底深色字；白字只写在深色块上。"""

PRESETS = {
    "soft-business": {
        "name": "柔和商务",
        "style": """主色：浅灰蓝、淡青、柔和橙。
背景：#E4ECF3 到 #D5EDEA 的浅色对角渐变。
装饰：白色/浅灰半透明圆和椭圆，柔阴影。
强调：橙 #C45A1E 给数字和结论句，青 #1F6F6C 给章节号。
标题深墨蓝 #1A2430，说明 #3D4F5E。
气质：现代、简约、商务、科技但柔和。卡片半透明白玻璃。""",
    },
    "campus": {
        "name": "校园学院风",
        "style": """奶油纸底 #F3EDE1，酒红书脊 #6E1D2C，金细线 #A67C3D，墨字 #1A1F2E。
左侧 56px 书脊贯穿。标题可用宋体，正文无衬线。
像学期研报/讲义，不要玻璃、不要霓虹、不要圆角大卡片。""",
    },
    "glass": {
        "name": "深色玻璃",
        "style": """深底 #0A0E27。半透明玻璃卡片。
标题浅色，数字用亮青 #3DDDFC。
科技、分层、克制，不要浅色纸。""",
    },
}

DEFAULT_FACTS = """材料锁死（不要编造，不要补特斯拉中国 8 月整数）：
- 2026-09-02 口径。8 月狭义乘用车零售约 158 万，新能源约 104 万，渗透约 65.8%（乘联分会预估）。
- 1-7 月新能源零售 567.5 万，同比 -12%；批发 825.1 万，同比 +8%。
- 上半年新能源乘用车出口 223.1 万，同比 +124.3%。6 月渗透 62.8%，7 月 64.4%。
- 比亚迪 8 月 440293（+17.8%），出口 189466（42.9%）；1-8 月 2668015（-6.84%）。
- 上汽新能源 190836；奇瑞新能源 120909；零跑 103129；小鹏 39107；理想 37679；蔚来 35836；小米 3 万+（无整数）。
论题：今年赢的是渗透，不是国内零售。零售/批发/出口/交付四本账，不可加总。"""


def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def api_key() -> str:
    load_env()
    return (os.environ.get("DEEPSEEK_API_KEY") or "").strip()


def configured() -> dict:
    load_env()
    key = api_key()
    return {
        "ok": True,
        "configured": bool(key),
        "model": os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat",
        "presets": [{"id": k, "name": v["name"]} for k, v in PRESETS.items()],
        "default_facts": DEFAULT_FACTS,
        "engine": "deepseek+rsvg+python-pptx+stock-media",
        "stock": True,
    }


def chat(messages: list[dict], *, max_tokens: int = 4096) -> str:
    load_env()
    key = api_key()
    if not key:
        raise RuntimeError("还没有 DeepSeek Key。写在 tools/ppt-studio/.env 的 DEEPSEEK_API_KEY。")
    base = (os.environ.get("DEEPSEEK_BASE") or "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": 0.6,
            "max_tokens": max_tokens,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-800:]
        raise RuntimeError(f"DeepSeek HTTP {exc.code}: {detail}") from exc
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"DeepSeek 返回异常：{json.dumps(data, ensure_ascii=False)[:800]}") from exc


def extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("模型没有返回 JSON 计划")
    return json.loads(text[start : end + 1])


def extract_svg(text: str) -> str:
    start = text.find("<svg")
    end = text.rfind("</svg>")
    if start < 0 or end < start:
        raise RuntimeError("这一页没有完整 <svg>…</svg>")
    svg = text[start : end + 6]
    if "viewBox" not in svg:
        svg = svg.replace("<svg", '<svg viewBox="0 0 1280 720"', 1)
    return svg


def rasterize(svg_path: Path, png_path: Path) -> None:
    if not Path(RSVG).exists():
        raise RuntimeError("找不到 rsvg-convert，没法把 SVG 打进 PPTX。")
    png_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [RSVG, "-w", str(PX_W), "-h", str(PX_H), str(svg_path), "-o", str(png_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not png_path.exists():
        raise RuntimeError((result.stderr or result.stdout or "rsvg 失败").strip()[-800:])


def build_pptx(pngs: list[Path], dest: Path, media: dict | None = None) -> dict:
    from media_pack import pack_pptx

    return pack_pptx(pngs, dest, media) or {}


def _stock_media():
    from media_pack import collect, copy_next_to_svg, inject_photo, queries_from_plan

    return collect, copy_next_to_svg, inject_photo, queries_from_plan


def fallback_svg(title: str, body: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" width="1280" height="720" '
        'font-family="Microsoft YaHei, PingFang SC, Arial, sans-serif">'
        '<rect width="1280" height="720" fill="#E4ECF3"/>'
        f'<text x="72" y="320" font-size="40" fill="#1A2430">{escape(title)}</text>'
        f'<text x="72" y="380" font-size="20" fill="#3D4F5E">{escape(body[:80])}</text>'
        "</svg>"
    )


def file_slug(index: int, title: str) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", str(title or "")).strip("-")[:28] or "page"
    return f"{index:02d}_{slug}.svg"


def roster_from_slides(slides: list, title: str) -> list[dict]:
    roster: list[dict] = []
    for i, page in enumerate(slides or [], 1):
        page = page if isinstance(page, dict) else {"title": str(page), "bullets": []}
        page_title = str(page.get("title") or title or f"第{i}页").strip() or f"第{i}页"
        bullets = [str(b).strip() for b in (page.get("bullets") or []) if str(b).strip()]
        code = str(page.get("code") or "").strip()
        job = ["按这一页的材料画，不要换成别的主题。"]
        if bullets:
            job.append("要点：\n" + "\n".join(f"- {b}" for b in bullets))
        if code:
            job.append("必须画深色等宽代码块，把下面代码完整抄上：\n" + code)
        roster.append({"file": file_slug(i, page_title), "title": page_title, "job": "\n".join(job)})
    return roster


def facts_from_slides(title: str, slides: list) -> str:
    lines = [f"这一章标题：{title}", "必须按下面页纲出，不要另起炉灶："]
    for i, page in enumerate(slides or [], 1):
        page = page if isinstance(page, dict) else {"title": str(page), "bullets": []}
        lines.append(f"{i}. {page.get('title') or title}")
        for bullet in page.get("bullets") or []:
            text = str(bullet).strip()
            if text:
                lines.append(f"   - {text}")
        code = str(page.get("code") or "").strip()
        if code:
            lines.append("   代码：")
            lines.append(code)
    return "\n".join(lines)


def locked_slides(payload: dict) -> list:
    raw = payload.get("slides") or payload.get("locked_pages") or []
    if not isinstance(raw, list):
        return []
    return raw


def run(payload: dict, emit: Emit) -> dict:
    title = str(payload.get("title") or "").strip() or "未命名简报"
    style_id = str(payload.get("preset") or "soft-business")
    style_text = str(payload.get("style") or "").strip() or PRESETS.get(style_id, PRESETS["soft-business"])["style"]
    extra = str(payload.get("extra") or "").strip()
    slides = locked_slides(payload)
    if slides:
        facts = str(payload.get("facts") or "").strip() or facts_from_slides(title, slides)
        extra = "\n".join(x for x in (LESSON_RULES, extra) if x).strip()
        pages_n = max(1, min(len(slides), 12))
        want_stock = payload.get("stock", False)
    else:
        facts = str(payload.get("facts") or DEFAULT_FACTS).strip()
        pages_n = int(payload.get("pages") or 8)
        pages_n = max(4, min(pages_n, 10))
        want_stock = payload.get("stock", True)
    if isinstance(want_stock, str):
        want_stock = want_stock.lower() not in {"0", "false", "no"}

    if slides:
        emit("status", {"text": "按课纲锁页，DeepSeek 逐页手画…"})
        roster = roster_from_slides(slides, title)[:pages_n]
        plan = {
            "palette": dict(PALETTES.get(style_id) or PALETTES["soft-business"]),
            "pages": roster,
            "media": {},
        }
    else:
        emit("status", {"text": "DeepSeek 在定视觉锁和页纲…"})
        plan_user = (
            f"主题：{title}\n页数：{pages_n}\n\n视觉：\n{style_text}\n\n{facts}\n\n"
            f"{('补充：' + extra) if extra else ''}\n"
            "只返回 JSON：\n"
            '{"palette":{"bg":"#...","ink":"#...","accent":"#...","muted":"#..."},'
            '"media":{"photos":["english photo query","english photo query"],'
            '"video":"english video query","music":"english bgm query"},'
            '"pages":[{"file":"01_cover.svg","title":"封面标题","job":"这一页画什么"}]}'
        )
        plan = extract_json(
            chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": plan_user}], max_tokens=2048)
        )
        roster = plan.get("pages") or []
        if len(roster) < 2:
            raise RuntimeError("页纲太短，模型没有规划出页面")
        roster = roster[:pages_n]
    for i, page in enumerate(roster, start=1):
        if not str(page.get("file") or "").endswith(".svg"):
            page["file"] = f"{i:02d}_page.svg"

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", title).strip("-")[:36] or "deepseek"
    job_id = f"{slug}-{stamp}"
    project = JOBS / job_id
    svg_dir = project / "svg_output"
    png_dir = project / "png"
    svg_dir.mkdir(parents=True)
    (project / "lock.json").write_text(
        json.dumps(
            {"title": title, "style": style_text, "facts": facts, "extra": extra, "plan": plan},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    emit("plan", {"plan": plan, "pages": [p.get("file") for p in roster], "job": job_id})

    media: dict = {}
    copy_next_to_svg = inject_photo = None
    if want_stock:
        emit("status", {"text": "按主题搜一点配图、现场视频和 BGM…"})
        try:
            collect, copy_next_to_svg, inject_photo, queries_from_plan = _stock_media()
            media = collect(project, queries_from_plan(plan, title), emit)
            media["title"] = title
        except Exception as exc:
            emit("status", {"text": f"素材搜不到也继续出片：{exc}"})
            media = {}
            copy_next_to_svg = inject_photo = None

    palette = json.dumps(plan.get("palette") or {}, ensure_ascii=False)
    pngs: list[Path] = []
    files: list[str] = []
    photos = list(media.get("photos") or [])
    cover_photo = next((p for p in photos if p.get("slot") == "cover"), photos[0] if photos else None)
    for index, page in enumerate(roster, start=1):
        fname = str(page["file"])
        emit("status", {"text": f"DeepSeek 在画 {fname}（{index}/{len(roster)}）"})
        user = (
            f"视觉锁 palette={palette}\n整份风格：\n{style_text}\n\n材料：\n{facts}\n\n"
            f"{('硬性要求：' + extra + chr(10)) if extra else ''}"
            f"当前第 {index}/{len(roster)} 页，文件名 {fname}。\n"
            f"页标题：{page.get('title')}\n任务：{page.get('job')}\n"
        )
        if index == 1 and cover_photo:
            user += "这一页会铺一张全幅氛围实拍，左侧有浅色遮罩保证字能读。标题靠左，不要铺满整页。\n"
        user += "只输出一个完整 SVG。"
        raw = chat(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            max_tokens=4096,
        )
        try:
            svg = extract_svg(raw)
        except RuntimeError:
            emit("status", {"text": f"{fname} 不完整，再画一次"})
            raw = chat(
                [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": raw[:2500]},
                    {"role": "user", "content": "只输出一个完整 <svg viewBox=\"0 0 1280 720\">…</svg>。"},
                ],
                max_tokens=4096,
            )
            try:
                svg = extract_svg(raw)
            except RuntimeError:
                svg = fallback_svg(str(page.get("title") or fname), str(page.get("job") or ""))
        if index == 1 and cover_photo and copy_next_to_svg and inject_photo:
            href = copy_next_to_svg(svg_dir, Path(cover_photo["path"]), "stock-cover.jpg")
            svg = inject_photo(svg, href, "cover")
            cover_photo["page"] = fname
            cover_photo["href"] = href
        svg_path = svg_dir / fname
        svg_path.write_text(svg, encoding="utf-8")
        png_path = png_dir / (Path(fname).stem + ".png")
        try:
            rasterize(svg_path, png_path)
        except RuntimeError as exc:
            emit("status", {"text": f"{fname} 渲染失败，换成更朴素的一页：{exc}"})
            svg_path.write_text(fallback_svg(str(page.get("title") or fname), str(page.get("job") or "")), encoding="utf-8")
            rasterize(svg_path, png_path)
        pngs.append(png_path)
        files.append(fname)
        emit(
            "page",
            {
                "file": fname,
                "index": index,
                "total": len(roster),
                "preview": f"/api/svg/{job_id}/svg_output/{fname}",
            },
        )

    pptx_name = f"{slug}-deepseek-{stamp}.pptx"
    dest = OUT_DIR / pptx_name
    emit("status", {"text": "正在打包 PPTX…" if slides else "正在打包 PPTX（图 + 视频 + BGM）…"})
    media = build_pptx(pngs, dest, media)
    lock_path = project / "lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    lock["pptx"] = pptx_name
    lock["media"] = media
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {
        "ok": True,
        "job": job_id,
        "pptx": str(dest),
        "download": f"/download/{pptx_name}",
        "edit": f"/edit?job={job_id}",
        "slides": len(pngs) + (1 if media.get("video") else 0),
        "project": str(project),
        "svg_dir": str(svg_dir),
        "png_dir": str(png_dir),
        "files": files,
        "media": {
            "photos": len(media.get("photos") or []),
            "video": bool(media.get("video")),
            "music": bool(media.get("music")),
        },
    }
    emit("done", result)
    return result


def run_lesson(payload: dict, emit: Emit | None = None) -> dict:
    emit = emit or (lambda *_a, **_k: None)
    body = dict(payload or {})
    body.setdefault("preset", "soft-business")
    body["stock"] = False
    if not locked_slides(body):
        raise RuntimeError("这一章还没有页纲，没法画课用 PPT。")
    return run(body, emit)


def stream(payload: dict, emit: Emit) -> None:
    try:
        run(payload, emit)
    except Exception as exc:
        emit("error", {"error": str(exc)})


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="DeepSeek 手画幻灯片")
    parser.add_argument("--lesson", action="store_true", help="按课纲锁页出片，不另起炉灶")
    args = parser.parse_args()
    payload = json.load(sys.stdin)

    def emit(kind: str, data: dict | None = None) -> None:
        data = dict(data or {})
        line = json.dumps({"event": kind, **data}, ensure_ascii=False)
        sys.stderr.write(line + "\n")
        sys.stderr.flush()

    result = run_lesson(payload, emit) if args.lesson else run(payload, emit)
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    _cli()
