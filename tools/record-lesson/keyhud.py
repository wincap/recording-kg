#!/usr/bin/env python3
"""Start/stop the on-screen key HUD used while recording Cursor or Terminal."""

from __future__ import annotations

import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "keyhud.swift"
BIN = HERE / "bin" / "keyhud"

_proc: subprocess.Popen | None = None


def _build() -> Path:
    BIN.parent.mkdir(parents=True, exist_ok=True)
    if BIN.is_file() and SRC.is_file() and BIN.stat().st_mtime >= SRC.stat().st_mtime:
        return BIN
    result = subprocess.run(
        ["swiftc", "-O", "-o", str(BIN), str(SRC)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("按键浮层编译失败：" + (result.stderr or result.stdout)[:400])
    return BIN


def start() -> str:
    global _proc
    stop()
    path = _build()
    _proc = subprocess.Popen([str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"按键浮层 pid={_proc.pid}"


def stop() -> None:
    global _proc
    if _proc is None:
        return
    if _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            _proc.kill()
    _proc = None
