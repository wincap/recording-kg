#!/usr/bin/env python3
"""Session wrapper so the web console can run recording steps one at a time."""

from __future__ import annotations

import shutil
import threading
import time
import re
from collections import deque
from pathlib import Path

import assemble as asm
import course
import keyhud
import lesson_tools
import pack_lesson as pack
import plan_lesson
import playground
import record_lesson as rl
import term_demo as td
import voiceover
import web_demo as wd

HOLD_AFTER = 90
RUN_NUDGE = (
    "不要改需求。打开刚才写的文件，用项目里的 .venv/bin/python 跑它，"
    "把完整终端输出发回来。必须出现以「问：」和「答：」开头的两行真实输出。"
    "禁止 pip install，禁止手写假结果。"
)


class Studio:
    def __init__(self) -> None:
        self.root = rl.project_root(None)
        self.folder = self.root / "demo-playground"
        self.prompt = (
            '在当前项目新建 helloworld.py，内容只有一行：print("hello world")\n'
            "然后执行 python3 helloworld.py，把终端输出发回来。"
        )
        self.expect = "hello world"
        self.recorder: rl.Recorder | None = None
        self.output: Path | None = None
        self.record_kind = "cursor"
        self.logs: deque[str] = deque(maxlen=500)
        self.busy = False
        self.done: list[str] = []
        self.tmp = Path("/tmp/cursor-studio")
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.sent_at: float | None = None
        self.watch_until: float | None = None
        self.live_offset = 0
        self.hold_forced = False
        self.hold_reason = ""
        self.finished = False
        self.finish_reason = ""
        self._watch_stop = threading.Event()
        self._watch_thread: threading.Thread | None = None
        self._finish_lock = threading.Lock()
        self.last_events: list[dict] = []
        self.lesson_plan: dict | None = None
        self.workspace = False
        self.brief: dict = dict(plan_lesson.OPENING["brief"])
        self.chat: list[dict] = []
        self.chatting = False
        self.cursor_for_demo = False
        self._abort = threading.Event()
        self._inline_cursor = False
        self.edit_field = ""
        self._producing = False
        self._stop_produce = False
        self._produce_gen = 0

    def _hud_on(self) -> None:
        try:
            self.log(keyhud.start())
        except Exception as exc:
            self.log(f"按键浮层没起来（可继续录）：{exc}")

    def _hud_off(self) -> None:
        try:
            keyhud.stop()
        except Exception:
            pass

    def _pack(self, folder: Path, events: list[dict], meta: dict) -> None:
        self.last_events = events
        payload = {**meta, "plan": self.lesson_plan} if self.lesson_plan else meta
        info = pack.write_pack(folder, events, payload)
        self.log(f"讲义：{info['notes']}")
        self.log(f"作业：{info['exercises']}")
        video = folder / "lesson.mp4"
        try:
            voiced = voiceover.attach(video, self.lesson_plan, self.log)
        except Exception as exc:
            voiced = None
            self.log(f"口播没配上：{exc}")
        if voiced:
            self.log("口播字幕已出。")
        elif info.get("speech_srt"):
            self.log("口播字幕（whisper）已出")
        else:
            self.log("字幕按步骤时间轴出了。画面若没声，是还没配口播。")
        if self.lesson_plan and (self.lesson_plan.get("chapters") or []):
            try:
                self.lesson_plan = course.publish_folder(self.root, self.lesson_plan, folder)
                self.log("已发布到学生端：" + str(self.lesson_plan.get("slug") or ""))
            except Exception as exc:
                self.log(f"学生端还没写上：{exc}")

    def takes(self) -> list[dict]:
        root = self.root / "recordings"
        if not root.is_dir():
            return []
        rows = []
        for mp4 in sorted(root.glob("*/lesson.mp4"), reverse=True):
            rows.append(
                {
                    "id": mp4.parent.name,
                    "path": str(mp4),
                    "notes": (mp4.parent / "notes.md").is_file(),
                    "exercises": (mp4.parent / "exercises.md").is_file(),
                }
            )
        return rows[:24]

    def log(self, message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}  {message}"
        self.logs.append(line)

    def _opening_chat(self) -> list[dict]:
        return [
            {
                "role": "assistant",
                "text": plan_lesson.OPENING["say"],
                "chips": list(plan_lesson.OPENING["chips"]),
                "acts": [],
            }
        ]

    def reset_session(self) -> dict:
        return self.close_project()

    def _clear_work(self) -> None:
        if self.recorder is not None:
            try:
                self.record_stop()
            except Exception:
                self.recorder = None
        self.lesson_plan = None
        self.brief = dict(plan_lesson.OPENING["brief"])
        self.chat = []
        self.logs.clear()
        self.busy = False
        self.chatting = False
        self.finished = False
        self.finish_reason = ""
        self.hold_forced = False
        self.hold_reason = ""
        self.edit_field = ""
        self.done = []
        self.output = None
        self._producing = False
        self._stop_produce = True
        self._produce_gen += 1

    def close_project(self) -> dict:
        self._clear_work()
        self.workspace = False
        return {"ok": True, **self.status()}

    def new_project(self) -> dict:
        self._clear_work()
        self.workspace = True
        self.chat = self._opening_chat()
        self.log("新工程。说这门课讲什么。")
        return {"ok": True, **self.status()}

    def open_project(self, slug: str) -> dict:
        data = course.load_course(self.root, str(slug or "").strip())
        if not data:
            return {"ok": False, "error": "找不到这个工程。", **self.status()}
        self._clear_work()
        self.workspace = True
        plan = course.plan_from_saved(data)
        self.lesson_plan = plan
        self.brief["topic"] = str(plan.get("course_title") or plan.get("title") or "")
        if plan.get("prompt"):
            self.prompt = str(plan.get("prompt"))
        self.expect = lesson_tools.honest_expect(plan, str(plan.get("expect") or self.expect or ""))
        course.remember_slug(self.root, str(plan.get("slug") or ""))
        n = int(plan.get("chapter") or 1)
        folder = course.chapter_dir(self.root, plan, n)
        video = folder / "lesson.mp4"
        if video.is_file():
            self.output = video
        self.chat = [
            {
                "role": "assistant",
                "text": "已打开工程，接着改。\n\n" + self._plan_speech(plan),
                "chips": self._plan_chips(plan) + ["换工程"],
                "acts": self._plan_acts(),
                "plan": plan,
            }
        ]
        self.log(f"打开工程：{plan.get('course_title') or plan.get('slug')}")
        return {"ok": True, **self.status()}

    @staticmethod
    def friendly_error(exc: BaseException) -> str:
        text = str(exc)
        if "same file" in text:
            return "这一章片子已经在了。打开学生端看，或做下一章。"
        if "socket hang up" in text or "[aborted]" in text:
            return "Cursor Agent 这轮断了。再发一次，或先切到 DeepSeek。"
        if "PosixPath" in text or "/Users/" in text:
            return "这一步没跑完。再说一次就行。"
        return text[:120]

    def _actor_state(self) -> Path:
        return self.folder

    def _pull_live(self) -> None:
        if self.sent_at is None:
            return
        lines, self.live_offset = rl.consume_live_log(
            self._actor_state() / rl.LIVE_RELATIVE, self.live_offset
        )
        for line in lines:
            self.logs.append(line)

    def _elapsed(self) -> float | None:
        if self.sent_at is None:
            return None
        return time.time() - self.sent_at

    def _phase(self) -> str:
        if self._inline_cursor:
            if self.hold_forced:
                return "hold"
            return "watching"
        if self.finished:
            return "ready"
        if self.sent_at is None and self.recorder is None:
            return "idle"
        if self.sent_at is not None:
            result = rl.turn_result(self._actor_state(), self.sent_at, self.expect)
            if result:
                kind, detail = result
                if kind in {"output", "stop"}:
                    return "ready"
                if kind == "fail":
                    self.enter_fail_hold(detail)
                    return "hold"
        if self.hold_forced:
            return "hold"
        if self.watch_until is not None and time.time() >= self.watch_until:
            if not self.hold_reason:
                elapsed = self._elapsed() or 0
                self.hold_reason = (
                    f"这一轮已经 {elapsed:.0f} 秒还没成功。去新窗口看是不是报错停住了。"
                    "录屏没停，处理好再点「我处理完了」。"
                )
                self.log("⚠ 需要人介入：超时还没成功，录屏继续。")
                rl.notify_need_human("这一轮超时还没成功，需要你介入")
            return "hold"
        if self.recorder is None and self.sent_at is None:
            return "idle"
        return "watching"

    def status(self) -> dict:
        self._pull_live()
        phase = self._phase()
        elapsed = self._elapsed()
        return {
            "busy": self.busy,
            "folder": str(self.folder),
            "prompt": self.prompt,
            "expect": self.expect,
            "recording": self.recorder is not None,
            "record_kind": self.record_kind,
            "output": str(self.output) if self.output else None,
            "done": list(self.done),
            "logs": [],
            "phase": phase,
            "hold": phase == "hold",
            "hold_reason": self.hold_reason if phase == "hold" else "",
            "elapsed": None if elapsed is None else round(elapsed),
            "hold_after": HOLD_AFTER,
            "finished": self.finished,
            "finish_reason": self.finish_reason,
            "failed": self.hold_forced and "任务失败" in (self.hold_reason or ""),
            "last_output": rl.last_printed_output(self._actor_state()) if self.sent_at else "",
            "snaps": playground.list_snaps(self.folder),
            "takes": self.takes(),
            "plan": self.lesson_plan,
            "chat": self.chat,
            "brief": self.brief,
            "chatting": self.chatting,
            "watch_url": f"/watch?slug={self.lesson_plan.get('slug')}" if self.lesson_plan and self.lesson_plan.get("slug") else "/watch",
            "need_project": not self.workspace,
            "project": (self.lesson_plan or {}).get("slug") or "",
            "project_title": (self.lesson_plan or {}).get("course_title") or (self.lesson_plan or {}).get("title") or "",
            "projects": course.list_courses(self.root),
            "last_slug": course.last_slug(self.root),
            **plan_lesson.engine_status(),
        }

    def _run(self, name: str, fn) -> dict:
        if self.busy:
            return {"ok": False, "error": "上一步还在跑，等它结束再点。"}
        self.busy = True
        try:
            self.log(f"→ {name}")
            fn()
            if name not in self.done:
                self.done.append(name)
            self.log(f"✓ {name}")
            return {"ok": True, **self.status()}
        except SystemExit as exc:
            msg = str(exc) or "步骤失败"
            self.log(f"✗ {name}：{msg}")
            return {"ok": False, "error": msg, **self.status()}
        except Exception as exc:
            self.log(f"✗ {name}：{exc}")
            return {"ok": False, "error": str(exc), **self.status()}
        finally:
            self.busy = False

    def prepare(self, folder: str | None = None) -> dict:
        if folder:
            self.folder = Path(folder).expanduser().resolve()

        def go() -> None:
            rl.require_accessibility()
            self.folder = rl.prepare_actor_project(self.folder)
            playground.ensure_baseline(self.folder)
            self.log("现场快照 baseline 已备好，录坏了可以还原。")

        return self._run("准备目录", go)

    def open_window(self) -> dict:
        return self._run("打开 Cursor 窗口", lambda: rl.open_actor_with_folder(self.folder))

    def open_chat(self) -> dict:
        def go() -> None:
            rl.raise_actor_window()
            rl.ensure_right_chat_panel()
            rl.click_composer()

        return self._run("打开右侧 Agent", go)

    def list_windows(self) -> dict:
        browsers = rl.list_browser_windows()
        return {"ok": True, "windows": browsers, **self.status()}

    def open_browser(self, url: str | None = None) -> dict:
        return self._run("打开浏览器", lambda: rl.open_browser(url))

    def record_start(self, target: str | None = None, window_id=None, url: str | None = None) -> dict:
        kind = str(target or "cursor").strip().lower()
        if kind not in {"cursor", "browser"}:
            kind = "cursor"

        def go() -> None:
            if self.recorder is not None:
                raise RuntimeError("已经在录了，先停止再开始。")
            stamp = time.strftime("%Y-%m-%d-%H%M%S")
            self.record_kind = kind
            self.output = self.root / "recordings" / stamp / "lesson.mp4"
            if kind == "browser":
                if url:
                    rl.open_browser(str(url))
                pick = None
                wid = None
                if window_id not in (None, ""):
                    try:
                        wid = int(window_id)
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError("窗口编号不对。") from exc
                    pick = next((w for w in rl.list_browser_windows() if w["id"] == wid), None)
                if wid is None:
                    pick = rl.pick_browser_window()
                    if pick is None:
                        rl.open_browser(url)
                        pick = rl.pick_browser_window()
                    if pick is None:
                        raise RuntimeError("找不到浏览器窗口。先打开 Chrome / Safari / Arc。")
                    wid = int(pick["id"])
                if pick:
                    rl.raise_process(str(pick["owner"]))
                    time.sleep(0.4)
                label = f"{pick['owner']} · {pick['name']}" if pick else f"id={wid}"
                rec = rl.start_capture_window_id(self.output, wid, label)
                if rec is None:
                    raise RuntimeError("浏览器窗口录制没起来。看一下屏幕录制权限。")
                self.recorder = rec
                self.log(f"在录浏览器：{label}")
            else:
                self.recorder = rl.start_ffmpeg(self.output, mic=False, window_only=True)
            self._hud_on()
            self._reset_turn()

        return self._run("开始录浏览器" if kind == "browser" else "开始录制", go)

    def record_page_tour(self, url: str | None = None) -> dict:
        return self.record_demo(preset="open-scroll", url=url)

    def record_web_search(self, query: str | None = None, home: str | None = None) -> dict:
        return self.record_demo(preset="baidu", query=query, url=home)

    def record_demo(
        self,
        script: str | None = None,
        preset: str | None = None,
        url: str | None = None,
        query: str | None = None,
        click: str | None = None,
    ) -> dict:
        def go() -> None:
            text = str(script or "").strip()
            if not text:
                if preset:
                    text = wd.fill_preset(str(preset), str(url or ""), str(query or ""), str(click or ""))
                elif query:
                    text = wd.fill_preset("baidu", str(url or ""), str(query), str(click or ""))
                elif url:
                    text = wd.fill_preset("open-scroll", str(url), str(query or ""), str(click or ""))
                else:
                    raise RuntimeError("写一下演示步骤，或填网址 / 搜索词。")
            if self.recorder is not None:
                rl.stop_ffmpeg(self.recorder)
                self.recorder = None
            stamp = time.strftime("%Y-%m-%d-%H%M%S")
            self.record_kind = "browser"
            self.output = self.root / "recordings" / stamp / "lesson.mp4"
            self._reset_turn()
            steps = wd.parse_script(text)
            rest = steps
            events: list[dict] = []
            t0 = time.time()
            self._hud_on()
            try:
                if steps and steps[0]["cmd"] == "open":
                    wd.run_step(steps[0], self.log)
                    events.append({"t": 0, **steps[0], "say": steps[0].get("raw")})
                    rest = steps[1:]
                rec, label = wd.begin_capture(self.output)
                self.recorder = rec
                self.log(f"开始录：{label}")
                time.sleep(1.0)
                wd.inject_overlay()
                events.extend(wd.run_steps_timed(rest, self.log, t0))
                time.sleep(1.0)
            finally:
                self._hud_off()
                if self.recorder is not None:
                    rl.stop_ffmpeg(self.recorder)
                    rec2 = self.recorder
                    self.recorder = None
                    path = rec2.mp4_path or rec2.mov_path or self.output
                    self.output = Path(path)
                    self.log(f"录屏已保存：{path}")
            self.finished = True
            self.finish_reason = "演示结束自动停录"
            if self.output is not None:
                self._pack(
                    self.output.parent,
                    events,
                    {"kind": "web", "title": "网页课程演示", "script": text},
                )

        return self._run("网页演示", go)

    def _reset_turn(self) -> None:
        self.sent_at = None
        self.watch_until = None
        self.live_offset = 0
        self.hold_forced = False
        self.hold_reason = ""
        self.finished = False
        self.finish_reason = ""
        self._watch_stop.set()

    def _start_watch(self) -> None:
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._watch_thread.start()

    def _watch_loop(self) -> None:
        while not self._watch_stop.wait(0.4):
            if self.finished or self.sent_at is None:
                return
            if self.hold_forced:
                continue
            result = rl.turn_result(self._actor_state(), self.sent_at, self.expect)
            if result:
                kind, detail = result
                if kind == "fail":
                    self.enter_fail_hold(detail)
                    continue
                self.finish_case(kind)
                return
            if self.watch_until is not None and time.time() >= self.watch_until:
                continue

    def enter_fail_hold(self, detail: str) -> None:
        if self.hold_forced and "任务失败" in (self.hold_reason or ""):
            return
        self.hold_forced = True
        self.hold_reason = f"任务失败：{detail} 去新窗口处理。录屏没停，窗口还开着。"
        self.log(f"✗ {self.hold_reason}")
        rl.notify_need_human(detail)

    def finish_case(self, reason: str) -> dict:
        with self._finish_lock:
            if self.finished:
                return {"ok": True, **self.status()}
            self.finished = True
            self._watch_stop.set()
            label = "看到完成标志" if reason == "output" else "这一轮任务停了"
            self.finish_reason = label
            if self.record_kind == "browser":
                self.log(f"{label}，这个 case 完了。浏览器窗口留着。")
            else:
                self.log(f"{label}，这个 case 完了，关掉新窗口。")
                closed = rl.close_actor_window(self.folder)
                if closed:
                    if "关闭新窗口" not in self.done:
                        self.done.append("关闭新窗口")
                else:
                    self.log("✗ 关窗失败，新窗口可能还在。")
            if self.recorder is not None:
                time.sleep(1.2)
                self._hud_off()
                rl.stop_ffmpeg(self.recorder)
                rec = self.recorder
                self.recorder = None
                path = rec.mp4_path or rec.mov_path or self.output
                self.log(f"录屏已保存：{path}")
                if path is not None:
                    self.output = Path(path)
                    kind = "cursor-demo" if self.cursor_for_demo else "cursor"
                    self._pack(
                        Path(path).parent,
                        [{"t": 0, "cmd": "prompt", "arg": self.prompt, "raw": self.prompt, "say": label}],
                        {"kind": kind, "title": "Agent 写演示" if self.cursor_for_demo else "Agent 录课", "prompt": self.prompt, "expect": self.expect},
                    )
            if self.cursor_for_demo:
                self.cursor_for_demo = False
                if self.lesson_plan:
                    self.lesson_plan = lesson_tools.apply_choice(self.lesson_plan, "accept_demo")
                    self.lesson_plan["gate"] = "review_demo"
                    self.lesson_plan["code_via"] = "cursor"
                    self.lesson_plan["confirmed"] = False
                self.chat.append(
                    {
                        "role": "assistant",
                        "text": "Cursor 写完了。你看 playground 里的文件对不对。对就确认这样录；跑偏了就改用本地这份，或作废这段。",
                        "chips": ["改用本地这份", "不要代码演示"],
                        "acts": self._plan_acts(),
                        "plan": self.lesson_plan,
                    }
                )
            self.sent_at = None
            self.watch_until = None
            return {"ok": True, **self.status()}

    def send_prompt(self, prompt: str | None = None, expect: str | None = None) -> dict:
        if prompt is not None:
            self.prompt = prompt.strip() or self.prompt
        if expect is not None:
            self.expect = expect.strip()

        def go() -> None:
            if not self.prompt.strip():
                raise RuntimeError("提示词是空的。")
            rl.raise_actor_window()
            rl.ensure_right_chat_panel()
            rl.click_composer()
            rl.clear_idle(self._actor_state())
            self.sent_at = time.time()
            self.watch_until = self.sent_at + HOLD_AFTER
            self.live_offset = 0
            self.hold_forced = False
            self.hold_reason = ""
            self.finished = False
            self.finish_reason = ""
            rl.type_and_send(self.prompt, 0.04, self.tmp)
            marker = f"完成标志：{self.expect}" if self.expect else "没有完成标志，等任务成功停下来"
            self.log(f"提示词已发送。{marker}。失败会叫你介入，不会关窗。")
            self._start_watch()

        return self._run("发送提示词", go)

    def mark_hold(self) -> dict:
        if self.sent_at is None and self.recorder is None:
            return {"ok": False, "error": "现在没有在跑任务。", **self.status()}
        if self.sent_at is None:
            return {"ok": False, "error": "还没发提示词，谈不上卡住。", **self.status()}
        self.hold_forced = True
        self.hold_reason = "你标记这一轮卡住了。去新窗口处理，录屏没停。"
        self.log("⚠ 人标记卡住。录屏继续，等你在新窗口处理完。")
        return {"ok": True, **self.status()}

    def human_continue(self) -> dict:
        if self.finished:
            return {"ok": False, "error": "这个 case 已经收过尾了。", **self.status()}
        if self.sent_at is None:
            return {"ok": False, "error": "现在没有在等任务。", **self.status()}
        self.hold_forced = False
        self.hold_reason = ""
        rl.reset_idle(self._actor_state())
        self.watch_until = time.time() + HOLD_AFTER
        self.log("人已处理完，继续等这一轮成功。录屏没停，窗口还开着。")
        return {"ok": True, **self.status()}

    def record_stop(self) -> dict:
        self._abort.set()
        self._watch_stop.set()
        if self.busy:
            self.log("收到停止。当前这一段会收尾。")
            return {"ok": True, **self.status()}

        def go() -> None:
            self._watch_stop.set()
            if not self.finished:
                if self.record_kind != "browser":
                    rl.close_actor_window(self.folder)
                self.finished = True
                self.finish_reason = "手动停录"
            if self.recorder is None:
                return
            self._hud_off()
            time.sleep(1.2)
            rl.stop_ffmpeg(self.recorder)
            rec = self.recorder
            self.recorder = None
            path = rec.mp4_path or rec.mov_path or self.output
            self.sent_at = None
            self.watch_until = None
            self.log(f"录屏已保存：{path}")

        return self._run("停止录制", go)

    def record_abort(self) -> dict:
        self._abort.set()
        self._watch_stop.set()
        if self.busy:
            self.log("这段作废请求已记下。")
            return {"ok": True, **self.status()}

        def go() -> None:
            self._watch_stop.set()
            rec = self.recorder
            if rec is not None:
                self._hud_off()
                rl.stop_ffmpeg(rec, remux=False)
            self.recorder = None
            if self.record_kind != "browser":
                rl.close_actor_window(self.folder)
            if rec is not None:
                for path in (rec.mov_path, rec.mp4_path, rec.log_path, self.output):
                    if path is not None and path.exists():
                        path.unlink()
                parent = self.output or rec.mp4_path or rec.mov_path
                if parent is not None:
                    folder = parent.parent
                    if folder.exists() and folder.name != "recordings":
                        shutil.rmtree(folder, ignore_errors=True)
            self.output = None
            self._reset_turn()
            self.record_kind = "cursor"
            self.log("这段作废了，文件已删。")

        return self._run("作废重来", go)

    def run_all(self, folder: str | None, prompt: str | None, expect: str | None = None) -> dict:
        if folder:
            self.folder = Path(folder).expanduser().resolve()
        if prompt is not None:
            self.prompt = prompt.strip() or self.prompt
        if expect is not None:
            self.expect = expect.strip()
        steps = [
            self.prepare,
            self.open_window,
            self.open_chat,
            self.record_start,
            self.send_prompt,
        ]
        last = {"ok": True}
        for step in steps:
            last = step()
            if not last.get("ok"):
                return last
        self.log("已发送。看到完成标志或 Agent 停了，会关新窗口并停录。")
        return {"ok": True, "hint": "任务完了会自动关窗口。", **self.status()}

    def snapshot(self, name: str | None = None) -> dict:
        label = (name or "baseline").strip() or "baseline"

        def go() -> None:
            path = playground.snapshot(self.folder, label)
            self.log(f"已存快照「{label}」→ {path}")

        return self._run("存快照", go)

    def open_term(self) -> dict:
        return self._run("打开终端", lambda: td.open_terminal(str(self.folder)))

    def restore(self, name: str | None = None) -> dict:
        label = (name or "baseline").strip() or "baseline"

        def go() -> None:
            path = playground.restore(self.folder, label)
            self.log(f"已还原到「{label}」← {path}")

        return self._run("还原现场", go)

    def record_term(self, script: str | None = None) -> dict:
        def go() -> None:
            text = str(script or "").strip()
            if not text:
                raise RuntimeError("写一下终端步骤，例如：run python3 helloworld.py")
            if self.recorder is not None:
                rl.stop_ffmpeg(self.recorder)
                self.recorder = None
            stamp = time.strftime("%Y-%m-%d-%H%M%S")
            self.record_kind = "term"
            self.output = self.root / "recordings" / stamp / "lesson.mp4"
            self._reset_turn()
            self._hud_on()
            try:
                path, events = td.record_script(text, self.output, str(self.folder), self.log)
                self.output = path
            finally:
                self._hud_off()
                self.recorder = None
            self.finished = True
            self.finish_reason = "终端演示结束自动停录"
            self._pack(self.output.parent, events, {"kind": "term", "title": "终端演示", "script": text})

        return self._run("终端演示", go)

    def pack_current(self, folder: str | None = None) -> dict:
        def go() -> None:
            target = Path(folder).expanduser() if folder else (self.output.parent if self.output else None)
            if target is None:
                takes = self.takes()
                if not takes:
                    raise RuntimeError("还没有成片。先录一段。")
                target = Path(takes[0]["path"]).parent
            timeline = target / "timeline.json"
            events = self.last_events
            if timeline.is_file():
                import json
                events = json.loads(timeline.read_text(encoding="utf-8")).get("events") or events
            meta = {"kind": self.record_kind, "title": target.name, "prompt": self.prompt, "expect": self.expect}
            self._pack(target, events, meta)

        return self._run("出讲义作业", go)

    def assemble_takes(self, takes: list | None = None, mode: str | None = None, left: str | None = None, right: str | None = None) -> dict:
        def go() -> None:
            kind = str(mode or "concat").strip().lower()
            stamp = time.strftime("%Y-%m-%d-%H%M%S")
            outdir = self.root / "recordings" / f"{stamp}-cut"
            outdir.mkdir(parents=True, exist_ok=True)
            output = outdir / "lesson.mp4"
            if kind == "split":
                a = Path(left or "")
                b = Path(right or "")
                if not a.is_file() or not b.is_file():
                    raise RuntimeError("对比镜要两段成片路径。")
                asm.split_screen(a, b, output)
                self.log("左右对比成片已出。")
            elif kind == "silence":
                rows = [Path(p) for p in (takes or []) if p]
                if not rows:
                    raise RuntimeError("选一段成片再去静音。")
                asm.silence_trim(rows[0], output)
                self.log("去静音（没音轨就原样拷贝）。")
            else:
                rows = [Path(p) for p in (takes or []) if p]
                if len(rows) < 1:
                    raise RuntimeError("先勾选要拼接的片段。")
                parts: list[Path] = []
                for i, src in enumerate(rows, 1):
                    if not src.is_file():
                        raise RuntimeError(f"找不到片段：{src}")
                    card = outdir / f"card-{i:02d}.mp4"
                    asm.title_card(src.parent.name, card, seconds=1.8)
                    parts.append(card)
                    parts.append(src)
                asm.concat(parts, output)
                self.log("多 take 已按顺序拼接，每段前有章节卡。")
            self.output = output
            events = [{"t": 0, "cmd": "take", "raw": kind, "say": "成片剪辑"}]
            self._pack(outdir, events, {"kind": "cut", "title": "剪辑成片"})

        return self._run("剪辑成片", go)

    def _capture_cursor_take(self, output: Path, batch: bool = False, window_only: bool = True) -> tuple[Path, list[dict]]:
        plan = self.lesson_plan or {}
        prompt = str(plan.get("prompt") or "").strip()
        expect = lesson_tools.honest_expect(plan, str(plan.get("expect") or ""))
        if not prompt:
            prompt, expect = lesson_tools.chapter_cursor_prompt(plan)
        self.prompt = prompt
        self.expect = expect
        self.record_kind = "cursor"
        self._inline_cursor = True
        self.hold_forced = False
        self.hold_reason = ""
        self.finished = False
        self.finish_reason = ""
        output.parent.mkdir(parents=True, exist_ok=True)
        events = [{"t": 0, "cmd": "cursor", "arg": expect, "raw": prompt, "say": "Cursor 写演示"}]
        path: Path | None = None
        try:
            rl.require_accessibility()
            self.folder = rl.prepare_actor_project(self.folder)
            playground.ensure_baseline(self.folder)
            playground.ensure_packages(self.folder, lesson_tools.blob_of(plan), self.log)
            rl.open_actor_with_folder(self.folder)
            rl.raise_actor_window()
            rl.ensure_right_chat_panel()
            rl.click_composer()
            self.recorder = rl.start_ffmpeg(output, mic=False, window_only=window_only)
            self._hud_on()
            self.log("开始录 Cursor 写代码并跑出真实结果。不要装包；跑偏了点停止。")
            time.sleep(0.8)
            rl.clear_idle(self._actor_state())
            self.sent_at = time.time()
            self.watch_until = self.sent_at + HOLD_AFTER
            self.live_offset = 0
            rl.type_and_send(self.prompt, 0.04, self.tmp)
            self.log(f"提示词已发送。完成标志：{self.expect}")
            nudged = False
            while True:
                if self._abort.is_set():
                    raise RuntimeError("你停了 Cursor 这一段")
                if self.hold_forced:
                    time.sleep(0.4)
                    continue
                result = rl.turn_result(self._actor_state(), self.sent_at, self.expect)
                if result:
                    kind, detail = result
                    if kind == "fail":
                        if batch:
                            raise RuntimeError(detail or "Cursor 这一段没跑通。")
                        self.enter_fail_hold(detail)
                        continue
                    if kind == "output":
                        self.log("终端里已经有真实输出。")
                        break
                    if kind == "stop" and not nudged:
                        nudged = True
                        self.log("写完了但还没跑出结果，再让它执行一遍。")
                        rl.reset_idle(self._actor_state())
                        rl.click_composer()
                        self.sent_at = time.time()
                        self.watch_until = self.sent_at + HOLD_AFTER
                        rl.type_and_send(RUN_NUDGE, 0.04, self.tmp)
                        continue
                    if batch:
                        raise RuntimeError("Cursor 写了代码，但终端里没有「问：」「答：」真实输出。")
                    self.log("Cursor 这一轮停了。")
                    break
                time.sleep(0.4)
        finally:
            self._inline_cursor = False
            time.sleep(1.0)
            self._hud_off()
            rec = self.recorder
            if rec is not None:
                rl.stop_ffmpeg(rec)
                self.recorder = None
                path = rec.mp4_path or rec.mov_path or output
            try:
                rl.close_actor_window(self.folder)
            except Exception:
                pass
            self.sent_at = None
            self.watch_until = None
        if path is None or not Path(path).is_file():
            mp4 = output.with_suffix(".mp4")
            mov = output.with_suffix(".mov")
            if mp4.is_file():
                path = mp4
            elif mov.is_file():
                path = mov
        if path is not None and Path(path).suffix.lower() == ".mov" and Path(path).is_file():
            mp4 = output.with_suffix(".mp4")
            if not mp4.is_file() or mp4.stat().st_size < 1000:
                try:
                    asm.run_ffmpeg(["-i", str(path), "-c", "copy", str(mp4)])
                    path = mp4
                except Exception:
                    pass
        if path is None or not Path(path).is_file() or Path(path).stat().st_size < 1000:
            raise RuntimeError("Cursor 窗口录屏没写出文件，实操没有进成片。")
        return Path(path), events

    def record_rundown(self, script: str | None = None) -> dict:
        def go() -> None:
            plan = self.lesson_plan or {}
            self._abort.clear()
            tools = plan.get("tools") or {}
            if tools.get("code_via") == "local":
                self.write_demo_files(plan, extra=self.folder)
            text = str(script or "").strip() or str(plan.get("rundown") or "")
            if plan and not plan.get("rundown_custom"):
                text = lesson_tools.fit_rundown({**plan, "rundown": text})
            text = plan_lesson.lighten_rundown(text)
            if not text:
                raise RuntimeError("分镜是空的。")
            segments = asm.parse_rundown(text)
            stamp = time.strftime("%Y-%m-%d-%H%M%S")
            outdir = self.root / "recordings" / stamp
            outdir.mkdir(parents=True, exist_ok=True)
            if tools.get("code_via") == "local":
                self.write_demo_files(plan, extra=outdir)
            parts: list[Path] = []
            events: list[dict] = []
            t0 = time.time()
            self._hud_on()
            try:
                for i, seg in enumerate(segments):
                    if self._abort.is_set():
                        self.log("分镜被你停了。")
                        break
                    kind = seg["kind"]
                    arg = seg.get("arg") or ""
                    lines = "\n".join(seg.get("lines") or [])
                    if kind == "card":
                        seconds, title = lesson_tools.parse_card_arg(arg)
                        card = outdir / f"card-{i:02d}.mp4"
                        asm.title_card(title, card, seconds=seconds)
                        parts.append(card)
                        events.append({"t": round(time.time() - t0, 2), "cmd": "card", "arg": title, "raw": arg, "say": title})
                    elif kind == "web":
                        part = outdir / f"part-{i:02d}.mp4"
                        path, ev = wd.record_script(lines, part, self.log)
                        parts.append(path)
                        events.extend(ev)
                    elif kind == "term":
                        part = outdir / f"part-{i:02d}.mp4"
                        try:
                            path, ev = td.record_script(lines, part, str(self.folder), self.log)
                        except RuntimeError as exc:
                            self.log(f"终端这段跳过：{exc}")
                            continue
                        parts.append(path)
                        events.extend(ev)
                    elif kind == "cursor":
                        part = outdir / f"part-{i:02d}.mp4"
                        self._hud_off()
                        try:
                            path, ev = self._capture_cursor_take(part)
                        except RuntimeError as exc:
                            self.log(f"Cursor 这段：{exc}")
                            if part.is_file():
                                parts.append(part)
                            if self._abort.is_set():
                                break
                            continue
                        parts.append(path)
                        events.extend(ev)
                        self._hud_on()
                    elif kind == "take":
                        src = Path(arg).expanduser()
                        if not src.is_file():
                            raise RuntimeError(f"找不到片段：{arg}")
                        parts.append(src)
                    elif kind == "split":
                        bits = arg.split()
                        if len(bits) < 2:
                            raise RuntimeError("对比 后面要跟两段成片路径")
                        out = outdir / f"split-{i:02d}.mp4"
                        asm.split_screen(Path(bits[0]), Path(bits[1]), out)
                        parts.append(out)
            finally:
                self._hud_off()
            if not parts:
                raise RuntimeError("这一轮一段也没录上。")
            output = outdir / "lesson.mp4"
            asm.concat(parts, output)
            self.output = output
            self.record_kind = "rundown"
            self.finished = True
            self.finish_reason = "分镜跑完自动停录"
            self._pack(outdir, events, {"kind": "rundown", "title": "分镜课", "prompt": self.prompt, "expect": self.expect})
            self.log(f"分镜成片：{output}")

        return self._run("分镜课", go)

    def write_demo_files(self, plan: dict | None, extra: Path | None = None) -> None:
        folders = [self.folder]
        if extra is not None and extra != self.folder:
            folders.append(extra)
        for item in (plan or {}).get("demo_files") or []:
            rel = str(item.get("path") or "").strip()
            content = str(item.get("content") or "")
            if not rel or not content:
                continue
            for folder in folders:
                dest = (folder / rel).resolve()
                if not str(dest).startswith(str(folder.resolve())):
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
                self.log(f"演示文件：{dest}")

    def revise_plan(self, patch: dict | None = None) -> dict:
        if not self.lesson_plan:
            return {"ok": False, "error": "还没有大纲，先说这一集讲什么。", **self.status()}
        plan = lesson_tools.merge_plan(self.lesson_plan, patch or {})
        self.apply_plan(plan)
        self.log("大纲已按你改的更新。")
        self.chat.append(
            {
                "role": "assistant",
                "text": "按你改的更新了。行了点当前步骤的确认。",
                "chips": self._plan_chips(),
                "acts": self._plan_acts(),
                "plan": plan,
            }
        )
        return {"ok": True, **self.status()}

    def apply_plan(self, plan: dict) -> None:
        self.lesson_plan = plan
        if plan.get("prompt"):
            self.prompt = plan["prompt"]
        self.expect = lesson_tools.honest_expect(plan, str(plan.get("expect") or self.expect or ""))
        beats = " / ".join(b["beat"] for b in plan.get("outline") or [])
        self.log(f"备课：{plan['title']}")
        self.log(f"听众：{plan.get('audience') or '（未写）'}")
        self.log(f"目标：{plan.get('objective') or '（未写）'}")
        tools = plan.get("tools") or {}
        used = "、".join(x.get("label") or "" for x in (tools.get("use") or []) if x.get("label"))
        if used:
            self.log(f"工具：{used}（先确认再录）")
        if beats:
            self.log(f"大纲：{beats}")
        chapters = plan.get("chapters") or []
        if chapters:
            self.log(
                f"课程：{plan.get('course_title') or plan.get('title')} · 共 {len(chapters)} 章 · "
                f"{lesson_tools.STAGE_LABEL.get(lesson_tools.pipeline_of(plan), '')} · 本集第 {plan.get('chapter') or 1} 章"
            )
        try:
            course.save_course(self.root, plan)
        except Exception:
            pass

    def _plan_chips(self, plan: dict | None = None) -> list[str]:
        return lesson_tools.pipeline_chips(plan or self.lesson_plan or {})

    def _switch_chapter(self, n: int, note: str = "") -> dict:
        plan = self.lesson_plan or {}
        stage = lesson_tools.pipeline_of(plan)
        if stage in {"outline", "materials"}:
            plan = dict(plan)
            plan["chapter"] = n
            ch = lesson_tools.current_chapter(plan)
            plan["title"] = ch.get("title") or plan.get("title")
            plan["objective"] = ch.get("goal") or plan.get("objective")
            self.apply_plan(plan)
            self.chat.append(
                {
                    "role": "assistant",
                    "text": self._plan_speech(plan),
                    "chips": self._plan_chips(plan),
                    "acts": self._plan_acts(),
                    "plan": plan,
                }
            )
            return {"ok": True, "action": "", **self.status()}
        return self._load_chapter(n, note)

    def _plan_speech(self, plan: dict) -> str:
        stage = lesson_tools.pipeline_of(plan)
        course = lesson_tools.course_speech(plan)
        if stage == "outline":
            return course
        lines = [course] if course else []
        cur = lesson_tools.current_chapter(plan)
        if stage == "materials":
            kind = lesson_tools.chapter_kind(cur)
            script = lesson_tools.as_script(cur.get("script") or plan.get("narration"))
            if script:
                lines.append("口播稿：")
                lines.extend(f"- {ln}" for ln in script[:8])
            slides = lesson_tools.as_slides(cur.get("slides") or plan.get("slides"))
            if slides:
                lines.append("PPT 页：")
                for page in slides[:6]:
                    bits = "；".join(page.get("bullets") or [])
                    lines.append(f"- {page.get('title')}" + (f"：{bits}" if bits else ""))
            if kind == "record":
                tool = lesson_tools.tool_label(cur.get("tool") or lesson_tools.chapter_tool(cur))
                lines.append(f"这一章先 PPT 讲概念，再衔接口播接到 {tool} 实操。")
            return "\n".join(lines)
        beats = plan.get("outline") or []
        lines += [
            f"这一章标题：「{plan.get('title') or cur.get('title') or '本集'}」。",
            f"听众：{plan.get('audience') or '先按会一点命令行的人来。'}",
            f"看完：{plan.get('objective') or cur.get('goal') or '能说出这一章在讲什么。'}",
        ]
        if beats:
            lines.append("这一章节拍：")
            for i, item in enumerate(beats[:6], 1):
                extra = f"（{item.get('seconds')}秒）" if item.get("seconds") else ""
                lines.append(f"{i}. {item.get('beat') or ''}{extra}")
        if lesson_tools.chapter_kind(cur) == "record":
            lines.append("出片顺序：PPT 口播 → 接到实操 → Cursor 写并跑通 → 对照 PPT 收束。")
            lines.append(lesson_tools.tool_speech(plan))
        return "\n".join(lines)

    def _emit_course(self, topic: str, lead: str = "") -> dict:
        topic = plan_lesson.resolve_topic(topic, self.brief)
        self.brief["topic"] = topic
        self.log(f"正在拆大纲：{topic}")
        plan = plan_lesson.outline_plan(topic, plan_lesson.brief_note(self.brief))
        self.apply_plan(plan)
        speech = self._plan_speech(plan)
        prefix = str(lead or "").strip()
        if prefix and prefix not in speech:
            speech = prefix + "\n\n" + speech
        self.chat.append(
            {
                "role": "assistant",
                "text": speech,
                "chips": self._plan_chips(),
                "acts": self._plan_acts(),
                "plan": plan,
            }
        )
        if len(self.chat) > 40:
            keep_head = self.chat[:1]
            self.chat = keep_head + self.chat[-36:]
        return {"ok": True, "action": "", **self.status()}

    def _plan_acts(self) -> list[dict]:
        plan = self.lesson_plan or {}
        stage = lesson_tools.pipeline_of(plan)
        if stage == "outline":
            return [{"label": "确认大纲", "act": "confirm_outline"}]
        if stage == "materials":
            return [{"label": "开始出片", "act": "confirm_materials"}]
        return [{"label": "打开学生端", "act": "watch"}]

    def produce_narrate(self, batch: bool = False) -> dict:
        plan = dict(self.lesson_plan or {})
        if not plan:
            return {"ok": False, "error": "还没有课纲。", **self.status()}
        ch = lesson_tools.current_chapter(plan)
        lines = lesson_tools.as_script(ch.get("script") or plan.get("narration"))
        if not lines:
            packed = plan_lesson.fill_chapter_scripts(plan)
            plan["chapters"] = packed.get("chapters") or plan.get("chapters")
            self.apply_plan(plan)
            ch = lesson_tools.current_chapter(plan)
            lines = lesson_tools.as_script(ch.get("script") or plan.get("narration"))
        if not lines:
            return {"ok": False, "error": "这一章还没有解说词。先改卡片里的口播，或再说细一点。", **self.status()}
        n = int(ch.get("n") or plan.get("chapter") or 1)
        folder = course.chapter_dir(self.root, plan, n)
        clip = voiceover.write_talk_assets(
            folder, "speech", str(ch.get("title") or plan.get("title") or "本集"), lines, self.log
        )
        dest = course.write_play_timeline(folder, [clip])
        plan["narration"] = lines
        plan = course.publish_folder(self.root, plan, folder, n)
        plan["confirmed"] = True
        plan["gate"] = "ready"
        self.apply_plan(plan)
        if batch:
            return {"ok": True, "dest": str(dest), "n": n, **self.status()}
        slug = plan.get("slug") or ""
        return self._after_chapter(plan, dest, f"第 {n} 章解说已配音出片。学生端可以看了。", slug)

    def _after_chapter(self, plan: dict, dest: Path, say: str, slug: str) -> dict:
        self.output = dest
        self.finished = False
        nxt = lesson_tools.next_todo_chapter(plan)
        if nxt:
            return self._kick_produce_all(say + " 余下的章接着做。")
        self.finished = True
        self.finish_reason = "各章已出片"
        self.chat.append(
            {
                "role": "assistant",
                "text": say + " 各章都齐了。打开学生端按顺序看。",
                "chips": ["打开学生端"],
                "acts": [{"label": "打开学生端", "act": "watch"}],
                "plan": plan,
            }
        )
        return {"ok": True, "action": "watch", "watch_url": f"/watch?slug={slug}", **self.status()}

    def _mark_chapter(self, n: int, status: str) -> None:
        plan = dict(self.lesson_plan or {})
        chapters = []
        for ch in plan.get("chapters") or []:
            row = dict(ch)
            if int(row.get("n") or 0) == int(n):
                row["status"] = status
            chapters.append(row)
        plan["chapters"] = chapters
        self.apply_plan(plan)

    def _kick_produce_all(self, lead: str = "") -> dict:
        if self._producing:
            return {"ok": True, "error": "", **self.status()}
        text = (lead or "大纲定了，开始按章出片，不用再点下一章。").strip()
        self.chat.append(
            {
                "role": "assistant",
                "text": text,
                "chips": [],
                "acts": [],
                "plan": self.lesson_plan,
            }
        )
        self._stop_produce = False
        self._producing = True
        self.busy = True
        self._produce_gen += 1
        gen = self._produce_gen
        threading.Thread(target=self._produce_all_sync, args=(gen,), daemon=True).start()
        return {"ok": True, **self.status()}

    def _produce_all_sync(self, gen: int = 0) -> None:
        try:
            plan = dict(self.lesson_plan or {})
            if not plan:
                return
            if lesson_tools.pipeline_of(plan) != "produce":
                self.log("正在写各章口播和幻灯片…")
                plan = plan_lesson.prepare_materials(plan)
                plan["pipeline"] = "produce"
                self.apply_plan(plan)
                self.chat.append(
                    {
                        "role": "assistant",
                        "text": "素材齐了，开始按章出片。",
                        "chips": [],
                        "acts": [],
                        "plan": plan,
                    }
                )
            plan = dict(self.lesson_plan or {})
            rows = [int(c.get("n") or 0) for c in (plan.get("chapters") or []) if int(c.get("n") or 0)]
            for nxt in rows:
                if self._stop_produce or gen != self._produce_gen:
                    break
                plan = dict(self.lesson_plan or {})
                ch_row = next((c for c in (plan.get("chapters") or []) if int(c.get("n") or 0) == nxt), {})
                if ch_row.get("status") == "skip" and lesson_tools.chapter_kind(ch_row) != "record":
                    continue
                plan["chapter"] = nxt
                self.apply_plan(plan)
                kind = lesson_tools.chapter_kind(lesson_tools.current_chapter(plan))
                label = lesson_tools.kind_label(kind)
                folder = course.chapter_dir(self.root, plan, nxt)
                try:
                    if lesson_tools.chapter_media_ready(folder, kind):
                        self.apply_plan(course.publish_folder(self.root, plan, folder, nxt))
                        continue
                    self.log(f"正在出第 {nxt} 章（{label}）…")
                    if kind == "narrate":
                        result = self.produce_narrate(batch=True)
                    elif kind == "record":
                        result = self.produce_record(batch=True)
                    else:
                        result = self.produce_ppt(batch=True)
                    if not result.get("ok"):
                        raise RuntimeError(result.get("error") or "出片失败")
                    self.chat.append(
                        {
                            "role": "assistant",
                            "text": f"第 {nxt} 章好了。",
                            "chips": [],
                            "acts": [],
                            "plan": self.lesson_plan,
                        }
                    )
                except Exception as exc:
                    err = self.friendly_error(exc)
                    if kind == "record":
                        self._mark_chapter(nxt, "todo")
                        self.chat.append(
                            {
                                "role": "assistant",
                                "text": f"第 {nxt} 章 Cursor 实操没录上：{err} 停在这里，不会跳过。",
                                "chips": ["把剩下的章做完"],
                                "acts": [],
                                "plan": self.lesson_plan,
                            }
                        )
                        self.log(f"第 {nxt} 章实操失败，整门课停住。")
                        break
                    self._mark_chapter(nxt, "skip")
                    self.chat.append(
                        {
                            "role": "assistant",
                            "text": f"第 {nxt} 章先跳过：{err}",
                            "chips": [],
                            "acts": [],
                            "plan": self.lesson_plan,
                        }
                    )
            if self._stop_produce or gen != self._produce_gen:
                return
            slug = str((self.lesson_plan or {}).get("slug") or "")
            self.finished = True
            self.finish_reason = "各章已出片"
            self.chat.append(
                {
                    "role": "assistant",
                    "text": "各章都出完了。打开学生端按顺序看。",
                    "chips": ["打开学生端"],
                    "acts": [{"label": "打开学生端", "act": "watch"}],
                    "plan": self.lesson_plan,
                }
            )
            self.output = course.chapter_dir(self.root, self.lesson_plan or {}, 1) / "lesson.mp4"
        except Exception as exc:
            self.chat.append(
                {
                    "role": "assistant",
                    "text": self.friendly_error(exc) + " 再说一次确认大纲，或点重新开始。",
                    "chips": ["重新开始", "确认大纲"],
                    "acts": [],
                    "plan": self.lesson_plan,
                }
            )
        finally:
            if gen == self._produce_gen:
                self._producing = False
                self.busy = False

    def _prepare_materials(self) -> dict:
        plan = dict(self.lesson_plan or {})
        if not plan:
            return {"ok": False, "error": "还没有大纲。", **self.status()}
        return self._kick_produce_all("大纲定了，开始写素材并按章出片，不用再点下一章。")

    def _enter_produce(self) -> dict:
        plan = dict(self.lesson_plan or {})
        plan["pipeline"] = "produce"
        self.apply_plan(plan)
        return self._kick_produce_all("开始按章出片，做完我叫你。")

    def _load_chapter(self, n: int, lead: str = "") -> dict:
        plan = dict(self.lesson_plan or {})
        chapters = plan.get("chapters") or []
        if n < 1 or n > len(chapters):
            self.chat.append(
                {
                    "role": "assistant",
                    "text": f"这门课一共 {len(chapters)} 章，没有第 {n} 章。",
                    "chips": self._plan_chips(plan),
                    "acts": self._plan_acts(),
                    "plan": plan,
                }
            )
            return {"ok": True, "action": "", **self.status()}
        plan["chapter"] = n
        stage = lesson_tools.pipeline_of(plan)
        topic = str(self.brief.get("topic") or plan.get("course_title") or plan.get("title") or "").strip()
        ch = chapters[n - 1]
        kind = lesson_tools.chapter_kind(ch)
        if stage == "produce" and kind == "record":
            self.log(f"正在备第 {n} 章录屏分镜…")
            next_plan = plan_lesson.generate(
                topic,
                "",
                course={
                    "course_title": plan.get("course_title") or topic,
                    "chapters": chapters,
                    "chapter": n,
                    "pipeline": "produce",
                    "audience": plan.get("audience") or "",
                    "slug": plan.get("slug") or "",
                },
                chapter=n,
            )
            next_plan["pipeline"] = "produce"
            self.apply_plan(next_plan)
            plan = next_plan
        else:
            plan["title"] = ch.get("title") or plan.get("title")
            plan["objective"] = ch.get("goal") or plan.get("objective")
            plan["chapter_kind"] = kind
            plan["narration"] = lesson_tools.as_script(ch.get("script"))
            plan["slides"] = lesson_tools.as_slides(ch.get("slides"))
            if kind == "narrate":
                plan = lesson_tools.force_narrate(plan)
            elif kind == "ppt":
                plan = lesson_tools.force_ppt(plan)
            self.apply_plan(plan)
        speech = self._plan_speech(plan)
        if lead:
            speech = lead.strip() + "\n\n" + speech
        self.chat.append(
            {
                "role": "assistant",
                "text": speech,
                "chips": self._plan_chips(plan),
                "acts": self._plan_acts(),
                "plan": plan,
            }
        )
        return {"ok": True, "action": "", **self.status()}

    def _produce_chapter(self) -> dict:
        plan = dict(self.lesson_plan or {})
        if lesson_tools.pipeline_of(plan) != "produce":
            if lesson_tools.pipeline_of(plan) == "outline":
                return self._prepare_materials()
            return self._enter_produce()
        kind = lesson_tools.chapter_kind(lesson_tools.current_chapter(plan))
        if kind == "narrate":
            return self.produce_narrate()
        if kind == "record":
            return self.produce_record()
        return self.produce_ppt()

    def produce_record(self, batch: bool = False) -> dict:
        import ppt_deck

        plan = dict(self.lesson_plan or {})
        if not plan:
            return {"ok": False, "error": "还没有课纲。", **self.status()}
        ch = lesson_tools.current_chapter(plan)
        n = int(ch.get("n") or plan.get("chapter") or 1)
        folder = course.chapter_dir(self.root, plan, n)
        folder.mkdir(parents=True, exist_ok=True)
        title = str(ch.get("title") or plan.get("title") or "本集")
        prompt, expect = lesson_tools.chapter_cursor_prompt(plan, ch)
        plan["title"] = title
        plan["objective"] = str(ch.get("goal") or plan.get("objective") or "")
        plan["prompt"] = prompt
        plan["expect"] = expect
        plan["tools"] = {
            **dict(plan.get("tools") or {}),
            "use_cursor": True,
            "code_via": "cursor",
            "need_code": True,
        }
        self.apply_plan(plan)
        slides = lesson_tools.slides_for_chapter(ch)
        lines = lesson_tools.concept_script(ch)
        if not lines:
            packed = plan_lesson.fill_chapter_scripts(plan)
            plan["chapters"] = packed.get("chapters") or plan.get("chapters")
            self.apply_plan(plan)
            ch = lesson_tools.current_chapter(plan)
            lines = lesson_tools.concept_script(ch)
            slides = lesson_tools.slides_for_chapter(ch)
        if not lines:
            stub = plan_lesson.stub_materials(ch)
            lines = lesson_tools.concept_script(stub)
            if stub.get("slides") and not slides:
                slides = lesson_tools.as_slides(stub.get("slides"))
        handoff = lesson_tools.practice_handoff(ch)
        ppt_deck.write_slides(folder / "slides.md", title, lesson_tools.with_practice_slide(slides, ch))
        slide_dir = folder / "slides"
        frames = sorted(slide_dir.glob("*.png")) or sorted(slide_dir.glob("*.svg"))
        if slides and not frames:
            self.log(f"第 {n} 章先画概念页，再接实操。")
            ppt_deck.write_pptx(folder / "deck.pptx", title, slides, log=self.log)
            frames = sorted(slide_dir.glob("*.png")) or sorted(slide_dir.glob("*.svg"))
        practice = folder / "practice.svg"
        ppt_deck.write_practice_slide(
            practice,
            str(handoff["slide"]["title"]),
            list(handoff["slide"].get("bullets") or []),
        )
        parts: list[dict | Path] = []
        intro_audio = folder / "intro.m4a"
        if frames and lines:
            if intro_audio.is_file() and intro_audio.stat().st_size > 200 and (folder / "timeline.json").is_file():
                self.log(f"第 {n} 章 PPT 口播已经在，接着接实操。")
                parts.append(
                    {
                        "id": "intro",
                        "kind": "slides",
                        "title": "PPT",
                        "audio": "intro.m4a",
                        "dur": round(course.clip_duration(intro_audio), 3),
                        "pages": voiceover.page_timings(folder, frames, course.clip_duration(intro_audio)),
                    }
                )
            else:
                self.log(f"第 {n} 章写 PPT 幻灯片 + 口播（不合成视频）。")
                parts.append(voiceover.write_talk_assets(folder, "intro", "PPT", lines, self.log, frames=frames))
        handoff_audio = folder / "handoff.m4a"
        if handoff_audio.is_file() and handoff_audio.stat().st_size > 200:
            self.log(f"第 {n} 章衔接口播已经在。")
            parts.append(
                {
                    "id": "handoff",
                    "kind": "slides",
                    "title": "接下来动手",
                    "audio": "handoff.m4a",
                    "dur": round(course.clip_duration(handoff_audio), 3),
                    "pages": voiceover.page_timings(folder, [practice], course.clip_duration(handoff_audio)),
                }
            )
        else:
            self.log(f"第 {n} 章口播接到实操…")
            parts.append(
                voiceover.write_talk_assets(
                    folder, "handoff", "接下来动手", handoff["into"], self.log, frames=[practice]
                )
            )
        self.log(f"第 {n} 章开始录 Cursor 实操…")
        take = folder / "cursor.mp4"
        try:
            path, _events = self._capture_cursor_take(take, batch=True, window_only=True)
        except Exception as exc:
            self.log(f"窗口录屏没写成（{self.friendly_error(exc)}），改整屏再录一次。")
            try:
                path, _events = self._capture_cursor_take(take, batch=True, window_only=False)
            except Exception as exc2:
                return {"ok": False, "error": self.friendly_error(exc2), **self.status()}
        if not path or not Path(path).is_file() or Path(path).stat().st_size < 1000:
            return {"ok": False, "error": "Cursor 实操没有成片。", **self.status()}
        parts.append(path)
        closer_audio = folder / "closer.m4a"
        if closer_audio.is_file() and closer_audio.stat().st_size > 200:
            parts.append(
                {
                    "id": "closer",
                    "kind": "slides",
                    "title": "对照",
                    "audio": "closer.m4a",
                    "dur": round(course.clip_duration(closer_audio), 3),
                    "pages": voiceover.page_timings(folder, [practice], course.clip_duration(closer_audio)),
                }
            )
        else:
            parts.append(
                voiceover.write_talk_assets(
                    folder, "closer", "对照", handoff["after"], self.log, frames=[practice]
                )
            )
        if not any(
            (isinstance(p, Path) and p.name == "cursor.mp4")
            or (isinstance(p, dict) and p.get("id") == "cursor")
            for p in parts
        ):
            return {"ok": False, "error": "这一章没有录上 Cursor。", **self.status()}
        dest = course.write_play_timeline(folder, parts)
        self.log(f"第 {n} 章写成时间轴：PPT 是幻灯片+口播，实操才是录屏。")
        plan["prompt"] = prompt
        plan["expect"] = expect
        plan = course.publish_folder(self.root, plan, folder, n)
        plan["confirmed"] = True
        plan["gate"] = "ready"
        self.apply_plan(plan)
        if batch:
            return {"ok": True, "dest": str(dest), "n": n, **self.status()}
        slug = plan.get("slug") or ""
        return self._after_chapter(
            plan,
            dest,
            f"第 {n} 章 PPT 和实操已按时间轴接好，素材改了不用再合成。",
            slug,
        )

    def produce_ppt(self, batch: bool = False) -> dict:
        import ppt_deck

        plan = dict(self.lesson_plan or {})
        if not plan:
            return {"ok": False, "error": "还没有课纲。", **self.status()}
        ch = lesson_tools.current_chapter(plan)
        lines = lesson_tools.as_script(ch.get("script"))
        slides = lesson_tools.slides_for_chapter(ch)
        if not lines:
            packed = plan_lesson.fill_chapter_scripts(plan)
            plan["chapters"] = packed.get("chapters") or plan.get("chapters")
            self.apply_plan(plan)
            ch = lesson_tools.current_chapter(plan)
            lines = lesson_tools.as_script(ch.get("script"))
            slides = lesson_tools.slides_for_chapter(ch)
        if not lines:
            return {"ok": False, "error": "这一章还没有解说词。", **self.status()}
        n = int(ch.get("n") or plan.get("chapter") or 1)
        folder = course.chapter_dir(self.root, plan, n)
        title = str(ch.get("title") or plan.get("title") or "本集")
        ppt_deck.write_slides(folder / "slides.md", title, slides)
        self.log(f"第 {n} 章正在让 DeepSeek 逐页手画 PPT…")
        pptx = ppt_deck.write_pptx(folder / "deck.pptx", title, slides, log=self.log)
        slide_dir = folder / "slides"
        frames = sorted(slide_dir.glob("*.png")) or sorted(slide_dir.glob("*.svg"))
        if pptx:
            self.log(f"DeepSeek 画页出片：{len(frames)} 页")
        else:
            self.log("DeepSeek 没画出 PPT，先出口播。")
        clip = voiceover.write_talk_assets(folder, "speech", title, lines, self.log, frames=frames)
        dest = course.write_play_timeline(folder, [clip])
        plan["narration"] = lines
        plan["slides"] = slides
        chapters = [dict(item) for item in (plan.get("chapters") or [])]
        for item in chapters:
            if int(item.get("n") or 0) == n:
                item["script"] = lines
                item["slides"] = slides
        plan["chapters"] = chapters
        plan = course.publish_folder(self.root, plan, folder, n)
        plan["confirmed"] = True
        plan["gate"] = "ready"
        self.apply_plan(plan)
        slug = plan.get("slug") or ""
        extra = "PPT 页和口播已分开写好。" if pptx else "口播已写好。"
        if batch:
            return {"ok": True, "dest": str(dest), "n": n, **self.status()}
        return self._after_chapter(plan, dest, f"第 {n} 章按时间轴出好了（幻灯片 + 语音，不合成视频）。{extra}", slug)

    def _handle_choice(self, choice: str) -> dict:
        plan = dict(self.lesson_plan or {})
        if choice in {"confirm_outline", "prepare_materials"}:
            return self._kick_produce_all("大纲定了，开始写素材并按章出片，不用再点下一章。")
        if choice == "confirm_materials":
            return self._kick_produce_all("开始按章出片，做完我叫你。")
        if choice in {"produce_chapter", "make_ppt", "next_chapter"}:
            return self._kick_produce_all("把还没出的章接着做完。")
        if choice in {"kind_ppt", "kind_narrate", "kind_record"}:
            kind = choice.replace("kind_", "")
            plan = lesson_tools.set_chapter_kind(plan, kind)
            self.apply_plan(plan)
            label = lesson_tools.kind_label(kind)
            self.chat.append(
                {
                    "role": "assistant",
                    "text": f"第 {plan.get('chapter') or 1} 章改成{label}，会走「{lesson_tools.tool_label(lesson_tools.chapter_tool(lesson_tools.current_chapter(plan)))}」。",
                    "chips": self._plan_chips(plan),
                    "acts": self._plan_acts(),
                    "plan": plan,
                }
            )
            return {"ok": True, "action": "", **self.status()}
        if choice == "show_demo":
            demo = (plan.get("demo_files") or [{}])[0]
            body = str(demo.get("content") or "还没有演示文件。")
            self.chat.append(
                {
                    "role": "assistant",
                    "text": f"{demo.get('path') or 'demo.py'} 全文如下。行就确认这样录，或让 Cursor 重写。\n\n{body}",
                    "chips": ["确认这样录", "让 Cursor 写演示", "不要代码演示"],
                    "acts": self._plan_acts(),
                    "plan": plan,
                }
            )
            return {"ok": True, "action": "", **self.status()}
        if choice == "narrate":
            return self.produce_narrate()
        if choice == "watch":
            slug = str(plan.get("slug") or "")
            self.chat.append(
                {
                    "role": "assistant",
                    "text": "学生端在右边打开。课纲里已出片的章可以直接看。",
                    "chips": self._plan_chips(plan),
                    "acts": self._plan_acts(),
                    "plan": plan,
                    "action": "watch",
                }
            )
            return {"ok": True, "action": "watch", "watch_url": f"/watch?slug={slug}" if slug else "/watch", **self.status()}
        if choice == "confirm_record":
            kind = lesson_tools.chapter_kind(lesson_tools.current_chapter(plan))
            if kind == "narrate":
                return self.produce_narrate()
            if kind == "ppt":
                return self.produce_ppt()
            if lesson_tools.pipeline_of(plan) == "produce":
                return self.produce_record()
            plan["confirmed"] = True
            plan["gate"] = "ready"
            self.apply_plan(plan)
            if (plan.get("tools") or {}).get("code_via") == "local":
                self.write_demo_files(plan)
            self.chat.append(
                {
                    "role": "assistant",
                    "text": "工具按你确认的来。开始按分镜录。",
                    "chips": ["停一下", "作废这段"],
                    "acts": [{"label": "停止录制", "act": "stop"}],
                    "action": "rundown",
                    "plan": plan,
                }
            )
            return {"ok": True, "action": "rundown", **self.status()}
        if choice == "cursor_demo":
            plan = lesson_tools.apply_choice(plan, "cursor_demo")
            prompt = str(plan.get("prompt") or "").strip()
            if not prompt:
                prompt, _ = lesson_tools.cursor_demo_prompt(plan)
                plan["prompt"] = prompt
            plan["expect"] = lesson_tools.honest_expect(plan)
            self.apply_plan(plan)
            self.prompt = prompt
            self.expect = plan["expect"]
            self.cursor_for_demo = True
            self.chat.append(
                {
                    "role": "assistant",
                    "text": "接下来让 Cursor 写演示代码。禁止装包、禁止密钥。跑偏了你点停止，我不会替你硬闯。",
                    "chips": ["停一下", "作废这段"],
                    "acts": [{"label": "停止录制", "act": "stop"}],
                    "action": "cursor",
                    "plan": plan,
                }
            )
            return {"ok": True, "action": "cursor", **self.status()}
        mapped = {
            "web_only": "web_only",
            "drop_code": "web_only",
            "accept_demo": "use_term",
            "drop_cursor": "use_term",
            "use_term": "use_term",
            "use_local": "use_term",
        }.get(choice)
        if mapped:
            plan = lesson_tools.apply_choice(plan, mapped)
            self.apply_plan(plan)
            self.chat.append(
                {
                    "role": "assistant",
                    "text": lesson_tools.tool_speech(plan),
                    "chips": self._plan_chips(),
                    "acts": self._plan_acts(),
                    "plan": plan,
                }
            )
            return {"ok": True, "action": "", **self.status()}
        return {"ok": False, "error": "这个选择还不认。", **self.status()}

    def _rewrite_demo(self, instruction: str) -> dict:
        data = plan_lesson.rewrite_demo(self.lesson_plan, instruction)
        plan = lesson_tools.merge_plan(
            self.lesson_plan,
            {
                "demo_path": data["path"],
                "demo_content": data["content"],
                "prompt": data["prompt"],
                "expect": data["expect"],
            },
        )
        self.apply_plan(plan)
        self.log(f"演示代码已重写：{data['path']}")
        self.chat.append(
            {
                "role": "assistant",
                "text": (data.get("say") or "代码已重写。") + "\n\n打开卡片看代码和提示词，不对再接着说。",
                "chips": self._plan_chips(),
                "acts": self._plan_acts(),
                "plan": plan,
            }
        )
        return {"ok": True, "action": "", **self.status()}

    def chat_turn(self, text: str | None = None) -> dict:
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "error": "说一句就行。", **self.status()}
        if not self.workspace:
            return {"ok": False, "error": "先选新建工程，或打开上次的课。", **self.status()}
        if text in {"重新开始", "重来", "清空对话", "换工程"}:
            return self.close_project()
        choice_now = lesson_tools.detect_choice(text)
        if self.busy or self.chatting:
            if choice_now == "watch":
                return self._handle_choice("watch")
            return {"ok": False, "error": "正在按章出片，稍等。要停就点重新开始。", **self.status()}
        self.chatting = True
        try:
            action = plan_lesson.detect_action(text)
            choice = lesson_tools.detect_choice(text)
            edit = lesson_tools.detect_edit(text)
            self.chat.append({"role": "user", "text": text, "chips": [], "acts": []})
            if self.lesson_plan and text in {"取消", "不改了"}:
                self.edit_field = ""
                self.chat.append(
                    {
                        "role": "assistant",
                        "text": "不改了。卡片还能改，行了就确认这样录。",
                        "chips": self._plan_chips(),
                        "acts": self._plan_acts(),
                        "plan": self.lesson_plan,
                    }
                )
                return {"ok": True, "action": "", **self.status()}
            if self.lesson_plan and self.edit_field:
                field = self.edit_field
                self.edit_field = ""
                if field == "regenerate":
                    topic = str(self.brief.get("topic") or self.lesson_plan.get("title") or text).strip()
                    packed = {
                        "course_title": self.lesson_plan.get("course_title") or topic,
                        "chapters": self.lesson_plan.get("chapters") or [],
                        "chapter": self.lesson_plan.get("chapter") or 1,
                    }
                    plan = plan_lesson.generate(
                        topic,
                        text,
                        course=packed if packed["chapters"] else None,
                        chapter=packed["chapter"] if packed["chapters"] else None,
                    )
                    self.apply_plan(plan)
                    self.chat.append(
                        {
                            "role": "assistant",
                            "text": self._plan_speech(plan),
                            "chips": self._plan_chips(),
                            "acts": self._plan_acts(),
                            "plan": plan,
                        }
                    )
                    return {"ok": True, "action": "", **self.status()}
                if field in {"demo_content", "prompt"} and not lesson_tools.looks_like_code(text):
                    return self._rewrite_demo(text)
                plan = lesson_tools.merge_plan(self.lesson_plan, {field: text})
                self.apply_plan(plan)
                label = lesson_tools.EDIT_LABELS.get(field) or field
                self.chat.append(
                    {
                        "role": "assistant",
                        "text": f"已改{label}。还可以改卡片，或再点一块继续改。行了再确认这样录。",
                        "chips": self._plan_chips(),
                        "acts": self._plan_acts(),
                        "plan": plan,
                    }
                )
                return {"ok": True, "action": "", **self.status()}
            if self.lesson_plan and edit == "new_topic":
                self.lesson_plan = None
                self.brief = dict(plan_lesson.OPENING["brief"])
                self.chat.append(
                    {
                        "role": "assistant",
                        "text": "好，这一集作废。新的讲什么？",
                        "chips": list(plan_lesson.OPENING["chips"]),
                        "acts": [],
                    }
                )
                return {"ok": True, "action": "", **self.status()}
            chapter_n = lesson_tools.detect_chapter(text)
            if self.lesson_plan and chapter_n:
                return self._switch_chapter(chapter_n, text)
            if self.lesson_plan and (
                lesson_tools.wants_code_rewrite(text) or edit in {"demo_content", "prompt"}
            ):
                instruction = text
                if re.fullmatch(r"(重写代码|改代码|改演示代码|重写演示|重写提示词|改提示词)", text):
                    instruction = (
                        "按标题和目标重写演示：必须是真 LCEL（langchain_core 的 Runnable 和 |），"
                        "不要手写 def chain()，提示词也一并改掉。"
                    )
                return self._rewrite_demo(instruction)
            if self.lesson_plan and edit == "regenerate":
                topic = str(self.brief.get("topic") or self.lesson_plan.get("title") or "").strip()
                packed = {
                    "course_title": self.lesson_plan.get("course_title") or topic,
                    "chapters": self.lesson_plan.get("chapters") or [],
                    "chapter": self.lesson_plan.get("chapter") or 1,
                }
                plan = plan_lesson.generate(
                    topic,
                    "按刚才的意见重写这一章，代码必须贴题，禁止假链。",
                    course=packed if packed["chapters"] else None,
                    chapter=packed["chapter"] if packed["chapters"] else None,
                )
                self.apply_plan(plan)
                self.chat.append(
                    {
                        "role": "assistant",
                        "text": self._plan_speech(plan),
                        "chips": self._plan_chips(),
                        "acts": self._plan_acts(),
                        "plan": plan,
                    }
                )
                return {"ok": True, "action": "", **self.status()}
            if self.lesson_plan and edit:
                self.edit_field = edit
                label = lesson_tools.EDIT_LABELS.get(edit) or "内容"
                self.chat.append(
                    {
                        "role": "assistant",
                        "text": f"把新的{label}发过来，整段贴就行。也可以直接改上面卡片再保存。",
                        "chips": ["取消"],
                        "acts": self._plan_acts(),
                        "plan": self.lesson_plan,
                    }
                )
                return {"ok": True, "action": "", **self.status()}
            if self.lesson_plan:
                stage = lesson_tools.pipeline_of(self.lesson_plan)
                if stage == "outline" and (
                    choice in {"confirm_outline", "prepare_materials"}
                    or plan_lesson.wants_decide(text)
                    or text in {"就这样", "可以", "没问题", "行"}
                ):
                    choice = "confirm_outline"
                elif stage == "materials" and (
                    choice in {"confirm_materials", "produce_chapter"}
                    or plan_lesson.wants_decide(text)
                    or text in {"就这样", "可以", "没问题", "行"}
                ):
                    choice = "confirm_materials"
                elif stage == "produce" and not choice and action == "rundown" and not self.lesson_plan.get("confirmed"):
                    choice = "confirm_record"
                if choice:
                    return self._handle_choice(choice)
                revised = lesson_tools.apply_revision(self.lesson_plan, text)
                if revised:
                    plan, say = revised
                    self.apply_plan(plan)
                    self.chat.append(
                        {
                            "role": "assistant",
                            "text": say,
                            "chips": self._plan_chips(),
                            "acts": self._plan_acts(),
                            "plan": plan,
                        }
                    )
                    return {"ok": True, "action": "", **self.status()}
                if action:
                    if action == "regenerate":
                        note = text
                        topic = str(self.brief.get("topic") or self.lesson_plan.get("title") or "").strip()
                        packed = {
                            "course_title": self.lesson_plan.get("course_title") or topic,
                            "chapters": self.lesson_plan.get("chapters") or [],
                            "chapter": self.lesson_plan.get("chapter") or 1,
                        }
                        plan = plan_lesson.generate(
                            topic,
                            note,
                            course=packed if packed["chapters"] else None,
                            chapter=packed["chapter"] if packed["chapters"] else None,
                        )
                        self.apply_plan(plan)
                        self.chat.append(
                            {
                                "role": "assistant",
                                "text": self._plan_speech(plan),
                                "chips": self._plan_chips(),
                                "acts": self._plan_acts(),
                                "plan": plan,
                            }
                        )
                        return {"ok": True, "action": "", **self.status()}
                    say = {
                        "rundown": "好，按分镜开始录。",
                        "web": "好，只录网页这一路。",
                        "term": "好，只录终端。",
                        "cursor": "好，只录 Cursor Agent。",
                        "pack": "好，出讲义和作业。",
                    }.get(action, "好。")
                    self.chat.append(
                        {
                            "role": "assistant",
                            "text": say,
                            "chips": ["停一下", "作废这段"],
                            "acts": [{"label": "停止录制", "act": "stop"}],
                            "action": action,
                            "plan": self.lesson_plan,
                        }
                    )
                    return {"ok": True, "action": action, **self.status()}
                turn = plan_lesson.interview(self.chat, self.brief, self.lesson_plan)
                self.brief = turn.get("brief") or self.brief
                patch = turn.get("patch") or {}
                plan = self.lesson_plan
                if patch:
                    plan = lesson_tools.merge_plan(plan, patch)
                    self.apply_plan(plan)
                if turn.get("action") == "rewrite_demo":
                    return self._rewrite_demo(text)
                if turn.get("action") in {"rundown", "web", "term", "cursor", "pack"}:
                    if turn.get("action") == "rundown" and not plan.get("confirmed"):
                        return self._handle_choice("confirm_record")
                    self.chat.append(
                        {
                            "role": "assistant",
                            "text": turn.get("say") or "好。",
                            "chips": ["停一下", "作废这段"],
                            "acts": [{"label": "停止录制", "act": "stop"}],
                            "action": turn.get("action"),
                            "plan": plan,
                        }
                    )
                    return {"ok": True, "action": turn.get("action"), **self.status()}
                if turn.get("action") == "regenerate" or (turn.get("ready") and (self.brief.get("note") or patch.get("note"))):
                    topic = str(self.brief.get("topic") or plan.get("title") or "").strip()
                    extra = str(self.brief.get("note") or patch.get("note") or text).strip()
                    packed = {
                        "course_title": plan.get("course_title") or topic,
                        "chapters": plan.get("chapters") or [],
                        "chapter": plan.get("chapter") or 1,
                    }
                    plan = plan_lesson.generate(
                        topic,
                        extra,
                        course=packed if packed["chapters"] else None,
                        chapter=packed["chapter"] if packed["chapters"] else None,
                    )
                    self.apply_plan(plan)
                    self.chat.append(
                        {
                            "role": "assistant",
                            "text": (turn.get("say") or "") + "\n\n" + self._plan_speech(plan),
                            "chips": turn.get("chips") or self._plan_chips(),
                            "acts": self._plan_acts(),
                            "plan": plan,
                        }
                    )
                    return {"ok": True, "action": "", **self.status()}
                self.chat.append(
                    {
                        "role": "assistant",
                        "text": turn.get("say") or "哪一块怪，直说。",
                        "chips": turn.get("chips") or self._plan_chips(),
                        "acts": self._plan_acts(),
                        "plan": plan,
                    }
                )
                return {"ok": True, "action": "", **self.status()}
            if plan_lesson.should_emit_outline(text, self.brief):
                return self._emit_course(text)
            turn = plan_lesson.interview(self.chat, self.brief, self.lesson_plan)
            self.brief = turn.get("brief") or self.brief
            msg = {
                "role": "assistant",
                "text": turn.get("say") or "接着说。",
                "chips": turn.get("chips") or [],
                "acts": [],
            }
            if turn.get("ready"):
                return self._emit_course(str(self.brief.get("topic") or text), turn.get("say") or "")
            self.chat.append(msg)
            if len(self.chat) > 40:
                keep_head = self.chat[:1]
                self.chat = keep_head + self.chat[-36:]
            return {"ok": True, "action": msg.get("action") or "", **self.status()}
        except Exception as exc:
            hint = self.friendly_error(exc)
            self.log(f"✗ 对话：{exc}")
            self.chat.append(
                {
                    "role": "assistant",
                    "text": hint + " 再说一次，或点「重新开始」。",
                    "chips": ["重新开始", "LangChain", "FastAPI"],
                    "acts": [],
                }
            )
            return {"ok": False, "error": hint, **self.status()}
        finally:
            self.chatting = False

    def set_engine(self, name: str | None = None) -> dict:
        try:
            info = plan_lesson.set_engine(str(name or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc), **self.status()}
        self.log("问答改走 " + str(info.get("engine_label") or name))
        return {"ok": True, **self.status()}

    def plan_lesson(self, topic: str | None = None, note: str | None = None) -> dict:
        def go() -> None:
            plan = plan_lesson.generate(str(topic or ""), str(note or ""))
            self.apply_plan(plan)

        result = self._run("备课", go)
        if result.get("ok"):
            result["plan"] = self.lesson_plan
        return result
