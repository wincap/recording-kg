#!/usr/bin/env python3
"""Search a little stock photo/video/BGM and pack them into a PPTX."""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from lxml.etree import SubElement

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.opc.constants import CONTENT_TYPE as CT
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.util import Emu, Inches, Pt

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"

HERE = Path(__file__).resolve().parent
STOCK_SEARCH = HERE.parent / "stock-media" / "search.py"
FALLBACK_BGM = HERE.parents[1] / "recordings" / "stock-media" / "music" / "archive-I-SWAY.mp3"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"

# Office / PowerPoint: H.264 + AAC in MP4, MP3 44.1kHz. 4K / WebM / 48kHz odd MP3 often won't play.

SLIDE_W = Inches(13.333333)
SLIDE_H = Inches(7.5)
Emit = Callable[[str, dict], None]


def _stock():
    spec = importlib.util.spec_from_file_location("stock_search", STOCK_SEARCH)
    if spec is None or spec.loader is None:
        raise RuntimeError("找不到 stock-media/search.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def default_queries(title: str) -> dict:
    if any(word in title for word in ("新能源", "汽车", "电动", "销量", "BYD", "比亚迪")):
        return {
            "photos": ["electric vehicle charging station china"],
            "video": "electric vehicle driving highway",
            "music": "calm piano",
        }
    return {
        "photos": ["soft daylight architecture interior"],
        "video": "city timelapse aerial",
        "music": "calm piano",
    }


def queries_from_plan(plan: dict, title: str) -> dict:
    fallback = default_queries(title)
    raw = plan.get("media") if isinstance(plan, dict) else None
    if not isinstance(raw, dict):
        return fallback
    photos = raw.get("photos") or raw.get("photo") or fallback["photos"]
    if isinstance(photos, str):
        photos = [photos]
    photos = [str(p).strip() for p in photos if str(p).strip()][:1] or fallback["photos"][:1]
    video = str(raw.get("video") or fallback["video"]).strip()
    music = str(raw.get("music") or fallback["music"]).strip()
    return {"photos": photos, "video": video, "music": music}


def collect(project: Path, queries: dict, emit: Emit) -> dict:
    """Download 1-2 photos, 1 video, 1 BGM into job/media. Never abort the deck."""
    stock = _stock()
    media_dir = project / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    out: dict = {"photos": [], "video": "", "poster": "", "music": "", "queries": queries}

    for query in (queries.get("photos") or [])[:1]:
        slot = "cover"
        try:
            emit("status", {"text": f"在搜配图：{query}"})
            hits = stock.search_photos(query, per_page=4)
            if not hits:
                emit("status", {"text": f"配图没搜到：{query}"})
                continue
            saved = stock.download(hits[0], dest_dir=media_dir)
            dest = Path(saved["path"])
            out["photos"].append(
                {
                    "file": f"media/{dest.name}",
                    "path": str(dest),
                    "query": query,
                    "slot": slot,
                    "title": hits[0].get("title") or dest.name,
                    "author": hits[0].get("author") or "",
                }
            )
            emit("media", {"text": f"配图 {dest.name}（{hits[0].get('author') or 'stock'}）"})
        except Exception as exc:
            emit("status", {"text": f"配图跳过：{exc}"})

    try:
        q = str(queries.get("video") or "city")
        emit("status", {"text": f"在搜现场视频：{q}"})
        hits = stock.search_videos(q, per_page=5)
        pick = None
        for item in hits:
            dur = int(item.get("duration") or 0)
            if 4 <= dur <= 25:
                pick = item
                break
        pick = pick or (hits[0] if hits else None)
        if pick:
            saved = stock.download(pick, dest_dir=media_dir)
            raw_video = Path(saved["path"])
            out["video_src"] = f"media/{raw_video.name}"
            video = to_ppt_video(raw_video, media_dir / "ppt-video.mp4")
            out["video"] = f"media/{video.name}"
            poster = media_dir / "ppt-poster.jpg"
            if _poster(video, poster):
                out["poster"] = "media/ppt-poster.jpg"
            emit("media", {"text": f"视频已转成 PPT 能播的 H.264 1080p（{video.name}）"})
    except Exception as exc:
        emit("status", {"text": f"视频跳过：{exc}"})

    try:
        q = str(queries.get("music") or "calm piano")
        emit("status", {"text": f"在找 BGM：{q}"})
        hits = stock.search_music(q, per_page=6)
        music_path = None
        if hits:
            local = next((h for h in hits if h.get("local")), hits[0])
            saved = stock.download(local, dest_dir=media_dir)
            music_path = Path(saved["path"])
        elif FALLBACK_BGM.is_file():
            dest = media_dir / FALLBACK_BGM.name
            dest.write_bytes(FALLBACK_BGM.read_bytes())
            music_path = dest
            emit("status", {"text": "网上 BGM 没搜到，用本机已下载的一首"})
        if music_path:
            ppt_audio = to_ppt_audio(music_path, media_dir / "ppt-audio.mp3")
            out["music_src"] = f"media/{music_path.name}"
            out["music"] = f"media/{ppt_audio.name}"
            emit("media", {"text": f"BGM 已转成 PPT 能播的 44.1k MP3（{ppt_audio.name}）"})
    except Exception as exc:
        emit("status", {"text": f"BGM 跳过：{exc}"})
        if FALLBACK_BGM.is_file():
            dest = media_dir / FALLBACK_BGM.name
            dest.write_bytes(FALLBACK_BGM.read_bytes())
            try:
                ppt_audio = to_ppt_audio(dest, media_dir / "ppt-audio.mp3")
                out["music_src"] = f"media/{dest.name}"
                out["music"] = f"media/{ppt_audio.name}"
            except Exception:
                out["music"] = f"media/{dest.name}"

    return out


def _run_ffmpeg(args: list[str]) -> None:
    if not Path(FFMPEG).exists():
        raise RuntimeError("找不到 ffmpeg，没法把素材转成 PPT 能播的格式。")
    result = subprocess.run([FFMPEG, "-y", "-hide_banner", *args], capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "ffmpeg 失败").strip()[-800:]
        raise RuntimeError(err)


def _has_audio(path: Path) -> bool:
    if not Path(FFPROBE).exists():
        return False
    result = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def to_ppt_video(src: Path, dest: Path) -> Path:
    """H.264 Main + AAC, ≤1080p, even size, faststart. Silent clips get a mute AAC track."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        "scale=1920:1080:force_original_aspect_ratio=decrease,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )
    if _has_audio(src):
        _run_ffmpeg(
            [
                "-i",
                str(src),
                "-vf",
                vf,
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-c:v",
                "libx264",
                "-profile:v",
                "main",
                "-level",
                "4.0",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-movflags",
                "+faststart",
                "-c:a",
                "aac",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-b:a",
                "160k",
                "-sn",
                "-dn",
                "-write_tmcd",
                "0",
                str(dest),
            ]
        )
    else:
        _run_ffmpeg(
            [
                "-i",
                str(src),
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-profile:v",
                "main",
                "-level",
                "4.0",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-movflags",
                "+faststart",
                "-c:a",
                "aac",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-b:a",
                "160k",
                "-shortest",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-sn",
                "-dn",
                "-write_tmcd",
                "0",
                str(dest),
            ]
        )
    if not dest.is_file() or dest.stat().st_size < 1000:
        raise RuntimeError("视频转码后文件不可用")
    return dest


def to_ppt_audio(src: Path, dest: Path) -> Path:
    """MPEG-1 Layer III, 44.1 kHz stereo — the MP3 profile PowerPoint actually plays."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-i",
            str(src),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-b:a",
            "192k",
            "-id3v2_version",
            "3",
            str(dest),
        ]
    )
    if not dest.is_file() or dest.stat().st_size < 1000:
        raise RuntimeError("音频转码后文件不可用")
    return dest


