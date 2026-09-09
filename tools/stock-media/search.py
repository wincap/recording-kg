#!/usr/bin/env python3
"""Search and download stock video / BGM. Same sources MoneyPrinterPlus uses, plus Archive.org."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ENV_FILE = HERE / ".env"
OUT = ROOT / "recordings" / "stock-media"
LOCAL_BGM = HERE / "bgmusic"

UA = "recording-kg-stock-media/1.0"
ALLOWED_HOSTS = (
    "pexels.com",
    "pixabay.com",
    "archive.org",
    "jamendo.com",
)

def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _key(name: str) -> str:
    load_env()
    return (os.environ.get(name) or "").strip()


def sources() -> dict:
    load_env()
    return {
        "ok": True,
        "pexels": bool(_key("PEXELS_API_KEY")),
        "pixabay": bool(_key("PIXABAY_API_KEY")),
        "archive": True,
        "local_bgm": LOCAL_BGM.is_dir(),
    }


def _get(url: str, headers: dict | None = None, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _json(url: str, headers: dict | None = None) -> dict:
    return json.loads(_get(url, headers).decode("utf-8"))


def _safe_name(name: str) -> str:
    stem = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", name).strip(".-")[:80]
    return stem or "clip"


def _host_ok(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def search_videos(query: str, *, source: str = "auto", per_page: int = 8) -> list[dict]:
    query = query.strip() or "city night"
    source = (source or "auto").lower()
    if source == "pexels" and not _key("PEXELS_API_KEY"):
        raise RuntimeError("还没有 PEXELS_API_KEY，写在 tools/stock-media/.env")
    if source == "pixabay" and not _key("PIXABAY_API_KEY"):
        raise RuntimeError("还没有 PIXABAY_API_KEY，写在 tools/stock-media/.env")
    items: list[dict] = []
    if source in {"auto", "pexels"} and _key("PEXELS_API_KEY"):
        items.extend(_pexels_videos(query, per_page))
    if source in {"auto", "pixabay"} and _key("PIXABAY_API_KEY"):
        items.extend(_pixabay_videos(query, per_page))
    if source in {"auto", "archive"} and (source == "archive" or not items):
        items.extend(_archive_videos(query, per_page))
    return items[: max(per_page, 12)]


def search_photos(query: str, *, source: str = "auto", per_page: int = 6) -> list[dict]:
    query = query.strip() or "electric car"
    source = (source or "auto").lower()
    items: list[dict] = []
    if source in {"auto", "pexels"} and _key("PEXELS_API_KEY"):
        items.extend(_pexels_photos(query, per_page))
    if source in {"auto", "pixabay"} and _key("PIXABAY_API_KEY"):
        items.extend(_pixabay_photos(query, per_page))
    return items[: max(per_page, 8)]


def search_music(query: str, *, source: str = "auto", per_page: int = 8) -> list[dict]:
    query = query.strip() or "calm piano"
    source = (source or "auto").lower()
    if source == "pixabay" and not _key("PIXABAY_API_KEY"):
        raise RuntimeError("还没有 PIXABAY_API_KEY，写在 tools/stock-media/.env")
    items: list[dict] = []
    if source in {"auto", "local"}:
        items.extend(_local_bgm(query if source == "local" else ""))
    if source in {"auto", "pixabay"} and _key("PIXABAY_API_KEY"):
        items.extend(_pixabay_music(query, per_page))
    if source in {"auto", "archive"} and (source == "archive" or len(items) < 3):
        items.extend(_archive_music(query, per_page))
    return items[: max(per_page, 12)]


def _pick_video_file(files: list[dict]) -> dict | None:
    ranked = []
    for item in files:
        w = int(item.get("width") or 0)
        h = int(item.get("height") or 0)
        link = item.get("link") or item.get("url") or ""
        if not link:
            continue
        ranked.append((w * h, w, item))
    ranked.sort(reverse=True)
    for _, w, item in ranked:
        if w >= 1280:
            return item
    return ranked[0][2] if ranked else None


def _pexels_photos(query: str, per_page: int) -> list[dict]:
    q = urllib.parse.urlencode({"query": query, "per_page": per_page, "orientation": "landscape"})
    data = _json(
        f"https://api.pexels.com/v1/search?{q}",
        {"Authorization": _key("PEXELS_API_KEY")},
    )
    out = []
    for photo in data.get("photos") or []:
        src = photo.get("src") or {}
        url = src.get("landscape") or src.get("large2x") or src.get("large") or src.get("original") or ""
        if not url:
            continue
        out.append(
            {
                "id": f"pexels-photo-{photo.get('id')}",
                "source": "pexels",
                "kind": "photo",
                "title": (photo.get("alt") or f"Pexels #{photo.get('id')}")[:80],
                "author": photo.get("photographer") or "",
                "duration": 0,
                "thumb": src.get("small") or src.get("tiny") or url,
                "preview": src.get("medium") or url,
                "download": url,
                "page": photo.get("url") or "",
                "width": photo.get("width") or 0,
                "height": photo.get("height") or 0,
            }
        )
    return out


def _pixabay_photos(query: str, per_page: int) -> list[dict]:
    q = urllib.parse.urlencode(
        {
            "key": _key("PIXABAY_API_KEY"),
            "q": query,
            "per_page": per_page,
            "image_type": "photo",
            "orientation": "horizontal",
            "safesearch": "true",
        }
    )
    data = _json(f"https://pixabay.com/api/?{q}")
    out = []
    for hit in data.get("hits") or []:
        url = hit.get("largeImageURL") or hit.get("webformatURL") or ""
        if not url:
            continue
        out.append(
            {
                "id": f"pixabay-photo-{hit.get('id')}",
                "source": "pixabay",
                "kind": "photo",
                "title": (hit.get("tags") or "Pixabay photo").split(",")[0].strip(),
                "author": hit.get("user") or "",
                "duration": 0,
                "thumb": hit.get("previewURL") or url,
                "preview": hit.get("webformatURL") or url,
                "download": url,
                "page": hit.get("pageURL") or "",
                "width": hit.get("imageWidth") or 0,
                "height": hit.get("imageHeight") or 0,
            }
        )
    return out


def _pexels_videos(query: str, per_page: int) -> list[dict]:
    q = urllib.parse.urlencode({"query": query, "per_page": per_page, "orientation": "landscape"})
    data = _json(
        f"https://api.pexels.com/videos/search?{q}",
        {"Authorization": _key("PEXELS_API_KEY")},
    )
    out = []
    for video in data.get("videos") or []:
        file = _pick_video_file(video.get("video_files") or [])
        if not file:
            continue
        out.append(
            {
                "id": f"pexels-{video.get('id')}",
                "source": "pexels",
                "kind": "video",
                "title": f"Pexels #{video.get('id')}",
                "author": ((video.get("user") or {}).get("name") or ""),
                "duration": video.get("duration") or 0,
                "thumb": video.get("image") or "",
                "preview": file.get("link") or "",
                "download": file.get("link") or "",
                "page": video.get("url") or "",
                "width": file.get("width") or 0,
                "height": file.get("height") or 0,
            }
        )
    return out


def _pixabay_videos(query: str, per_page: int) -> list[dict]:
    q = urllib.parse.urlencode(
        {"key": _key("PIXABAY_API_KEY"), "q": query, "per_page": per_page, "video_type": "film"}
    )
    data = _json(f"https://pixabay.com/api/videos/?{q}")
    out = []
    for hit in data.get("hits") or []:
        videos = hit.get("videos") or {}
        file = videos.get("large") or videos.get("medium") or videos.get("small") or {}
        url = file.get("url") or ""
        if not url:
            continue
        out.append(
            {
                "id": f"pixabay-{hit.get('id')}",
                "source": "pixabay",
                "kind": "video",
                "title": (hit.get("tags") or "Pixabay video").split(",")[0].strip(),
                "author": hit.get("user") or "",
                "duration": hit.get("duration") or 0,
                "thumb": (videos.get("tiny") or {}).get("thumbnail") or hit.get("picture_id") or "",
                "preview": url,
                "download": url,
                "page": hit.get("pageURL") or "",
                "width": file.get("width") or 0,
                "height": file.get("height") or 0,
            }
        )
    return out


def _pixabay_music(query: str, per_page: int) -> list[dict]:
    data = _json(
        "https://pixabay.com/api/audio/?"
        + urllib.parse.urlencode({"key": _key("PIXABAY_API_KEY"), "q": query, "per_page": per_page})
    )
    out = []
    for hit in data.get("hits") or []:
        url = hit.get("audio") or hit.get("previewURL") or ""
        if not url:
            continue
        out.append(
            {
                "id": f"pixabay-audio-{hit.get('id')}",
                "source": "pixabay",
                "kind": "music",
                "title": hit.get("tags") or f"Pixabay #{hit.get('id')}",
                "author": hit.get("user") or "",
                "duration": hit.get("duration") or 0,
                "thumb": hit.get("thumbnail") or "",
                "preview": hit.get("previewURL") or url,
                "download": url,
                "page": hit.get("pageURL") or "",
            }
        )
    return out


def _archive_search(query: str, mediatype: str, per_page: int) -> list[dict]:
    q = f"mediatype:({mediatype}) AND ({query})"
    params = (
        "q=" + urllib.parse.quote(q)
        + "&fl[]=identifier&fl[]=title&fl[]=creator&sort[]=downloads+desc"
        + f"&rows={per_page}&page=1&output=json"
    )
    data = _json(f"https://archive.org/advancedsearch.php?{params}")
    docs = ((data.get("response") or {}).get("docs") or [])
    out = []
    kind = "video" if mediatype == "movies" else "music"
    for doc in docs:
        ident = doc.get("identifier") or ""
        if not ident:
            continue
        creator = doc.get("creator") or ""
        if isinstance(creator, list):
            creator = creator[0] if creator else ""
        out.append(
            {
                "id": f"archive-{ident}",
                "source": "archive",
                "kind": kind,
                "title": doc.get("title") or ident,
                "author": creator,
                "duration": 0,
                "thumb": f"https://archive.org/services/img/{ident}",
                "preview": f"https://archive.org/details/{ident}",
                "download": f"https://archive.org/download/{ident}",
                "page": f"https://archive.org/details/{ident}",
                "identifier": ident,
            }
        )
    return out


def _archive_videos(query: str, per_page: int) -> list[dict]:
    return _archive_search(query, "movies", per_page)


def _archive_music(query: str, per_page: int) -> list[dict]:
    return _archive_search(query, "audio", per_page)


def _local_bgm(query: str) -> list[dict]:
    if not LOCAL_BGM.is_dir():
        return []
    q = query.lower().strip()
    skip = {"calm piano", "bgm", ""}
    out = []
    for path in sorted(LOCAL_BGM.iterdir()):
        if path.suffix.lower() not in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}:
            continue
        if q and q not in skip and q not in path.name.lower():
            continue
        out.append(
            {
                "id": f"local-{path.name}",
                "source": "local",
                "kind": "music",
                "title": path.stem,
                "author": "local",
                "duration": 0,
                "thumb": "",
                "preview": f"/local-bgm/{path.name}",
                "download": str(path),
                "page": "",
                "local": True,
            }
        )
    return out


def resolve_archive_file(identifier: str, kind: str) -> str:
    data = _json(f"https://archive.org/metadata/{urllib.parse.quote(identifier)}")
    files = data.get("files") or []
    prefer = (".mp4", ".webm", ".ogv") if kind == "video" else (".mp3", ".ogg", ".flac", ".wav")
    for name_end in prefer:
        for item in files:
            name = item.get("name") or ""
            if name.lower().endswith(name_end) and not name.startswith("__"):
                return f"https://archive.org/download/{identifier}/{urllib.parse.quote(name)}"
    return ""


def download(item: dict, dest_dir: Path | None = None) -> dict:
    kind = item.get("kind") or "video"
    source = item.get("source") or "web"
    title = _safe_name(str(item.get("title") or item.get("id") or "clip"))
    kind_folder = {"video": "videos", "music": "music", "photo": "images"}.get(kind, "videos")
    folder = Path(dest_dir) if dest_dir else (OUT / kind_folder)
    folder.mkdir(parents=True, exist_ok=True)

    if item.get("local") or source == "local":
        src = Path(str(item.get("download") or ""))
        if not src.is_file():
            raise RuntimeError("本地文件不存在")
        dest = folder / src.name
        dest.write_bytes(src.read_bytes())
        return {"ok": True, "path": str(dest), "name": dest.name, "kind": kind}

    url = str(item.get("download") or "")
    if source == "archive" and item.get("identifier"):
        url = resolve_archive_file(str(item["identifier"]), kind) or url
    if not url.startswith("http"):
        raise RuntimeError("没有可下载地址")
    if not _host_ok(url):
        raise RuntimeError("不允许从这个域名下载")

    default_suffix = {"video": ".mp4", "music": ".mp3", "photo": ".jpg"}.get(kind, ".bin")
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower() or default_suffix
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mp3", ".wav", ".m4a", ".ogg"}:
        suffix = default_suffix
    dest = folder / f"{source}-{title}{suffix}"
    dest.write_bytes(_get(url, timeout=120))
    return {"ok": True, "path": str(dest), "name": dest.name, "kind": kind}
