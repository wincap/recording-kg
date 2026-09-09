#!/usr/bin/env python3
"""Generate Xiaoxiao narration clips and stitch one lecture track."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUDIO = HERE / "audio"
EDGE = Path(__file__).resolve().parents[1] / "record-lesson" / "_edge_say.py"
PY = Path(__file__).resolve().parents[1] / "record-lesson" / ".venv" / "bin" / "python3"
VOICE = "zh-CN-XiaoxiaoNeural"

STEPS = [
    {
        "id": 0,
        "title": "立柱",
        "say": "这是一道立体几何题。图里是直三棱柱 ABC 到 A1B1C1。底面三角形 ABC 在 C 点是直角，而且 AC 等于 BC。我们先把这根柱子立起来，看清楚上下两个全等的直角三角形。",
    },
    {
        "id": 1,
        "title": "标 DE",
        "say": "题目说，D 是 AB 的中点，E 是对角线 AC1 的中点。把 D 和 E 连起来，就是这条金色的线段 DE。第一问要证它平行于侧面 BCC1B1，第二问要求它到这个侧面的距离。",
    },
    {
        "id": 2,
        "title": "建系",
        "say": "立体几何最稳的办法是建系。把直角顶点 C 当作原点。CA 沿着 x 轴，CB 沿着 y 轴，竖直棱 CC1 沿着 z 轴。这样三个互相垂直的方向都对齐了。",
    },
    {
        "id": 3,
        "title": "写出坐标",
        "say": "设 AC 等于 BC 等于 a，高 CC1 等于 h。那么 A 是 a,0,0，B 是 0,a,0，C1 是 0,0,h。D 是 AB 中点，坐标就是 a 比 2，a 比 2，0。E 是 AC1 中点，坐标是 a 比 2，0，h 比 2。",
    },
    {
        "id": 4,
        "title": "证平行",
        "say": "向量 DE 等于 E 减 D，算出来是 0，负的 a 比 2，h 比 2。侧面 BCC1B1 正好是坐标平面 x 等于 0，法向量是 1,0,0。DE 点乘法向量等于 0，所以 DE 平行于这个面。D 的横坐标不是 0，线又不在面上。第一问得证。",
    },
    {
        "id": 5,
        "title": "线面角",
        "say": "第二问给了高 CC1 等于 2，并且 DE 与另一个侧面 ACC1A1 成 45 度。ACC1A1 是 y 等于 0，法向量是 0,1,0。线面角的正弦，等于方向向量点乘法向量的绝对值，再除以模长。代入 45 度，得到根号 2 比 2，等于 a 比根号 a 方加 4。",
    },
    {
        "id": 6,
        "title": "求出距离",
        "say": "两边平方，二分之一等于 a 方比 a 方加 4，解出 a 等于 2。DE 已经平行于青色那个侧面，平行线到平面的距离处处相等，就等于点 D 到 x 等于 0 的距离，也就是 a 比 2，等于 1。所以这道题的答案是 1。",
    },
]


def speak(text: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if PY.is_file() and EDGE.is_file():
        result = subprocess.run(
            [str(PY), str(EDGE), text, VOICE, "+0%", "+0Hz", str(dest)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and dest.is_file() and dest.stat().st_size > 200:
            return
    aiff = dest.with_suffix(".aiff")
    subprocess.run(["say", "-v", "Tingting", "-o", str(aiff), text], check=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(aiff), str(dest)],
        check=True,
    )
    aiff.unlink(missing_ok=True)


def duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def main() -> None:
    AUDIO.mkdir(parents=True, exist_ok=True)
    cues = []
    t = 0.0
    parts: list[Path] = []
    for item in STEPS:
        clip = AUDIO / f"step-{item['id']:02d}.mp3"
        print(f"配音 {item['id']} {item['title']}", flush=True)
        speak(item["say"], clip)
        dur = duration(clip)
        cues.append(
            {
                "id": item["id"],
                "title": item["title"],
                "say": item["say"],
                "start": round(t, 2),
                "end": round(t + dur, 2),
                "src": f"audio/step-{item['id']:02d}.mp3",
            }
        )
        t += dur
        parts.append(clip)
    work = Path(tempfile.mkdtemp(prefix="prism-vo-"))
    try:
        listing = work / "list.txt"
        listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
        lecture = AUDIO / "lecture.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c:a",
                "libmp3lame",
                "-q:a",
                "4",
                str(lecture),
            ],
            check=True,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    payload = {"duration": round(t, 2), "steps": cues}
    (AUDIO / "cues.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (HERE / "cues.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ok", lecture, "总长", round(t, 1), "秒")


if __name__ == "__main__":
    main()