def _needs_encode(src: Path, dest: Path) -> bool:
    if src.resolve() == dest.resolve():
        return False
    if not dest.is_file() or dest.stat().st_size < 1000:
        return True
    return dest.stat().st_mtime < src.stat().st_mtime


def _first_existing(project: Path, rels: list[str], extra: list[Path] | None = None) -> Path | None:
    for rel in rels:
        if not rel:
            continue
        path = project / rel
        if path.is_file():
            return path
    for path in extra or []:
        if path.is_file():
            return path
    return None


def ensure_ppt_media(project: Path, media: dict) -> dict:
    media = dict(media or {})
    media_dir = project / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    dest = media_dir / "ppt-video.mp4"
    extras = [p for p in media_dir.glob("*.mp4") if p.name != "ppt-video.mp4"]
    src = _first_existing(
        project,
        [str(media.get("video_src") or ""), str(media.get("video") or "")],
        extras + ([dest] if dest.is_file() else []),
    )
    if src:
        if src.name != "ppt-video.mp4":
            media["video_src"] = f"media/{src.name}"
        if _needs_encode(src, dest):
            to_ppt_video(src, dest)
        if dest.is_file():
            media["video"] = "media/ppt-video.mp4"
            poster = media_dir / "ppt-poster.jpg"
            if _poster(dest, poster):
                media["poster"] = "media/ppt-poster.jpg"
    audio_dest = media_dir / "ppt-audio.mp3"
    audio_extras = [p for p in media_dir.glob("*.mp3") if p.name != "ppt-audio.mp3"]
    audio_src = _first_existing(
        project,
        [str(media.get("music_src") or ""), str(media.get("music") or "")],
        audio_extras + ([audio_dest] if audio_dest.is_file() else []),
    )
    if audio_src:
        if audio_src.name != "ppt-audio.mp3":
            media["music_src"] = f"media/{audio_src.name}"
        if _needs_encode(audio_src, audio_dest):
            to_ppt_audio(audio_src, audio_dest)
        if audio_dest.is_file():
            media["music"] = "media/ppt-audio.mp3"
    return media


