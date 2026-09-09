#!/usr/bin/env python3
"""Record a Cursor Agent lesson: type prompts, wait for turns, capture the screen.

Cursor cannot type into its own chat box or record the local IDE. This script
drives the UI with macOS Accessibility keystrokes and records with ffmpeg.

Usage:
  python3 tools/record-lesson/record_lesson.py --dry-run
  python3 tools/record-lesson/record_lesson.py --prompts tools/record-lesson/prompts.example.txt

First run needs:
  System Settings → Privacy & Security → Accessibility  (Terminal / Python)
  System Settings → Privacy & Security → Screen Recording (ffmpeg / Terminal)
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPTS = SCRIPT_DIR / "prompts.example.txt"
ACTOR_ORIGIN = (48, 36)
ACTOR_SIZE = (1280, 860)
ACTOR_TITLE = ""
IDLE_RELATIVE = Path(".cursor/record-state/idle")
LIVE_RELATIVE = Path(".cursor/record-state/live.log")


@dataclass
class Prompt:
    text: str
    new_chat: bool = False
    wait_seconds: float | None = None
    expect: str | None = None


def project_root(explicit: Path | None) -> Path:
    if explicit:
        return explicit.expanduser().resolve()
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / "app.py").exists() and (candidate / "tools/record-lesson").exists():
            return candidate
        if (candidate / "tools/record-lesson/record_lesson.py").exists():
            return candidate
    return here


def parse_prompts(path: Path) -> list[Prompt]:
    raw = path.read_text(encoding="utf-8")
    chunks = [part.strip() for part in raw.split("\n---\n")]
    prompts: list[Prompt] = []
    for chunk in chunks:
        if not chunk or chunk.strip() == "---":
            continue
        lines = chunk.splitlines()
        new_chat = False
        wait_seconds: float | None = None
        expect: str | None = None
        body: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("@new_chat"):
                new_chat = True
                continue
            if stripped.startswith("@wait "):
                wait_seconds = float(stripped.split(None, 1)[1])
                continue
            if stripped.startswith("@expect "):
                expect = stripped.split(None, 1)[1].strip()
                continue
            if stripped.startswith("@") and body == []:
                raise SystemExit(f"Unknown directive in {path}: {stripped}")
            body.append(line)
        text = "\n".join(body).strip()
        if text:
            prompts.append(Prompt(text=text, new_chat=new_chat, wait_seconds=wait_seconds, expect=expect))
    if not prompts:
        raise SystemExit(f"No prompts found in {path}")
    return prompts


def run_osascript(source: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["osascript", "-e", source, *args],
        text=True,
        capture_output=True,
    )


def require_accessibility() -> None:
    result = run_osascript('tell application "System Events" to get name of first process')
    if result.returncode != 0:
        raise SystemExit(
            "没有辅助功能权限，无法往 Cursor 输入框打字。\n"
            "打开：系统设置 → 隐私与安全性 → 辅助功能，勾选「终端」（或你运行脚本的 App），然后重试。"
        )


def cursor_frontmost() -> bool:
    result = run_osascript(
        'tell application "System Events" to get name of first process whose frontmost is true'
    )
    return result.returncode == 0 and result.stdout.strip() == "Cursor"


def activate_cursor(folder: Path | None = None) -> None:
    raise_actor_window()
    if not cursor_frontmost():
        raise SystemExit("无法把 Cursor 带到前台。请先点一下 Cursor 窗口，再运行脚本。")


def cursor_window_count() -> int:
    result = run_osascript(
        'tell application "System Events" to tell process "Cursor" to count windows'
    )
    if result.returncode != 0 or not result.stdout.strip().isdigit():
        return 0
    return int(result.stdout.strip())


def stamp_front_window_as_actor() -> None:
    x, y = ACTOR_ORIGIN
    w, h = ACTOR_SIZE
    result = run_osascript(
        'tell application "System Events" to tell process "Cursor"\n'
        "  set frontmost to true\n"
        f"  set position of window 1 to {{{x}, {y}}}\n"
        f"  set size of window 1 to {{{w}, {h}}}\n"
        "end tell"
    )
    if result.returncode != 0:
        raise SystemExit(f"无法摆放新窗口：{result.stderr.strip()}")
    time.sleep(0.3)


def window_names() -> str:
    result = run_osascript(
        'tell application "System Events" to tell process "Cursor" to get name of every window'
    )
    return result.stdout or ""


def raise_window_containing(token: str) -> bool:
    if not token:
        return False
    script = f'''
    tell application "Cursor" to activate
    tell application "System Events" to tell process "Cursor"
      set frontmost to true
      repeat with w in windows
        set n to name of w as text
        if n contains "{token}" then
          perform action "AXRaise" of w
          return "ok"
        end if
      end repeat
      return "missing"
    end tell
    '''
    result = run_osascript(script)
    return result.returncode == 0 and "ok" in result.stdout


def raise_actor_window() -> None:
    if ACTOR_TITLE and raise_window_containing(ACTOR_TITLE):
        time.sleep(0.2)
        return
    x, y = ACTOR_ORIGIN
    script = f"""
    tell application "Cursor" to activate
    tell application "System Events" to tell process "Cursor"
      set frontmost to true
      set found to false
      repeat with w in windows
        set p to position of w
        if (item 1 of p > {x - 8}) and (item 1 of p < {x + 8}) then
          if (item 2 of p > {y - 8}) and (item 2 of p < {y + 8}) then
            perform action "AXRaise" of w
            set found to true
            exit repeat
          end if
        end if
      end repeat
      if found is false and (count of windows) > 0 then
        perform action "AXRaise" of window 1
      end if
    end tell
    """
    run_osascript(script)
    time.sleep(0.25)


def reuse_existing_actor_window() -> bool:
    reused = run_osascript(
        'tell application "Cursor" to activate\n'
        'tell application "System Events" to tell process "Cursor"\n'
        "  set frontmost to true\n"
        "  set picked to false\n"
        "  repeat with w in windows\n"
        '    set n to name of w as text\n'
        '    if n is "Cursor" then\n'
        "      perform action \"AXRaise\" of w\n"
        "      set picked to true\n"
        "      exit repeat\n"
        "    end if\n"
        "  end repeat\n"
        "  if picked then\n"
        "    return \"ok\"\n"
        "  else\n"
        "    return \"missing\"\n"
        "  end if\n"
        "end tell"
    )
    return reused.returncode == 0 and "ok" in reused.stdout


def open_actor_with_folder(folder: Path) -> None:
    global ACTOR_TITLE
    folder = folder.resolve()
    ACTOR_TITLE = folder.name
    print(f"  目录已建好，新窗口直接打开：{folder}", flush=True)
    subprocess.Popen(
        ["cursor", "-n", str(folder)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        time.sleep(0.25)
        if folder.name in window_names():
            time.sleep(1.2)
            raise_window_containing(folder.name)
            stamp_front_window_as_actor()
            raise_window_containing(folder.name)
            print(f"  已打开窗口：{folder.name}（这边只遥控，不往这个对话输入）", flush=True)
            return
    raise SystemExit(f"新窗口没有打开 {folder}")


def install_actor_hooks(folder: Path) -> None:
    """Put the stop/output hooks into the actor project so we hear that window."""
    controller = project_root(None)
    folder = folder.resolve()
    src = controller / ".cursor"
    if not (src / "hooks.json").exists():
        print("  当前工程没有 .cursor/hooks.json，装不上结束钩子", flush=True)
        return
    if folder == controller.resolve():
        print("  新窗口就是当前工程，钩子已经在", flush=True)
        return
    dst = folder / ".cursor"
    (dst / "hooks").mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "hooks.json", dst / "hooks.json")
    for name in ("record-event.py", "record-event.sh"):
        shutil.copy2(src / "hooks" / name, dst / "hooks" / name)
    os.chmod(dst / "hooks" / "record-event.sh", 0o755)
    print(f"  已把结束钩子装进：{folder}", flush=True)


def prepare_actor_project(folder: Path) -> Path:
    folder = folder.expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    install_actor_hooks(folder)
    print(f"  项目目录：{folder}", flush=True)
    return folder


def _dismiss_close_sheet() -> None:
    run_osascript(
        'tell application "System Events" to tell process "Cursor"\n'
        "  if exists (sheet 1 of window 1) then\n"
        "    tell sheet 1 of window 1\n"
        '      try\n'
        '        click button "Save"\n'
        "      end try\n"
        '      try\n'
        '        click button "保存"\n'
        "      end try\n"
        "    end tell\n"
        "    delay 0.3\n"
        "  end if\n"
        "  if exists (sheet 1 of window 1) then\n"
        '    keystroke return\n'
        "  end if\n"
        "end tell"
    )


def close_actor_window(folder: Path | None = None) -> bool:
    """Close only the actor Cursor window. Never quit Cursor, never close this controller."""
    controller = project_root(None)
    if folder is not None and folder.resolve() == controller.resolve():
        print("  不关：那就是导播这个窗口", flush=True)
        return False
    token = ACTOR_TITLE or (folder.name if folder is not None else "")
    if not token:
        print("  不知道新窗口叫什么，不关", flush=True)
        return False
    if token.lower() in {controller.name.lower(), "recording-kg"}:
        print(f"  不关：窗口名 {token} 会碰到导播工程", flush=True)
        return False
    if token not in window_names():
        print("  新窗口已经不在了", flush=True)
        return True
    escaped = token.replace("\\", "\\\\").replace('"', '\\"')
    if not raise_window_containing(token):
        print("  找不到新窗口，不关导播这边", flush=True)
        return False
    time.sleep(0.25)
    attempts = [
        (
            "menu",
            '''
            tell application "System Events" to tell process "Cursor"
              set frontmost to true
              try
                click menu item "Close Window" of menu "File" of menu bar 1
                return "ok"
              end try
              try
                click menu item "关闭窗口" of menu "文件" of menu bar 1
                return "ok"
              end try
              try
                click menu item "Close Folder" of menu "File" of menu bar 1
                return "ok"
              end try
              try
                click menu item "关闭文件夹" of menu "文件" of menu bar 1
                return "ok"
              end try
              return "missing"
            end tell
            ''',
        ),
        (
            "close-button",
            f'''
            tell application "System Events" to tell process "Cursor"
              set frontmost to true
              repeat with w in windows
                if (name of w as text) contains "{escaped}" then
                  perform action "AXRaise" of w
                  delay 0.15
                  try
                    click (first button of w whose subrole is "AXCloseButton")
                  on error
                    click button 1 of w
                  end try
                  return "ok"
                end if
              end repeat
              return "missing"
            end tell
            ''',
        ),
        (
            "shortcut",
            'tell application "System Events" to keystroke "w" using {command down, shift down}',
        ),
    ]
    for name, script in attempts:
        run_osascript(script)
        time.sleep(0.45)
        _dismiss_close_sheet()
        time.sleep(0.4)
        if token not in window_names():
            print(f"  已关掉新窗口（{name}）", flush=True)
            return True
    print("  关窗口没成功。请手动把新窗口关掉。", flush=True)
    notify_need_human("关窗失败，请手动关掉录课那个 Cursor 窗口")
    return False


def open_folder_in_actor(folder: Path) -> None:
    folder = folder.resolve()
    print(f"  在新窗口中打开目录：{folder}", flush=True)
    raise_actor_window()
    time.sleep(0.35)
    subprocess.run(["pbcopy"], input=str(folder), text=True, check=False)
    menu = run_osascript(
        'tell application "System Events" to tell process "Cursor"\n'
        "  set frontmost to true\n"
        "  try\n"
        '    click menu item "Open Folder..." of menu "File" of menu bar 1\n'
        "    return \"en\"\n"
        "  end try\n"
        "  try\n"
        '    click menu item "Open Folder…" of menu "File" of menu bar 1\n'
        "    return \"en\"\n"
        "  end try\n"
        "  try\n"
        '    click menu item "打开文件夹…" of menu "文件" of menu bar 1\n'
        "    return \"zh\"\n"
        "  end try\n"
        "  try\n"
        '    click menu item "打开…" of menu "文件" of menu bar 1\n'
        "    return \"zh-open\"\n"
        "  end try\n"
        '  return "missing"\n'
        "end tell"
    )
    if "missing" in (menu.stdout or "") or menu.returncode != 0:
        run_osascript('tell application "System Events" to keystroke "o" using {command down, option down}')
        time.sleep(0.8)
    else:
        time.sleep(1.0)
    run_osascript('tell application "System Events" to keystroke "g" using {command down, shift down}')
    time.sleep(0.6)
    run_osascript('tell application "System Events" to keystroke "v" using command down')
    time.sleep(0.3)
    run_osascript('tell application "System Events" to keystroke return')
    time.sleep(0.5)
    run_osascript('tell application "System Events" to keystroke return')
    time.sleep(2.0)
    stamp_front_window_as_actor()
    raise_actor_window()
    run_osascript('tell application "System Events" to keystroke "i" using command down')
    time.sleep(0.7)
    names = run_osascript(
        'tell application "System Events" to tell process "Cursor" to get name of every window'
    )
    print(f"  当前窗口：{names.stdout.strip() or '(未知)'}", flush=True)
    if "demo-playground" not in names.stdout and folder.name not in names.stdout:
        print("  警告：窗口标题里还没有项目名，打开目录可能没成功", flush=True)


def cursor_window_bounds() -> tuple[int, int, int, int] | None:
    result = run_osascript(
        'tell application "System Events" to tell process "Cursor"\n'
        "  get {position, size} of window 1\n"
        "end tell"
    )
    if result.returncode != 0:
        return None
    nums = [int(x.strip()) for x in result.stdout.replace("{", "").replace("}", "").split(",") if x.strip()]
    if len(nums) != 4:
        return None
    x, y, w, h = nums
    return x, y, w, h


def cg_click(x: int, y: int) -> None:
    class CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    cg.CGEventCreateMouseEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        CGPoint,
        ctypes.c_uint32,
    ]
    cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    point = CGPoint(float(x), float(y))
    for event_type in (5, 1, 2):
        event = cg.CGEventCreateMouseEvent(None, event_type, point, 0)
        cg.CGEventPost(0, event)
        cf.CFRelease(event)
        time.sleep(0.02)


def click_agents_window_button() -> None:
    """Click the top-right Agents Window control in the actor Cursor."""
    bounds = cursor_window_bounds()
    if bounds is None:
        return
    x, y, w, h = bounds
    cg_click(x + w - 108, y + 46)


def ensure_right_chat_panel() -> None:
    """Open the right-side Agent chat. Do not toggle it closed afterward."""
    raise_actor_window()
    time.sleep(0.25)
    click_agents_window_button()
    time.sleep(0.8)
    run_osascript(
        'tell application "System Events" to keystroke "l" using {shift down, command down}'
    )
    time.sleep(0.7)
    print("  已尝试打开右侧 Agent 聊天栏", flush=True)


def click_composer() -> None:
    """Focus the Agent box in the actor window (the new Cursor), not this controller."""
    raise_actor_window()
    bounds = cursor_window_bounds()
    if bounds is None:
        return
    x, y, w, h = bounds
    cx = x + int(w * 0.82)
    cy = y + int(h * 0.90)
    time.sleep(0.15)
    cg_click(cx, cy)
    time.sleep(0.12)
    cg_click(cx, cy)
    time.sleep(0.15)


def write_type_script(prompt_path: Path, delay: float) -> str:
    posix = str(prompt_path)
    x, y = ACTOR_ORIGIN
    return f"""
