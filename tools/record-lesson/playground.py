#!/usr/bin/env python3
"""Snapshot and restore a lesson demo folder so each take starts clean."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SKIP = {".git", "node_modules", "__pycache__", ".venv", ".lesson-snapshots", ".DS_Store"}

PACKAGES = {
    "fastapi": ("fastapi", "httpx"),
    "langchain": ("langchain-core",),
    "lcel": ("langchain-core",),
}


def snap_root(folder: Path) -> Path:
    return folder / ".lesson-snapshots"


def list_snaps(folder: Path) -> list[str]:
    root = snap_root(folder)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def snapshot(folder: Path, name: str = "baseline") -> Path:
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise RuntimeError(f"目录不存在：{folder}")
    dest = snap_root(folder) / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for item in folder.iterdir():
        if item.name in SKIP or item.name == ".lesson-snapshots":
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=shutil.ignore_patterns(*SKIP))
        else:
            shutil.copy2(item, target)
    return dest


def restore(folder: Path, name: str = "baseline") -> Path:
    folder = folder.expanduser().resolve()
    src = snap_root(folder) / name
    if not src.is_dir():
        raise RuntimeError(f"没有叫「{name}」的快照。先点存快照。")
    for item in folder.iterdir():
        if item.name in SKIP or item.name == ".lesson-snapshots":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in src.iterdir():
        target = folder / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    return src


def ensure_baseline(folder: Path) -> Path:
    if "baseline" not in list_snaps(folder):
        return snapshot(folder, "baseline")
    return snap_root(folder) / "baseline"


def actor_python(folder: Path) -> Path:
    folder = Path(folder).expanduser().resolve()
    py = folder / ".venv" / "bin" / "python3"
    if py.is_file():
        return py
    return Path(shutil.which("python3") or "python3")


def ensure_packages(folder: Path, blob: str = "", log=None) -> Path:
    """Install lesson runtime into the actor venv before recording. Not on camera."""
    folder = Path(folder).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    emit = log or (lambda m: None)
    venv_py = folder / ".venv" / "bin" / "python3"
    if not venv_py.is_file():
        emit("演示目录还没有 .venv，正在建…")
        subprocess.run(["python3", "-m", "venv", str(folder / ".venv")], check=True)
    wanted: list[str] = []
    text = str(blob or "").lower()
    for key, pkgs in PACKAGES.items():
        if key in text:
            wanted.extend(pkgs)
    if not wanted:
        return actor_python(folder)
    need: list[str] = []
    for pkg in wanted:
        mod = pkg.replace("-", "_")
        probe = subprocess.run(
            [str(venv_py), "-c", f"import {mod}"],
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            need.append(pkg)
    if need:
        emit("课前装好：" + "、".join(need))
        pip = subprocess.run(
            [str(venv_py), "-m", "pip", "install", "-q", *need],
            capture_output=True,
            text=True,
        )
        if pip.returncode != 0:
            raise RuntimeError((pip.stderr or pip.stdout or "装包失败").strip()[-400:])
    return actor_python(folder)