def _poster(video: Path, dest: Path) -> bool:
    if not Path(FFMPEG).exists():
        return False
    result = subprocess.run(
        [
            FFMPEG,
            "-y",
            "-hide_banner",
            "-ss",
            "1",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            "scale=1920:-2",
            "-q:v",
            "3",
            str(dest),
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and dest.exists()


def strip_stock(svg: str) -> str:
    svg = re.sub(r'<image[^>]*id="stockPhoto"[^>]*/?>\s*<rect[^>]*fill="none"[^>]*/?>', "", svg)
    svg = re.sub(r'<image[^>]*id="stockPhoto"[^>]*/?>', "", svg)
    svg = re.sub(r'<rect[^>]*id="photoVeil"[^>]*/?>', "", svg)
    svg = re.sub(r'<rect[^>]*id="stockPhotoFrame"[^>]*/?>', "", svg)
    svg = re.sub(r'<linearGradient id="photoVeilGrad"[\s\S]*?</linearGradient>', "", svg)
    return svg


def inject_photo(svg: str, href: str, slot: str = "cover") -> str:
    """Cover only: full-bleed landscape behind type, left veil keeps titles readable."""
    svg = strip_stock(svg)
    if slot != "cover":
        return svg
    grad = (
        '<linearGradient id="photoVeilGrad" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0%" stop-color="#E8EEF4" stop-opacity="0.96"/>'
        '<stop offset="38%" stop-color="#E8EEF4" stop-opacity="0.88"/>'
        '<stop offset="62%" stop-color="#E8EEF4" stop-opacity="0.28"/>'
        '<stop offset="100%" stop-color="#E8EEF4" stop-opacity="0.06"/>'
        "</linearGradient>"
    )
    if "id=\"photoVeilGrad\"" not in svg:
        if "<defs>" in svg:
            svg = svg.replace("<defs>", "<defs>" + grad, 1)
        else:
            svg = svg.replace("<svg", "<svg", 1)
            svg = re.sub(r"(<svg[^>]*>)", r"\1<defs>" + grad + "</defs>", svg, count=1)
    layer = (
        f'<image id="stockPhoto" href="{escape(href)}" x="0" y="0" width="1280" height="720" '
        'preserveAspectRatio="xMidYMid slice"/>'
        '<rect id="photoVeil" width="1280" height="720" fill="url(#photoVeilGrad)"/>'
    )
    match = re.search(r'<rect[^>]*(?:width="1280"|width=\'1280\')[^>]*/>', svg)
    if match:
        return svg[: match.end()] + layer + svg[match.end() :]
    return svg.replace("</svg>", layer + "</svg>", 1)


def copy_next_to_svg(svg_dir: Path, src: Path, name: str) -> str:
    dest = svg_dir / name
    dest.write_bytes(Path(src).read_bytes())
    return name


def apply_photos(svg_dir: Path, media: dict) -> None:
    photos = media.get("photos") or []
    files = sorted(p.name for p in svg_dir.glob("*.svg") if not p.name.startswith("_"))
    if not files:
        return
    for photo in photos:
        src = Path(photo.get("path") or "")
        if not src.is_file():
            continue
        slot = photo.get("slot") or "cover"
        if slot != "cover":
            continue
        local = copy_next_to_svg(svg_dir, src, "stock-cover.jpg")
        target = files[0]
        path = svg_dir / target
        path.write_text(inject_photo(path.read_text(encoding="utf-8"), local, "cover"), encoding="utf-8")
        photo["page"] = target
        photo["href"] = local
    for extra in files[1:]:
        path = svg_dir / extra
        cleaned = strip_stock(path.read_text(encoding="utf-8"))
        path.write_text(cleaned, encoding="utf-8")


def _autoplay(slide) -> None:
    for cond in slide._element.xpath(".//p:cond"):
        if cond.get("delay") == "indefinite":
            cond.set("delay", "0")
            return


def _wire_bgm(slide, shape) -> None:
    """MP3 must sit on-slide as audio (not off-slide video). Autoplay, loop, play across slides."""
    pic = shape._element
    for node in pic.xpath('.//*[local-name()="videoFile"]'):
        node.tag = f"{{{A_NS}}}audioFile"
    part = slide.part
    for rel in part.rels.values():
        target = str(getattr(rel, "target_ref", "") or "")
        if target.endswith(".mp3") and getattr(rel, "_reltype", None) == RT.VIDEO:
            rel._reltype = RT.AUDIO
            rel.__dict__.pop("reltype", None)
    sld = slide._element
    for node in sld.xpath('.//*[local-name()="video"]'):
        parent = node.getparent()
        if parent is None or not parent.tag.endswith("childTnLst"):
            continue
        node.tag = f"{{{P_NS}}}audio"
        for ctn in node.xpath('.//*[local-name()="cTn"]'):
            ctn.set("dur", "indefinite")
            ctn.set("repeatCount", "indefinite")
            ctn.set("fill", "hold")
            ctn.set("masterRel", "sameClick")
            ctn.attrib.pop("display", None)
            for cond in ctn.xpath('./*[local-name()="stCondLst"]/*[local-name()="cond"]'):
                cond.set("delay", "0")
            if not ctn.xpath('./*[local-name()="endCondLst"]'):
                end = SubElement(ctn, f"{{{P_NS}}}endCondLst")
                cond = SubElement(end, f"{{{P_NS}}}cond")
                cond.set("evt", "onStopAudio")
                cond.set("delay", "0")
                tgt = SubElement(cond, f"{{{P_NS}}}tgtEl")
                SubElement(tgt, f"{{{P_NS}}}sldTgt")


def pack_pptx(pngs: list[Path], dest: Path, media: dict | None = None) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    project = pngs[0].parent.parent if pngs else dest.parent
    media = ensure_ppt_media(project, media or {})
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    blank = prs.slide_layouts[6]

    def add_png(png: Path):
        slide = prs.slides.add_slide(blank)
        slide.shapes.add_picture(str(png), Emu(0), Emu(0), width=SLIDE_W, height=SLIDE_H)
        return slide

    first = None
    for i, png in enumerate(pngs):
        slide = add_png(png)
        if i == 0:
            first = slide

    video_rel = media.get("video") or ""
    video = project / video_rel if video_rel else None
    if video and video.is_file():
        poster_rel = media.get("poster") or ""
        poster = (project / poster_rel) if poster_rel and (project / poster_rel).is_file() else None
        title = str(media.get("title") or "")
        _video_closer(prs, blank, video, poster, title)

    music_rel = media.get("music") or ""
    music = project / music_rel if music_rel else None
    if first is not None and music and music.is_file():
        speaker = first.shapes.add_movie(
            str(music),
            Inches(12.62),
            Inches(6.88),
            Inches(0.52),
            Inches(0.46),
            poster_frame_image=None,
            mime_type="audio/mpeg",
        )
        _wire_bgm(first, speaker)

    prs.save(str(dest))
    return media


def _video_closer(prs, blank, video: Path, poster: Path | None, title: str) -> None:
    """Last page: full-bleed B-roll, caption sits on the bottom edge — not a dumped extra slide in the middle."""
    slide = prs.slides.add_slide(blank)
    slide.shapes.add_movie(
        str(video),
        Emu(0),
        Emu(0),
        SLIDE_W,
        SLIDE_H,
        poster_frame_image=str(poster) if poster and poster.is_file() else None,
        mime_type=CT.MP4,
    )
    bar = slide.shapes.add_shape(1, Emu(0), Inches(6.55), SLIDE_W, Inches(0.95))
    bar.fill.solid()
    bar.fill.fore_color.rgb = RGBColor(0x12, 0x20, 0x2B)
    bar.line.fill.background()
    label = (title.strip() or "现场") + "  ·  单击播放"
    box = slide.shapes.add_textbox(Inches(0.7), Inches(6.72), Inches(12), Inches(0.55))
    tf = box.text_frame
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    run = p.add_run()
    run.text = label
    run.font.size = Pt(18)
    run.font.color.rgb = RGBColor(0xE8, 0xEE, 0xF3)
    run.font.name = "Microsoft YaHei"


def _ghost_poster(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return
    from PIL import Image

    Image.new("RGB", (8, 8), (18, 32, 43)).save(dest, "PNG")
