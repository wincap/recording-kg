#!/usr/bin/env python3
"""Local web console for the Cursor lesson recorder."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
INDEX = HERE / "web" / "index.html"
WATCH = HERE / "web" / "watch.html"
sys.path.insert(0, str(HERE))
from studio import Studio
import course

STUDIO = Studio()
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        msg = fmt % args
        if "/api/status" in msg:
            return
        print(f"[web] {self.address_string()} {msg}", flush=True)

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self) -> None:
        html = INDEX.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/", "/index.html"}:
            self._html()
            return
        if path in {"/watch", "/watch.html"}:
            self._file(WATCH, "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._json(200, STUDIO.status())
            return
        if path == "/api/windows":
            import term_demo as td
            browsers = STUDIO.list_windows()
            browsers["terms"] = td.list_term_windows()
            self._json(200, browsers)
            return
        if path == "/api/courses":
            self._json(200, {"ok": True, "courses": course.list_courses(STUDIO.root)})
            return
        if path == "/api/course":
            slug = (parse_qs(parsed.query).get("slug") or [""])[0]
            data = course.public_course(STUDIO.root, slug)
            if not data:
                self._json(404, {"ok": False, "error": "还没有这门课。"})
                return
            self._json(200, {"ok": True, "course": data})
            return
        if path == "/api/student-notes":
            import student_coach

            slug = (parse_qs(parsed.query).get("slug") or [""])[0]
            notes = student_coach.load_notes(STUDIO.root, slug)
            self._json(
                200,
                {
                    "ok": True,
                    "notes": notes,
                    "summary": student_coach.summarize_notes(notes),
                },
            )
            return
        if path.startswith("/media/courses/"):
            rel = unquote(path[len("/media/courses/") :]).lstrip("/")
            base = course.courses_root(STUDIO.root).resolve()
            target = (base / rel).resolve()
            if not str(target).startswith(str(base)) or not target.is_file():
                self._json(404, {"ok": False, "error": "找不到成片。"})
                return
            suffix = target.suffix.lower()
            ctype = {
                ".mp4": "video/mp4",
                ".mov": "video/quicktime",
                ".m4a": "audio/mp4",
                ".mp3": "audio/mpeg",
                ".json": "application/json; charset=utf-8",
                ".svg": "image/svg+xml",
                ".png": "image/png",
            }.get(suffix, "text/plain; charset=utf-8")
            self._file(target, ctype)
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        data = self._body()
        if path == "/api/reset":
            self._json(200, STUDIO.close_project())
            return
        if path == "/api/project":
            action = str(data.get("action") or "").strip().lower()
            if action in {"new", "create"}:
                self._json(200, STUDIO.new_project())
            elif action in {"open", "load"}:
                self._json(200, STUDIO.open_project(str(data.get("slug") or data.get("project") or "")))
            else:
                self._json(200, STUDIO.close_project())
            return
        if path == "/api/plan":
            self._json(200, STUDIO.plan_lesson(data.get("topic"), data.get("note")))
            return
        if path == "/api/engine":
            self._json(200, STUDIO.set_engine(data.get("engine") or data.get("name")))
            return
        if path == "/api/chat":
            self._json(200, STUDIO.chat_turn(data.get("text")))
            return
        if path == "/api/student-coach":
            import student_coach

            self._json(
                200,
                student_coach.analyze_utterance(
                    STUDIO.root,
                    slug=str(data.get("slug") or ""),
                    text=str(data.get("text") or ""),
                    chapter=int(data.get("chapter") or 1),
                    player=data.get("player") if isinstance(data.get("player"), dict) else {},
                    asset=data.get("asset") if isinstance(data.get("asset"), dict) else {},
                ),
            )
            return
        if path == "/api/revise":
            self._json(200, STUDIO.revise_plan(data.get("patch") if isinstance(data.get("patch"), dict) else data))
            return
        if path == "/api/narrate":
            self._json(200, STUDIO.produce_narrate())
            return
        if path == "/api/hold":
            self._json(200, STUDIO.mark_hold())
            return
        if path == "/api/human-ok":
            self._json(200, STUDIO.human_continue())
            return
        if path == "/api/record-stop":
            self._json(200, STUDIO.record_stop())
            return
        if path == "/api/record-abort":
            self._json(200, STUDIO.record_abort())
            return
        with LOCK:
            if path == "/api/prepare":
                self._json(200, STUDIO.prepare(data.get("folder")))
                return
            if path == "/api/open-window":
                self._json(200, STUDIO.open_window())
                return
            if path == "/api/open-chat":
                self._json(200, STUDIO.open_chat())
                return
            if path == "/api/open-browser":
                self._json(200, STUDIO.open_browser(data.get("url")))
                return
            if path == "/api/record-demo":
                self._json(
                    200,
                    STUDIO.record_demo(
                        data.get("script"),
                        data.get("preset"),
                        data.get("url"),
                        data.get("query"),
                        data.get("click"),
                    ),
                )
                return
            if path == "/api/record-page":
                self._json(200, STUDIO.record_page_tour(data.get("url")))
                return
            if path == "/api/record-search":
                self._json(
                    200,
                    STUDIO.record_web_search(data.get("query"), data.get("home")),
                )
                return
            if path == "/api/record-start":
                self._json(
                    200,
                    STUDIO.record_start(data.get("target"), data.get("window_id"), data.get("url")),
                )
                return
            if path == "/api/send":
                self._json(200, STUDIO.send_prompt(data.get("prompt"), data.get("expect")))
                return
            if path == "/api/run-all":
                self._json(200, STUDIO.run_all(data.get("folder"), data.get("prompt"), data.get("expect")))
                return
            if path == "/api/snapshot":
                self._json(200, STUDIO.snapshot(data.get("name")))
                return
            if path == "/api/restore":
                self._json(200, STUDIO.restore(data.get("name")))
                return
            if path == "/api/record-term":
                self._json(200, STUDIO.record_term(data.get("script")))
                return
            if path == "/api/open-term":
                self._json(200, STUDIO.open_term())
                return
            if path == "/api/pack":
                self._json(200, STUDIO.pack_current(data.get("folder")))
                return
            if path == "/api/assemble":
                self._json(
                    200,
                    STUDIO.assemble_takes(
                        data.get("takes"),
                        data.get("mode"),
                        data.get("left"),
                        data.get("right"),
                    ),
                )
                return
            if path == "/api/rundown":
                self._json(200, STUDIO.record_rundown(data.get("script")))
                return
        self._json(404, {"ok": False, "error": "not found"})


def main() -> None:
    host, port = "127.0.0.1", 8765
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"导播台：http://{host}:{port}", flush=True)
    print("在网页上按步骤控制：建目录 → 开窗口 → 开右侧 Agent → 录制 → 发送 → 停止", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
