#!/usr/bin/env python3
"""Record a Terminal / iTerm lesson: run commands, pause, auto-stop."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import record_lesson as rl

Emit = Callable[[str], None]

TERM_OWNERS = {"Terminal", "iTerm2", "Alacritty", "kitty", "Warp", "Ghostty", "WezTerm"}

ALIASES = {
    "执行": "run",
    "run": "run",
    "命令": "run",
    "看": "look",
    "look": "look",
    "停顿": "look",
    "等待": "wait",
    "wait": "wait",
    "清屏": "clear",
    "clear": "clear",
    "章节": "card",
    "card": "card",
}


def parse_script(text: str) -> list[dict]:
    steps: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cmd, _, rest = line.partition(" ")
        key = ALIASES.get(cmd, ALIASES.get(cmd.lower()))
        if key is None:
            key = "run"
            rest = line
        steps.append({"cmd": key, "arg": rest.strip(), "raw": line})
    if not steps:
        raise RuntimeError("终端步骤是空的。")
    return steps


def list_term_windows() -> list[dict]:
    return [w for w in rl.list_on_screen_windows() if w["owner"] in TERM_OWNERS]


def pick_term_window() -> dict | None:
    wins = list_term_windows()
    return wins[0] if wins else None


def open_terminal(cwd: str | None = None) -> None:
    import shlex
    cmd = "clear"
    if cwd:
        cmd = f"cd {shlex.quote(str(cwd))} && clear"
    quoted = json.dumps(cmd)
    rl.run_osascript(
        "tell application \"Terminal\"\n"
        "  activate\n"
        f"  do script {quoted}\n"
        "end tell\n"
    )
    time.sleep(1.2)


def run_in_terminal(command: str) -> None:
    quoted = json.dumps(command)
    rl.run_osascript(
        "tell application \"Terminal\"\n"
        "  activate\n"
        "  if (count of windows) = 0 then do script \"\"\n"
        f"  do script {quoted} in front window\n"
        "end tell\n"
    )


def run_step(step: dict, emit: Emit | None = None) -> None:
    cmd = step["cmd"]
    arg = step.get("arg") or ""
    log = emit or (lambda m: print(m, flush=True))
    if cmd in {"wait", "look"}:
        secs = float(arg or "1.5")
        log("给学生看一眼" if cmd == "look" else f"等 {secs}s")
        time.sleep(secs)
        return
    if cmd == "clear":
        log("清屏")
        run_in_terminal("clear")
        time.sleep(0.4)
        return
    if cmd == "card":
        title = arg or "看这里"
        log(f"章节：{title}")
        shown = title.replace("'", "")
        run_in_terminal(f"printf '\\n==== {shown} ====\\n'")
        time.sleep(1.6)
        return
    if cmd == "run":
        if not arg:
            raise RuntimeError("执行 后面要跟命令")
        log(f"$ {arg}")
        run_in_terminal(arg)
        time.sleep(1.1)
        return
    raise RuntimeError(f"还没做这步：{cmd}")


def begin_capture(output: Path):
    pick = None
    for attempt in range(8):
        pick = pick_term_window()
        if pick:
            break
        if attempt == 0:
            open_terminal()
        time.sleep(0.7)
    if pick is None:
        raise RuntimeError("找不到终端窗口。先打开「终端」。")
    rl.raise_process(str(pick["owner"]))
    time.sleep(0.35)
    label = f"{pick['owner']} · {pick['name'] or 'Terminal'}"
    rec = rl.start_capture_window_id(output, int(pick["id"]), label)
    if rec is None:
        raise RuntimeError("终端窗口录制没起来。看一下屏幕录制权限。")
    return rec, label


def record_script(text: str, output: Path, cwd: str | None = None, emit: Emit | None = None) -> tuple[Path, list[dict]]:
    log = emit or (lambda m: print(m, flush=True))
    steps = parse_script(text)
    open_terminal(cwd)
    rec, label = begin_capture(output)
    log(f"开始录：{label}")
    t0 = time.time()
    events: list[dict] = []
    try:
        time.sleep(0.8)
        for step in steps:
            events.append({"t": round(time.time() - t0, 2), **step, "say": step["raw"]})
            log(f"→ {step['raw']}")
            run_step(step, emit)
        time.sleep(0.8)
    finally:
        rl.stop_ffmpeg(rec)
    path = rec.mp4_path or rec.mov_path or output
    log(f"录屏已保存：{path}")
    return Path(path), events
