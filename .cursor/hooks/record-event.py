#!/usr/bin/env python3
"""Cursor hook: append live Agent activity for record_lesson.py to tail."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".cursor/record-state"
LOG = STATE / "live.log"
IDLE = STATE / "idle"


def clip(value: object, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def pick(data: dict, *keys: str) -> object:
    for key in keys:
        if key in data and data[key] not in (None, "", []):
            return data[key]
    return None


def summarize(event: str, data: dict) -> str:
    if event == "beforeShell":
        cmd = pick(data, "command", "command_prefix") or json.dumps(data, ensure_ascii=False)[:200]
        return f"开始跑命令：{clip(cmd)}"
    if event == "afterShell":
        cmd = pick(data, "command", "command_prefix") or ""
        code = pick(data, "exit_code", "exitCode", "status")
        out = pick(data, "output", "stdout", "result", "error")
        line = f"命令结束 exit={code if code is not None else '?'}：{clip(cmd)}"
        if out:
            line += f" | 输出：{clip(out, 360)}"
        return line
    if event == "afterEdit":
        path = pick(data, "file_path", "filePath", "path", "relative_path") or "文件"
        return f"改了文件：{clip(path, 160)}"
    if event == "afterReply":
        text = pick(data, "text", "response", "content", "message") or "Agent 输出了一段回复"
        return f"Agent 回复：{clip(text, 280)}"
    if event == "stop":
        return "这一轮结束（Agent 停了，可以发下一条）"
    return f"{event}：{clip(json.dumps(data, ensure_ascii=False), 280)}"


def response_for(event: str) -> dict:
    if event == "beforeShell":
        return {"permission": "allow"}
    return {}


def main() -> int:
    event = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {"_raw": raw[:800]}
    if not isinstance(data, dict):
        data = {"value": data}

    STATE.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%H:%M:%S')}  {summarize(event, data)}\n"
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line)
    if event == "stop":
        IDLE.write_text(str(int(time.time())), encoding="utf-8")
    sys.stdout.write(json.dumps(response_for(event), ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
