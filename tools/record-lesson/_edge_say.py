#!/usr/bin/env python3
"""Speak one line with Microsoft neural TTS (edge-tts)."""

from __future__ import annotations

import asyncio
import sys

import edge_tts


def main() -> None:
    if len(sys.argv) < 6:
        raise SystemExit("usage: _edge_say.py TEXT VOICE RATE PITCH OUTPUT")
    text, voice, rate, pitch, dest = sys.argv[1:6]

    async def run() -> None:
        talk = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await talk.save(dest)

    asyncio.run(run())


if __name__ == "__main__":
    main()