set promptPath to "{posix}"
set theText to read POSIX file promptPath as «class utf8»
set the clipboard to theText
tell application "Cursor" to activate
tell application "System Events" to tell process "Cursor"
  set frontmost to true
  repeat with w in windows
    set p to position of w
    if (item 1 of p > {x - 8}) and (item 1 of p < {x + 8}) then
      if (item 2 of p > {y - 8}) and (item 2 of p < {y + 8}) then
        perform action "AXRaise" of w
        exit repeat
      end if
    end if
  end repeat
end tell
delay 0.2
tell application "System Events"
  keystroke "a" using command down
  delay 0.08
  key code 51
  delay 0.08
  keystroke "v" using command down
  delay 0.15
  keystroke return using command down
end tell
"""


def type_and_send(text: str, delay: float, tmp_dir: Path) -> None:
    prompt_path = tmp_dir / "prompt.txt"
    prompt_path.write_text(text, encoding="utf-8")
    script_path = tmp_dir / "type.scpt"
    script_path.write_text(write_type_script(prompt_path, delay), encoding="utf-8")
    result = subprocess.run(["osascript", str(script_path)], text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"打字失败：{result.stderr.strip() or result.stdout.strip()}")


def new_agent_chat() -> None:
    click_composer()
    run_osascript('tell application "System Events" to keystroke "r" using command down')
    time.sleep(0.4)
    click_composer()


def clear_idle(root: Path) -> None:
    state = root / ".cursor/record-state"
    state.mkdir(parents=True, exist_ok=True)
    idle = state / "idle"
    live = state / "live.log"
    if idle.exists():
        idle.unlink()
    live.write_text("", encoding="utf-8")


def reset_idle(root: Path) -> None:
    """Drop the stop flag but keep the live log, so a failed turn can be retried."""
    idle = root / IDLE_RELATIVE
    if idle.exists():
        idle.unlink()


def idle_ready(root: Path, started_at: float) -> bool:
    path = root / IDLE_RELATIVE
    if not path.exists():
        return False
    return path.stat().st_mtime >= started_at - 0.05


def live_text(root: Path) -> str:
    path = root / LIVE_RELATIVE
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


_CMD_END_RE = re.compile(r"命令结束 exit=([^：\s]+)")


def _clip(text: str, limit: int = 400) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def printed_output(line: str) -> str:
    if "输出：" not in line:
        return ""
    return line.split("输出：", 1)[1].strip()


def iter_command_ends(root: Path):
    for raw in live_text(root).splitlines():
        match = _CMD_END_RE.search(raw)
        if not match:
            continue
        yield match.group(1), raw, printed_output(raw)


def last_shell(root: Path) -> tuple[str, str, str]:
    last = ("", "", "")
    for item in iter_command_ends(root):
        last = item
    return last


def last_printed_output(root: Path) -> str:
    return last_shell(root)[2]


def marker_in_result(payload: str, expect: str) -> bool:
    """True if a printed line is the marker or starts with it. Ignore markers buried mid-sentence."""
    needle = (expect or "").strip().lower()
    if not needle or not payload.strip():
        return False
    blob = payload.strip().lower()
    if blob == needle or blob.startswith(needle):
        return True
    for part in payload.splitlines():
        line = part.strip().lower()
        if line == needle or (line and line.startswith(needle)):
            return True
    return False


def output_ran_ok(root: Path) -> bool:
    code, _line, payload = last_shell(root)
    if code not in {"", "?", "0"}:
        return False
    text = (payload or "").strip()
    if not text:
        return False
    low = text.lower()
    return not any(mark in low for mark in ("traceback", "syntaxerror", "modulenotfounderror", "nameerror"))


def looks_like_goal_expect(expect: str) -> bool:
    raw = (expect or "").strip()
    return bool(re.match(r"^(能|看完|会|理解|掌握|说出|运行第|第一个)", raw))


def output_marker_seen(root: Path, expect: str | None) -> bool:
    needle = (expect or "").strip()
    if not needle:
        return False
    for code, _line, payload in iter_command_ends(root):
        if code not in {"", "?"} and code.lstrip("-").isdigit() and int(code) != 0:
            continue
        if marker_in_result(payload, needle):
            return True
    return False


def failure_detail(root: Path, expect: str | None) -> str | None:
    """Why this stopped turn should not be treated as success. Quote the real output."""
    code, _line, payload = last_shell(root)
    expect_s = (expect or "").strip()
    shown = _clip(payload) if payload else ""
    if expect_s and not output_marker_seen(root, expect):
        if looks_like_goal_expect(expect_s) and output_ran_ok(root):
            return None
        if shown:
            return f"没看到「{expect_s}」。实际输出：{shown}"
        if code and code not in {"0", "?"}:
            return f"没看到「{expect_s}」。exit={code}"
        return f"Agent 停了，但没看到「{expect_s}」"
    if code.lstrip("-").isdigit() and int(code) != 0:
        if shown:
            return f"exit={code}。实际输出：{shown}"
        return f"exit={code}"
    return None


def turn_stopped(root: Path, started_at: float) -> bool:
    if idle_ready(root, started_at):
        return True
    for line in live_text(root).splitlines():
        if "这一轮结束" in line:
            return True
    return False


def turn_result(root: Path, started_at: float, expect: str | None = None) -> tuple[str, str] | None:
    """(kind, detail) for a finished turn, or None if still running.

    kind: output = saw the demo marker; stop = Agent finished cleanly;
    fail = Agent stopped but the task did not succeed.
    """
    if output_marker_seen(root, expect):
        return "output", "看到完成标志"
    if not turn_stopped(root, started_at):
        return None
    if looks_like_goal_expect(expect or "") and output_ran_ok(root):
        return "output", "程序已跑通"
    fail = failure_detail(root, expect)
    if fail:
        return "fail", fail
    return "stop", "这一轮任务停了"


def notify_need_human(body: str) -> None:
    title = "录课需要你介入"
    script = (
        f"display notification {json.dumps(body, ensure_ascii=False)} "
        f"with title {json.dumps(title, ensure_ascii=False)} sound name \"Basso\""
    )
    run_osascript(script)


def consume_live_log(path: Path, offset: int) -> tuple[list[str], int]:
    if not path.exists():
        return [], offset
    data = path.read_text(encoding="utf-8", errors="replace")
    if len(data) < offset:
        offset = 0
    chunk = data[offset:]
    if not chunk:
        return [], offset
    lines = [line for line in chunk.splitlines() if line.strip()]
    return lines, offset + len(chunk)


def _print_hold_gate(saw_event: bool) -> None:
    print("  ⚠ 需要人介入。录屏还在录，不要当这一轮已经结束。", flush=True)
    if not saw_event:
        print("  一直没收到命令输出。去新窗口看是不是报错、Hooks 没加载，或 Agent 停住了。", flush=True)
    else:
        print("  有命令输出，但这一轮还没停。去新窗口把卡住的地方处理掉。", flush=True)
    print("  处理好回车 = 继续等这一轮结束。输入 skip 再回车 = 不等了。Ctrl+C = 停录。", flush=True)


def _print_fail_gate(detail: str) -> None:
    print(f"  ✗ 任务失败：{detail}", flush=True)
    print("  需要你介入。录屏没停，窗口还开着。去新窗口处理。", flush=True)
    print("  处理好回车 = 继续等。输入 skip 再回车 = 不等了。Ctrl+C = 停录。", flush=True)
    notify_need_human(detail)


def wait_for_turn(
    root: Path,
    timeout: float,
    mode: str,
    expect: str | None = None,
) -> str:
    """Wait until this assigned task succeeds.

    Success: expected output, or a clean Agent stop.
    Failure or timeout: tell the human and wait; do not close the window.
    """
    stdin_wait = sys.stdin.isatty()
    live_path = root / LIVE_RELATIVE

    def check() -> tuple[str, str] | None:
        return turn_result(root, started, expect)

    if mode == "seconds":
        started = time.time()
        print(f"  固定等待 {timeout:.0f}s（看不到命令是否结束，只是计时）", flush=True)
        time.sleep(timeout)
        result = check()
        if result and result[0] != "fail":
            return result[0]
        if result and result[0] == "fail":
            _print_fail_gate(result[1])
        else:
            _print_hold_gate(saw_event=True)
        if not stdin_wait:
            print("  没有终端可以等人，接着等任务自己结束。", flush=True)
            offset = 0
            while True:
                lines, offset = consume_live_log(live_path, offset)
                for line in lines:
                    print(f"  {line}", flush=True)
                result = check()
                if result and result[0] != "fail":
                    return result[0]
                time.sleep(0.25)
        raw = sys.stdin.readline().strip().lower()
        if raw == "skip":
            return "skip"
        reset_idle(root)
        print("  人已处理完，继续等这一轮结束。", flush=True)
        return "human"

    started = time.time()
    offset = 0
    last_pulse = started
    saw_event = False
    holding = False
    deadline = started + timeout if timeout > 0 else None
    if expect:
        print(f"  等这一轮任务成功。看到输出「{expect}」就算演示完。失败会叫你介入。", flush=True)
    else:
        print("  等这一轮任务成功。失败或超时会叫你介入。", flush=True)
    while True:
        lines, offset = consume_live_log(live_path, offset)
        for line in lines:
            saw_event = True
            print(f"  {line}", flush=True)
        if mode in {"hook", "auto"}:
            result = check()
            if result:
                kind, detail = result
                if kind == "fail":
                    if not holding:
                        holding = True
                        _print_fail_gate(detail)
                else:
                    extra, offset = consume_live_log(live_path, offset)
                    for line in extra:
                        print(f"  {line}", flush=True)
                    time.sleep(0.4)
                    return kind
        elapsed = time.time() - started
        if deadline is not None and not holding and time.time() >= deadline:
            holding = True
            _print_hold_gate(saw_event)
            notify_need_human("这一轮超时还没成功，需要你介入")
        now = time.time()
        if not holding and now - last_pulse >= 8:
            print(f"  …还在等任务（已 {elapsed:.0f}s）", flush=True)
            last_pulse = now
        if stdin_wait:
            ready, _, _ = select.select([sys.stdin], [], [], 0.25)
            if ready:
                raw = sys.stdin.readline().strip().lower()
                if holding:
                    if raw == "skip":
                        return "skip"
                    holding = False
                    last_pulse = time.time()
                    reset_idle(root)
                    if timeout > 0:
                        deadline = time.time() + timeout
                    print("  人已处理完，继续等这一轮结束。录屏没停。", flush=True)
                else:
                    return "key"
        else:
            time.sleep(0.25)


@dataclass
class Recorder:
    pid: int
    via: str
    proc: subprocess.Popen[bytes] | None = None
    pid_file: Path | None = None
    log_path: Path | None = None
    mov_path: Path | None = None
    mp4_path: Path | None = None


BROWSER_OWNERS = {
    "Google Chrome",
    "Chromium",
    "Arc",
    "Brave Browser",
    "Microsoft Edge",
    "Safari",
    "Firefox",
    "Dia",
    "Orion",
    "Vivaldi",
    "Opera",
}


def list_on_screen_windows() -> list[dict]:
    script = SCRIPT_DIR / "list-windows.swift"
    if not script.is_file():
        script = SCRIPT_DIR / "list-cursor-windows.swift"
    result = subprocess.run(["swift", str(script)], capture_output=True, text=True)
    rows: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 6)
        if len(parts) < 6:
            continue
        try:
            wid, x, y, w, h = (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]))
        except ValueError:
            continue
        if len(parts) >= 7:
            owner, name = parts[5], parts[6]
        else:
            owner, name = "Cursor", parts[5]
        rows.append({"id": wid, "x": x, "y": y, "w": w, "h": h, "owner": owner, "name": name})
    return rows


def list_cursor_windows() -> list[tuple[int, int, int, int, int, str]]:
    return [
        (w["id"], w["x"], w["y"], w["w"], w["h"], w["name"])
        for w in list_on_screen_windows()
        if w["owner"] == "Cursor"
    ]


def list_browser_windows() -> list[dict]:
    return [w for w in list_on_screen_windows() if w["owner"] in BROWSER_OWNERS]


def pick_browser_window() -> dict | None:
    wins = list_browser_windows()
    return wins[0] if wins else None


def raise_process(owner: str) -> None:
    run_osascript(f'tell application "{owner}" to activate\n')


def open_browser(url: str | None = None) -> None:
    target = (url or "").strip() or "https://www.bing.com"
    quoted = json.dumps(target)
    if Path("/Applications/Google Chrome.app").is_dir():
        run_osascript(
            "tell application \"Google Chrome\"\n"
            "  activate\n"
            "  if (count of windows) = 0 then make new window\n"
            f"  set URL of active tab of front window to {quoted}\n"
            "end tell\n"
        )
        time.sleep(2.4)
        return
    for name in ("Arc", "Microsoft Edge", "Brave Browser", "Safari", "Firefox"):
        app = Path("/Applications") / f"{name}.app"
        if not app.is_dir():
            continue
        subprocess.run(["open", "-a", name, target], check=False)
        time.sleep(2.2)
        return
    subprocess.run(["open", target], check=False)
    time.sleep(2.2)


def scroll_front_browser(steps: int = 6, pause: float = 0.9) -> None:
    pick = pick_browser_window()
    owner = str(pick["owner"]) if pick else "Google Chrome"
    raise_process(owner)
    time.sleep(0.35)
    js = "window.scrollBy(0, Math.round(Math.max(window.innerHeight, 700) * 0.85))"
    for _ in range(steps):
        used_keys = True
        if owner in {"Google Chrome", "Chromium", "Microsoft Edge", "Brave Browser", "Arc"}:
            result = run_osascript(
                f'tell application "{owner}"\n'
                "  tell active tab of front window\n"
                f"    execute javascript {json.dumps(js)}\n"
                "  end tell\n"
                "end tell\n"
            )
            used_keys = result.returncode != 0
        elif owner == "Safari":
            result = run_osascript(
                "tell application \"Safari\"\n"
                f"  do JavaScript {json.dumps(js)} in document 1\n"
                "end tell\n"
            )
            used_keys = result.returncode != 0
        if used_keys:
            run_osascript('tell application "System Events" to key code 121\n')
        time.sleep(pause)


def chrome_js(code: str) -> bool:
    pick = pick_browser_window()
    owner = str(pick["owner"]) if pick else "Google Chrome"
    result = run_osascript(
        f'tell application "{owner}"\n'
        "  tell active tab of front window\n"
        f"    execute javascript {json.dumps(code)}\n"
        "  end tell\n"
        "end tell\n"
    )
    return result.returncode == 0


def type_baidu_search(query: str) -> None:
    from urllib.parse import quote

    q = json.dumps(query)
    js = (
        "(function(){"
        f"var q={q};"
        'var box=document.querySelector("#chat-textarea")||document.querySelector("#kw")'
        '||document.querySelector("textarea")||document.querySelector("input[name=wd]");'
        "if(box){box.focus();box.value=q;box.dispatchEvent(new Event('input',{bubbles:true}));}"
        'var btn=document.querySelector("#chat-submit-button")||document.querySelector("#su")'
        '||document.querySelector("form button[type=submit]");'
        "if(btn){btn.click();return 'ok';}"
        "if(box&&box.form){box.form.submit();return 'ok';}"
        "location.href='https://www.baidu.com/s?wd='+encodeURIComponent(q);"
        "return 'nav';"
        "})();"
    )
    if not chrome_js(js):
        open_browser("https://www.baidu.com/s?wd=" + quote(query))


def actor_window_id() -> int | None:
    titled: list[int] = []
    placed: list[int] = []
    x0, y0 = ACTOR_ORIGIN
    for wid, x, y, w, h, name in list_cursor_windows():
        if ACTOR_TITLE and ACTOR_TITLE in name:
            titled.append(wid)
        if abs(x - x0) <= 16 and abs(y - y0) <= 16:
            placed.append(wid)
    if titled:
        return max(titled)
    if placed:
        return max(placed)
    return None


def start_capture_window_id(output: Path, window_id: int, label: str = "") -> Recorder | None:
    output.parent.mkdir(parents=True, exist_ok=True)
    mov_path = output.with_suffix(".mov")
    log_path = output.with_suffix(".ffmpeg.log")
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        ["screencapture", "-x", "-v", "-l", str(window_id), str(mov_path)],
        stdout=log_file,
        stderr=log_file,
    )
    time.sleep(1.2)
    if proc.poll() is not None:
        log_file.close()
        detail = log_path.read_text(encoding="utf-8", errors="replace").strip()
        print(f"  窗口录制启动失败：{detail}", flush=True)
        return None
    who = label or f"id={window_id}"
    print(f"  只录窗口 {who}，不会把其他窗口录进去", flush=True)
    return Recorder(
        pid=proc.pid,
        via="window",
        proc=proc,
        log_path=log_path,
        mov_path=mov_path,
        mp4_path=output.with_suffix(".mp4"),
    )


def start_window_capture(output: Path, window_id: int | None = None) -> Recorder | None:
    """Record one window. Default: actor Cursor. Pass window_id for browser / other apps."""
    if window_id is None:
        raise_actor_window()
        for _ in range(8):
            window_id = actor_window_id()
            if window_id:
                break
            time.sleep(0.3)
        if window_id is None:
            print("  找不到新窗口编号，无法做窗口录制", flush=True)
            return None
    return start_capture_window_id(output, window_id)


def _ffmpeg_cmd(output: Path, mic: bool) -> list[str]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("找不到 ffmpeg。先执行：brew install ffmpeg")
    video_audio = "1:0" if mic else "1:none"
    return [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "avfoundation",
        "-capture_cursor",
        "1",
        "-pixel_format",
        "nv12",
        "-framerate",
        "30",
        "-i",
        video_audio,
        "-c:v",
        "h264_videotoolbox",
        "-b:v",
        "8M",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]


def start_ffmpeg_via_terminal(output: Path, mic: bool) -> Recorder | None:
    """Record from Terminal.app so macOS does not replace Cursor with the wallpaper."""
    output.parent.mkdir(parents=True, exist_ok=True)
    pid_file = output.with_suffix(".ffmpeg.pid")
    log_path = output.with_suffix(".ffmpeg.log")
    runner = output.with_suffix(".ffmpeg.sh")
    cmd = _ffmpeg_cmd(output, mic)
    quoted = " ".join(shlex.quote(part) for part in cmd)
    runner.write_text(
        "#!/bin/bash\n"
        f"echo $$ > {shlex.quote(str(pid_file))}.bash\n"
        f"{quoted} \\\n"
        f"  > {shlex.quote(str(log_path))} 2>&1 &\n"
        "FFPID=$!\n"
        f"echo $FFPID > {shlex.quote(str(pid_file))}\n"
        "wait $FFPID\n",
        encoding="utf-8",
    )
    runner.chmod(0o755)
    if pid_file.exists():
        pid_file.unlink()
    launched = run_osascript(
        'tell application "Terminal"\n'
        f'  do script {json.dumps("bash " + str(runner))}\n'
        "end tell\n"
        "delay 0.4\n"
        'tell application "System Events"\n'
        '  if exists process "Terminal" then set visible of process "Terminal" to false\n'
        "end tell\n"
        'tell application "Cursor" to activate\n'
    )
    if launched.returncode != 0:
        print(f"  无法用终端启动录屏：{launched.stderr.strip()}", flush=True)
        return None
    for _ in range(20):
        time.sleep(0.25)
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
            except ValueError:
                continue
            try:
                os.kill(pid, 0)
            except OSError:
                continue
            return Recorder(pid=pid, via="terminal", pid_file=pid_file, log_path=log_path)
    print("  终端录屏没有起来，改回直接启动（Cursor 窗口可能变成桌面背景）", flush=True)
    return None


def start_ffmpeg(output: Path, mic: bool, window_only: bool = True) -> Recorder:
    output.parent.mkdir(parents=True, exist_ok=True)
    if window_only:
        rec = start_window_capture(output)
        if rec is not None:
            return rec
        print("  窗口录制失败，退回整屏（会录到其他窗口）", flush=True)
    rec = start_ffmpeg_via_terminal(output, mic)
    if rec is not None:
        print("  录屏进程由「终端」启动，这样能录到你正在用的 Cursor 窗口", flush=True)
        return rec
    log_path = output.with_suffix(".ffmpeg.log")
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(_ffmpeg_cmd(output, mic), stdin=subprocess.PIPE, stderr=log_file)
    time.sleep(1.5)
    if proc.poll() is not None:
        log_file.close()
        detail = log_path.read_text(encoding="utf-8", errors="replace").strip()
        raise SystemExit("ffmpeg 无法开始录屏。\n" + detail)
    print("  警告：录屏仍由 Cursor 拉起，画面里 Cursor 可能被换成桌面背景", flush=True)
    return Recorder(pid=proc.pid, via="cursor", proc=proc, log_path=log_path)


def stop_ffmpeg(proc: Recorder | None, remux: bool = True) -> None:
    if proc is None:
        return
    if proc.proc is not None:
        child = proc.proc
        if child.poll() is None:
            if proc.via == "window":
                child.send_signal(signal.SIGINT)
            elif child.stdin:
                try:
                    child.stdin.write(b"q")
                    child.stdin.flush()
                except BrokenPipeError:
                    pass
                else:
                    child.send_signal(signal.SIGINT)
            else:
                child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.send_signal(signal.SIGINT)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
        if proc.via == "window" and proc.mov_path:
            for _ in range(24):
                if proc.mov_path.exists() and proc.mov_path.stat().st_size > 1000:
                    break
                time.sleep(0.25)
    else:
        try:
            os.kill(proc.pid, signal.SIGINT)
        except OSError:
            pass
        else:
            for _ in range(20):
                try:
                    os.kill(proc.pid, 0)
                    time.sleep(0.25)
                except OSError:
                    break
            else:
                try:
                    os.kill(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
    if remux and proc.via == "window" and proc.mov_path and proc.mp4_path and proc.mov_path.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(proc.mov_path),
                "-c",
                "copy",
                str(proc.mp4_path),
            ],
            check=False,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="往 Cursor 输入框逐字打字，并可选自动录屏。"
    )
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS, help="提示词文件，用单独一行的 --- 分隔")
    parser.add_argument("--project", type=Path, default=None, help="Cursor 工程目录，默认自动检测")
    parser.add_argument("--output", type=Path, default=None, help="录屏输出 mp4，默认 recordings/时间戳.mp4")
    parser.add_argument("--type-delay", type=float, default=0.045, help="每个字的间隔秒数")
    parser.add_argument(
        "--wait",
        choices=("auto", "hook", "key", "seconds"),
        default="auto",
        help="auto=钩子或回车；超时会等人介入而不是当结束；hook=等 Agent 结束；key=按回车；seconds=固定等待",
    )
    parser.add_argument("--timeout", type=float, default=180, help="每一轮最长等待秒数；到点后等人介入，不会自动当结束")
    parser.add_argument(
        "--expect",
        default="",
        help="演示完成标志，出现在命令输出里就算这个 case 完了，例如 hello world",
    )
    parser.add_argument("--countdown", type=int, default=3, help="开始前倒计时秒数")
    parser.add_argument("--mic", action="store_true", help="同时录麦克风")
    parser.add_argument("--no-record", action="store_true", help="只自动打字，不录屏")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要发送的提示词，不操作 Cursor")
    parser.set_defaults(new_window=True)
    parser.add_argument("--new-window", dest="new_window", action="store_true", help="新开 Cursor 窗口再输入（默认）")
    parser.add_argument("--same-window", dest="new_window", action="store_false", help="仍在当前窗口输入")
    parser.add_argument("--full-screen", action="store_true", help="录整块屏幕（默认只录新开的那个窗口）")
    parser.add_argument("--folder", type=Path, default=None, help="新窗口要打开的项目目录，默认 demo-playground")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = project_root(args.project)
    prompts = parse_prompts(args.prompts.expanduser().resolve())
    output = args.output or (root / "recordings" / time.strftime("%Y-%m-%d-%H%M%S") / "lesson.mp4")
    output = output.expanduser().resolve()

    print(f"工程：{root}")
    print(f"提示词：{len(prompts)} 条  ←  {args.prompts}")
    for i, prompt in enumerate(prompts, 1):
        preview = prompt.text.replace("\n", " / ")
        if len(preview) > 80:
            preview = preview[:77] + "..."
        extras = []
        if prompt.new_chat:
            extras.append("新对话")
        if prompt.wait_seconds is not None:
            extras.append(f"等{prompt.wait_seconds}s")
        suffix = f"  [{', '.join(extras)}]" if extras else ""
        print(f"  {i}. {preview}{suffix}")

    if args.dry_run:
        print("dry-run：不会打开 Cursor，也不会录屏。")
        return 0

    require_accessibility()
    tmp_dir = Path(f"/tmp/cursor-record-{os.getpid()}")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    recorder: Recorder | None = None

    def cleanup(_signum=None, _frame=None):
        stop_ffmpeg(recorder)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if _signum is not None:
            sys.exit(130)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        actor_dir = prepare_actor_project(args.folder or (root / "demo-playground"))
        if args.new_window:
            open_actor_with_folder(actor_dir)
        if not args.no_record:
            print(f"录屏：{output}")
            recorder = start_ffmpeg(output, args.mic, window_only=not args.full_screen)
        raise_actor_window()
        ensure_right_chat_panel()
        click_composer()
        if args.countdown > 0:
            for n in range(args.countdown, 0, -1):
                print(f"  {n}...", flush=True)
                time.sleep(1)

        for i, prompt in enumerate(prompts, 1):
            print(f"[{i}/{len(prompts)}] 输入中（目标：新 Cursor 窗口）…", flush=True)
            raise_actor_window()
            ensure_right_chat_panel()
            if prompt.new_chat:
                new_agent_chat()
            else:
                click_composer()
            if not cursor_frontmost():
                raise_actor_window()
                click_composer()
            clear_idle(actor_dir)
            sent_at = time.time()
            type_and_send(prompt.text, args.type_delay, tmp_dir)
            print(f"[{i}/{len(prompts)}] 已发送", flush=True)
            wait_timeout = prompt.wait_seconds if prompt.wait_seconds is not None else args.timeout
            wait_mode = "seconds" if prompt.wait_seconds is not None else args.wait
            expect = prompt.expect if prompt.expect else (args.expect.strip() or None)
            reason = wait_for_turn(actor_dir, wait_timeout, wait_mode, expect)
            print(f"[{i}/{len(prompts)}] 结束原因：{reason}（距发送 {time.time() - sent_at:.0f}s）")

        print("任务完成，关掉新窗口。")
        close_actor_window(actor_dir)
        time.sleep(1.2)
    finally:
        stop_ffmpeg(recorder)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if recorder is not None:
        print(f"录屏已保存：{output}")
        print("如果画面是黑的，去系统设置打开屏幕录制权限后重录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
