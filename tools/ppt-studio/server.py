#!/usr/bin/env python3
"""Local page: pick a PPT Master template, then export a .pptx."""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from generate import OUT_DIR, SKILL, catalog, generate
from deepseek_generate import JOBS, configured, stream
from editor import export_job, list_jobs, load_job, save_page

HERE = Path(__file__).resolve().parent
INDEX = HERE / "web" / "index.html"
DEEPSEEK = HERE / "web" / "deepseek.html"
EDIT = HERE / "web" / "edit.html"
TPL = SKILL / "templates"
LOCK = threading.Lock()
BUSY = {"on": False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[ppt] {self.address_string()} {fmt % args}", flush=True)

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
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path in {"/", "/index.html"}:
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            return
        if path in {"/deepseek", "/deepseek.html"}:
            self._send(200, DEEPSEEK.read_bytes(), "text/html; charset=utf-8")
            return
        if path in {"/edit", "/edit.html"}:
            self._send(200, EDIT.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/jobs":
            self._json(200, {"ok": True, "jobs": list_jobs()})
            return
        if path.startswith("/api/job/"):
            job_id = path.removeprefix("/api/job/").split("/", 1)[0]
            try:
                self._json(200, load_job(job_id))
            except FileNotFoundError:
                self._json(404, {"ok": False, "error": "找不到这份稿"})
            except Exception as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            return
        if path == "/api/catalog":
            self._json(200, catalog())
            return
        if path == "/api/deepseek/status":
            self._json(200, configured())
            return
        if path.startswith("/api/svg/"):
            rel = Path(path.removeprefix("/api/svg/"))
            target = (JOBS / rel).resolve()
            root = JOBS.resolve()
            if root not in target.parents or not target.is_file() or target.suffix.lower() not in {".svg", ".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                self._json(404, {"ok": False, "error": "missing file"})
                return
            ctype = "image/svg+xml" if target.suffix.lower() == ".svg" else (mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self._send(200, target.read_bytes(), ctype)
            return
        if path.startswith("/api/media/"):
            rel = Path(path.removeprefix("/api/media/"))
            target = (JOBS / rel).resolve()
            root = JOBS.resolve()
            if root not in target.parents or not target.is_file():
                self._json(404, {"ok": False, "error": "missing media"})
                return
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), ctype)
            return
        if path.startswith("/tpl/"):
            rel = Path(path.removeprefix("/tpl/"))
            target = (TPL / rel).resolve()
            if TPL not in target.parents and target != TPL:
                self._json(403, {"ok": False, "error": "forbidden"})
                return
            if not target.is_file():
                self._json(404, {"ok": False, "error": "missing template"})
                return
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix == ".svg":
                ctype = "image/svg+xml"
            self._send(200, target.read_bytes(), ctype)
            return
        if path.startswith("/download/"):
            name = Path(path).name
            target = (OUT_DIR / name).resolve()
            if target.parent != OUT_DIR.resolve() or not target.is_file():
                self._json(404, {"ok": False, "error": "file not found"})
                return
            self._send(
                200,
                target.read_bytes(),
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path.startswith("/api/job/") and path.endswith("/page"):
            job_id = path.removeprefix("/api/job/").removesuffix("/page").strip("/")
            body = self._body()
            try:
                self._json(
                    200,
                    save_page(
                        job_id,
                        str(body.get("file") or ""),
                        list(body.get("texts") or []),
                        body.get("svg"),
                    ),
                )
            except FileNotFoundError:
                self._json(404, {"ok": False, "error": "找不到这一页"})
            except Exception as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            return
        if path.startswith("/api/job/") and path.endswith("/export"):
            job_id = path.removeprefix("/api/job/").removesuffix("/export").strip("/")
            with LOCK:
                if BUSY["on"]:
                    self._json(429, {"ok": False, "error": "正在生成，等这一份结束。"})
                    return
                BUSY["on"] = True
            try:
                self._json(200, export_job(job_id))
            except FileNotFoundError:
                self._json(404, {"ok": False, "error": "找不到这份稿"})
            except Exception as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            finally:
                BUSY["on"] = False
            return
        if path == "/api/generate":
            with LOCK:
                if BUSY["on"]:
                    self._json(429, {"ok": False, "error": "正在生成，等这一份结束。"})
                    return
                BUSY["on"] = True
            try:
                self._json(200, generate(self._body()))
            except Exception as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            finally:
                BUSY["on"] = False
            return
        if path == "/api/deepseek":
            with LOCK:
                if BUSY["on"]:
                    self._json(429, {"ok": False, "error": "正在生成，等这一份结束。"})
                    return
                BUSY["on"] = True
            payload = self._body()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                def emit(kind: str, data: dict) -> None:
                    body = json.dumps({"event": kind, **data}, ensure_ascii=False)
                    self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
                    self.wfile.flush()

                stream(payload, emit)
            except Exception as exc:
                try:
                    body = json.dumps({"event": "error", "error": str(exc)}, ensure_ascii=False)
                    self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass
            finally:
                BUSY["on"] = False
            return
        self._json(404, {"ok": False, "error": "not found"})


def main() -> None:
    host, port = "127.0.0.1", 8766
    print(f"PPT 选模板：http://{host}:{port}", flush=True)
    print(f"DeepSeek 出片（不用 PPT Master）：http://{host}:{port}/deepseek", flush=True)
    print(f"在线改字：http://{host}:{port}/edit", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
