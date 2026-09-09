#!/usr/bin/env python3
"""Local text-to-image tool for course packs.

Tries AutoGLM when the desktop token service is up, otherwise Pollinations.
Always writes a local PNG/JPEG so the lesson can replay without calling the API again.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

APP_ID = "100003"
APP_KEY = "38d2391985e2369a5fb8227d8e6cd5e5"
AUTOGLM_URL = "https://autoglm-api.zhipuai.cn/agentdr/v1/assistant/skills/generate-image"
TOKEN_URL = "http://127.0.0.1:53699/get_token"


def _autoglm_token() -> str | None:
    try:
        with urllib.request.urlopen(TOKEN_URL, timeout=2) as resp:
            token = resp.read().decode("utf-8").strip()
    except Exception:
        return None
    if not token:
        return None
    if not token.lower().startswith("bearer "):
        token = f"Bearer {token}"
    return token


def _download(url: str, dest: Path, headers: dict | None = None) -> None:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "recording-kg/image-gen"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        dest.write_bytes(resp.read())


def generate_image(prompt: str, dest: Path, *, width: int = 1280, height: int = 720) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    token = _autoglm_token()
    if token:
        timestamp = str(int(time.time()))
        sign = hashlib.md5(f"{APP_ID}&{timestamp}&{APP_KEY}".encode()).hexdigest()
        payload = json.dumps({"text": prompt}).encode("utf-8")
        req = urllib.request.Request(
            AUTOGLM_URL,
            data=payload,
            headers={
                "Authorization": token,
                "Content-Type": "application/json",
                "X-Auth-Appid": APP_ID,
                "X-Auth-TimeStamp": timestamp,
                "X-Auth-Sign": sign,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        url = (result.get("data") or {}).get("image_url")
        if not url:
            raise RuntimeError(f"AutoGLM 没有返回图片：{result}")
        _download(url, dest)
        return {"ok": True, "backend": "autoglm", "path": str(dest), "prompt": prompt}

    q = urllib.parse.quote(prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{q}"
        f"?width={width}&height={height}&nologo=true"
    )
    _download(url, dest)
    if dest.stat().st_size < 8000:
        raise RuntimeError("文生图失败：返回的文件太小，不像一张图。")
    return {"ok": True, "backend": "pollinations", "path": str(dest), "prompt": prompt}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    info = generate_image(args.prompt, Path(args.output))
    print(json.dumps(info, ensure_ascii=False, indent=2))
