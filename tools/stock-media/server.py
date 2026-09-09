#!/usr/bin/env python3
"""Local stock video + BGM search desk."""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from search import LOCAL_BGM, download, search_music, search_videos, sources

HERE = Path(__file__).resolve().parent
INDEX = HERE / "web" / "index.html"
OUT = HERE.parents[1] / "recordings" / "stock-media"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[stock] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path in {"/", "/index.html"}:
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._json(200, sources())
            return
        if path.startswith("/local-bgm/"):
            name = Path(path).name
            target = (LOCAL_BGM / name).resolve()
            if target.parent != LOCAL_BGM.resolve() or not target.is_file():
                self._json(404, {"ok": False, "error": "missing"})
                return
            ctype = mimetypes.guess_type(target.name)[0] or "audio/mpeg"
            self._send(200, target.read_bytes(), ctype)
            return
        if path.startswith("/files/"):
            rel = Path(path.removeprefix("/files/"))
            target = (OUT / rel).resolve()
            if OUT.resolve() not in target.parents or not target.is_file():
                self._json(404, {"ok": False, "error": "missing"})
                return
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), ctype)
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        payload = self._body()
        try:
            if path == "/api/search":
                kind = str(payload.get("kind") or "video")
                query = str(payload.get("query") or "")
                source = str(payload.get("source") or "auto")
                items = search_music(query, source=source) if kind == "music" else search_videos(query, source=source)
                self._json(200, {"ok": True, "items": items})
                return
            if path == "/api/download":
                result = download(payload.get("item") or payload)
                self._json(200, result)
                return
        except Exception as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        self._json(404, {"ok": False, "error": "not found"})


def main() -> None:
    host, port = "127.0.0.1", 8767
    print(f"找视频 / 找 BGM：http://{host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
