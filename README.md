# recording-kg · Lesson Studio

**Language:** [English](./README.md) · [中文](./README.zh-CN.md)

> **Status: Early Preview**  
> APIs, folder conventions, and the student UI may still change. Fine for trying out and feedback — not production-stable yet.  
> Issues / Discussions welcome.

A local **lesson control room + timeline student player** (source available; **free for non-commercial use, commercial use needs a license**).  
Built for programming courses: slide voiceover, Cursor hands-on capture, playback driven by `timeline.json`, and Q&A that takes over the main stage.

Repo: https://github.com/wincap/recording-kg

> **Published:** code and docs.  
> **Not published:** API keys, local `recordings/` media, student notes and uploads (gitignored by default).  
> **Commercial use:** recording-as-a-service, private deploy, resale — please open an [Issue](https://github.com/wincap/recording-kg/issues) for permission.

---

## Demo

| Step | What you see |
|------|----------------|
| 1 | Student player: slides + voiceover |
| 2 | Chapter 3 timeline: PPT → handoff → hands-on → recap |
| 3 | Switch to the Cursor **screen recording** |
| 4 | **Tutor main stage**: student code / AI notes side by side |

**Fullscreen Cursor take (chapter 3, ~32s):**

![Cursor fullscreen demo](docs/demo-cursor.gif)

Video: [docs/demo-cursor.mp4](docs/demo-cursor.mp4)

**Student player window capture (ch.3: PPT → handoff → hands-on → recap, ~40s):**

![Student watch UI](docs/demo-watch.gif)

Video: [docs/demo-watch.mp4](docs/demo-watch.mp4)

Try locally:

1. `python3 tools/record-lesson/studio_server.py`
2. Open http://127.0.0.1:8765/watch
3. Pick the FastAPI REST course → chapter 3 → jump to **hands-on**; upload code to enter tutor mode

---

## Problems it solves

| Pain | Common approach | This project |
|------|-----------------|--------------|
| One PPT change → re-bake the whole lesson | One long `lesson.mp4` | **Decoupled assets**: slides + VO + capture, driven by a timeline |
| Q&A stuck in a side chat | Main view stays on the old video | **Tutor on the main stage**: code/screenshot + AI notes |
| Voice locked to picture | Rewording means re-recording | Swap `*.m4a` / `slides/` — no full re-bake |
| Keys / media leaked into git | `.env` and big videos in the repo | `.env` and `recordings/` ignored by default |

---

## Capabilities

1. **Studio (record)** — outline, PPT/voiceover, Cursor capture, write `timeline.json`
2. **Student player “课场” (watch)** — read the timeline; switch slides / video / audio over time
3. **Voice / upload tutoring** — assets on disk, DeepSeek notes, main canvas enters tutor mode, one-click back to the lesson
4. **Learning log** — `student-notes.json` + `student-assets/` (local, not in git)

---

## Layout (short)

```text
recording-kg/
├── tools/record-lesson/     # studio + student player (main entry)
│   ├── studio_server.py     # http://127.0.0.1:8765
│   ├── web/index.html       # studio
│   ├── web/watch.html       # student player
│   ├── course.py / voiceover.py / student_coach.py
│   └── ...
├── tools/ppt-studio/        # PPT / DeepSeek slide drawing
│   └── .env.example         # example only — real key stays in local .env
├── recordings/              # finished media (gitignored)
├── README.md                # English
└── README.zh-CN.md          # 中文
```

---

## Security: keep keys out of git

**Already set up:**

- `.gitignore` covers `.env`, `**/.env`, `recordings/`, `student-notes.json`, `student-assets/`, `.venv`, etc.
- The repo only has `.env.example` (empty template) — **no real keys**

**You should:**

1. Configure locally (do not commit):

```bash
cp tools/ppt-studio/.env.example tools/ppt-studio/.env
# edit .env → DEEPSEEK_API_KEY=sk-...
```

2. Check before push:

```bash
git status
git grep -n 'sk-' -- ':!**/.env.example'   # should find no real key
```

3. If a key ever leaked in chat/screenshots, rotate it in the [DeepSeek console](https://platform.deepseek.com/), then put the new one only in local `.env`.

---

## Requirements

- macOS (screen / window control)
- Python 3.10+
- ffmpeg (duration probes, audio extract, etc.)
- Optional: `tesseract` (OCR for student screenshots)
- Optional: Edge TTS / system `say` (voiceover)

---

## Deploy / local run

### 1. Clone

```bash
git clone https://github.com/wincap/recording-kg.git
cd recording-kg
```

### 2. Python deps

```bash
cd tools/record-lesson
python3 -m venv .venv
source .venv/bin/activate
# install whatever your machine needs to import the modules
# if requirements.txt exists: pip install -r requirements.txt
```

### 3. DeepSeek (tutor / slide drawing)

```bash
cp ../ppt-studio/.env.example ../ppt-studio/.env
# set DEEPSEEK_API_KEY
```

### 4. Start studio + player

```bash
cd tools/record-lesson
python3 studio_server.py
```

| Page | URL |
|------|-----|
| Studio (record) | http://127.0.0.1:8765/ |
| Student (watch) | http://127.0.0.1:8765/watch |

Media lives under `recordings/courses/<course>/ch-XX/`.  
A fresh clone has no `recordings/` — produce chapters in the studio or copy a backup (never commit a real `.env`).

### 5. Smoke check

```bash
curl -sS http://127.0.0.1:8765/api/courses
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/watch
```

### 6. Tests (optional)

```bash
cd tools/record-lesson
source .venv/bin/activate
python -m unittest test_lesson_tools.py -v
```

---

## Timeline format

Example `timeline.json` in a chapter folder:

```json
{
  "kind": "play",
  "clips": [
    {
      "id": "intro",
      "kind": "slides",
      "title": "PPT",
      "audio": "intro.m4a",
      "dur": 71.0,
      "pages": [{ "src": "slides/01.png", "at": 0, "dur": 14 }]
    },
    {
      "id": "cursor",
      "kind": "video",
      "title": "Hands-on",
      "src": "cursor.mp4",
      "dur": 33.0
    }
  ]
}
```

- `slides` — slides + voiceover  
- `video` — screen capture  
- `audio` — voice only  

The player advances on a global clock. In tutor mode it uses a **separate scrubber**; the lesson scrubber pauses.

---

## Why it’s useful

- **Maintainable** — change a page, a line of VO, or a take independently  
- **Teachable** — concept PPT → handoff → Cursor → recap  
- **Interactive** — upload code/screenshot → tutor stage  
- **Private by default** — local HTTP, data stays on disk  
- **Safe defaults** — secrets and big media stay out of git  

---

## Known limits (Early Preview)

- Early preview: breaking API/UI changes possible  
- Capture features are **macOS**-first  
- Tutor model is **DeepSeek** (bring your own API key)  
- Screenshot OCR needs local `tesseract`; without it, tutoring still works but vision is weaker  
- `recordings/` is large — back up to cloud storage, don’t put it in git  

---

## Next improvements

Near-term (product completeness):

1. **Demo the tutor main stage** — record upload → AI notes → side-by-side tutor UI (missing from current watch demo)
2. **More reliable Cursor takes** — wait for full agent runs; avoid stopping mid-thought; clearer FastAPI/other-framework completion checks
3. **One-click student demo** — scripted watch recording (`chapter` / `t` / `play` URL params) so README demos stay fresh
4. **Install story** — `requirements.txt` / lockfile and a short “first successful run” checklist

Mid-term (scale & quality):

5. **Knowledge / retrieval** — optional vector store (e.g. Chroma) over lesson notes + student uploads for better tutoring
6. **Multi-model engines** — pluggable LLM beyond DeepSeek; clearer engine status in studio
7. **Cross-platform capture** — reduce macOS-only assumptions for window/screen recording
8. **Export packs** — publish a chapter as a portable folder (timeline + assets) without the whole `recordings/` tree

Later (if demand shows up):

9. **Commercial license path** — simple Issue / email flow for paid deploy & white-label
10. **Hosted preview** — optional read-only student player for public sample courses (no secrets, no private notes)

Feedback and PRs welcome via [Issues](https://github.com/wincap/recording-kg/issues).

---

## License

[PolyForm Noncommercial 1.0.0](./LICENSE) — **free for personal study, research, and other non-commercial use**; **any commercial use** (paid service, closed product, commercial training, etc.) **needs a separate license**.  
Do not commit `.env`, finished media, or student privacy into a public repo.
