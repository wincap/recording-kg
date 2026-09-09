#!/usr/bin/env python3
"""Turn a one-line topic into audience, objective, outline, and a recordable rundown."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import assemble as asm
import course as course_mod
import lesson_tools
import web_demo as wd

ENV_FILE = Path(__file__).resolve().parents[1] / "ppt-studio" / ".env"
ROOT = Path(__file__).resolve().parents[2]
ENGINE_FILE = Path(__file__).resolve().parent / ".llm-engine"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
ENGINES = ("deepseek",)
_engine = "deepseek"

SYSTEM = """你是录课编剧。用户只给主题，你产出一集可录的课。只输出一个 JSON 对象，不要 markdown。

规则：
- 主题含糊时，选定一个具体开源项目（LangChain、FastAPI、Vite、Vue、Redis）。
- 不要编造星标、下载量。网址只用真实官网或 GitHub 首页。
- 一集只教一件能验收的事。听众写「已经会什么 / 看完能做什么」。
- 不要写「几分钟」。时长由后面的分镜和口播决定。
- 不要 pip install、不要 API Key、不要 ChatOpenAI、不要 clone。那些会把课卡死。
- 框架/库（LangChain、FastAPI、Vite、Vue）的 outline 必须有一拍是「看 Cursor 写出并跑通一段最小代码」，不是只逛官网，也不是终端 cat 现成文件。
- 纯产品介绍（例如 Redis 是什么）可以只逛官网。
- 口播 narration 6 句左右，每句 40～90 字，能对着画面念。框架课最后两句对着代码和运行结果。
- rundown / web_script / term_script 可以写，本地会按主题重配工具，不必纠结 look 秒数。
- expect 只要 2～12 个字。
- demo_files 必须是 [{path, content}]，content 是完整可运行源码，必须像真框架，禁止用标准库函数假装成框架。
- LangChain / LCEL / 链式调用：必须 from langchain_core.runnables import RunnableLambda（或 RunnablePassthrough），用 | 组链，chain.invoke(...) 跑通。禁止手写 def chain()。没有密钥就 RunnableLambda 当假模型。
- prompt 是发给 Cursor 的完整中文任务，必须点名要用的 API（例如 LCEL 的 Runnable 和 |）。禁止写「只用标准库」「不要 import 第三方」这种套话。

- 本集只备用户指定的那一章。title / outline / narration / demo 只针对这一章，不要把整门课塞进一集。

JSON 字段：
audience, title, objective, expect, homepage, outline, prompt, demo_files, web_script, term_script, rundown, narration
outline 是 [{beat, seconds}] 数组。
demo_files 是 [{path, content}] 数组。
"""


def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def agent_bin() -> str:
    load_env()
    for cand in (
        (os.environ.get("CURSOR_AGENT_BIN") or "").strip(),
        shutil.which("agent") or "",
        str(Path.home() / ".local/bin/agent"),
    ):
        if cand and Path(cand).is_file() and os.access(cand, os.X_OK):
            return cand
    raise RuntimeError("找不到 Cursor Agent。先装 CLI（cursor.com/docs/cli），或设置 CURSOR_AGENT_BIN。")


def deepseek_ready() -> bool:
    load_env()
    return bool((os.environ.get("DEEPSEEK_API_KEY") or "").strip())


def cursor_ready() -> bool:
    try:
        agent_bin()
        return True
    except Exception:
        return False


def _normalize_engine(name: str | None) -> str:
    raw = str(name or "").strip().lower()
    if raw in {"cursor-agent", "cursor_agent", "agent", "cursor"}:
        return "deepseek"
    if raw in ENGINES or raw == "deepseek":
        return "deepseek"
    return ""


def current_engine() -> str:
    global _engine
    _engine = "deepseek"
    return _engine


def set_engine(name: str) -> dict:
    global _engine
    picked = _normalize_engine(name) or "deepseek"
    if picked != "deepseek":
        raise RuntimeError("问答模型只用 DeepSeek。")
    if not deepseek_ready():
        raise RuntimeError("还没有 DeepSeek Key。写在 tools/ppt-studio/.env 的 DEEPSEEK_API_KEY。")
    _engine = "deepseek"
    ENGINE_FILE.write_text("deepseek\n", encoding="utf-8")
    return engine_status()


def list_engines() -> list[dict]:
    return [
        {"id": "deepseek", "label": "DeepSeek", "ok": deepseek_ready()},
    ]


def engine_label() -> str:
    return "DeepSeek" if deepseek_ready() else "DeepSeek（无 Key）"


def engine_status() -> dict:
    return {"engine": current_engine(), "engine_label": engine_label(), "engines": list_engines()}


def render_prompt(messages: list[dict], *, json_mode: bool = False) -> str:
    parts = ["你是录课导播台里的问答助手。只回答，不要改仓库、不要跑命令、不要调用会改文件的工具。"]
    if json_mode:
        parts.append("最终回复必须是一个 JSON 对象。不要 markdown，不要前言后记。")
    for item in messages or []:
        role = str(item.get("role") or "user")
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        label = {"system": "系统设定", "user": "用户", "assistant": "上一轮助手"}.get(role, role)
        parts.append(f"【{label}】\n{content}")
    return "\n\n".join(parts)


def agent_cmd(prompt: str) -> list[str]:
    load_env()
    cmd = [
        agent_bin(),
        "--print",
        "--mode",
        "ask",
        "--trust",
        "--sandbox",
        "enabled",
        "--workspace",
        str(ROOT),
        "--output-format",
        "text",
    ]
    model = (os.environ.get("CURSOR_MODEL") or os.environ.get("CURSOR_AGENT_MODEL") or "").strip()
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)
    return cmd


def chat_cursor(messages: list[dict], *, json_mode: bool = False) -> str:
    prompt = render_prompt(messages, json_mode=json_mode)
    try:
        result = subprocess.run(
            agent_cmd(prompt),
            capture_output=True,
            text=True,
            timeout=240,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Cursor Agent 超时。再发一次，或检查 agent 是否卡在登录。") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("找不到 Cursor Agent。先装 CLI，或设置 CURSOR_AGENT_BIN。") from exc
    text = ANSI.sub("", (result.stdout or "")).strip()
    err = ANSI.sub("", (result.stderr or "")).strip()
    if result.returncode != 0:
        hint = err or text
        if re.search(r"not logged|unauthor|401|login", hint, re.I):
            raise RuntimeError("Cursor Agent 未登录。终端执行：agent login")
        raise RuntimeError("Cursor Agent 请求失败：" + hint[-400:])
    if not text:
        raise RuntimeError("Cursor Agent 空回复" + (("：" + err[-200:]) if err else ""))
    return text


def chat_deepseek(messages: list[dict], *, max_tokens: int = 2500, json_mode: bool = False) -> str:
    load_env()
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("还没有 DeepSeek Key。写在 tools/ppt-studio/.env 的 DEEPSEEK_API_KEY。")
    base = (os.environ.get("DEEPSEEK_BASE") or "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.4 if json_mode else 0.5,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as handle:
        handle.write(payload)
        body_path = handle.name
    try:
        result = subprocess.run(
            [
                "curl",
                "-sS",
                "--max-time",
                "180",
                "-H",
                f"Authorization: Bearer {key}",
                "-H",
                "Content-Type: application/json",
                "--data-binary",
                f"@{body_path}",
                f"{base}/chat/completions",
            ],
            capture_output=True,
            text=True,
        )
    finally:
        Path(body_path).unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError("DeepSeek 请求失败：" + (result.stderr or result.stdout)[-400:])
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("DeepSeek 返回不是 JSON：" + result.stdout[:400]) from exc
    if data.get("error"):
        raise RuntimeError(str(data["error"])[:400])
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError("DeepSeek 返回异常：" + json.dumps(data, ensure_ascii=False)[:400]) from exc
    if not str(content or "").strip():
        raise RuntimeError("DeepSeek 空回复")
    return content


def chat(messages: list[dict], *, max_tokens: int = 2500, json_mode: bool = False) -> str:
    return chat_deepseek(messages, max_tokens=max_tokens, json_mode=json_mode)


def chat_json(system: str, messages: list[dict], *, max_tokens: int = 1600, loose: bool = False) -> dict:
    """Ask for JSON. json_mode first; empty/parse failure retries as plain text."""
    combined = [{"role": "system", "content": system}, *messages]
    last_err = "模型没有返回 JSON"
    for json_mode in (True, False):
        try:
            raw = chat(combined, max_tokens=max_tokens, json_mode=json_mode)
        except Exception as exc:
            last_err = str(exc)
            continue
        try:
            return extract_json(raw)
        except Exception as exc:
            last_err = str(exc)
            if loose and str(raw or "").strip():
                return {
                    "say": str(raw).strip(),
                    "chips": [],
                    "ready": False,
                    "action": "",
                    "brief": {},
                    "patch": {},
                }
            continue
    raise RuntimeError(last_err)


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        raise RuntimeError("模型没有返回 JSON 计划")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise RuntimeError("模型 JSON 读不出来") from exc
    if not isinstance(obj, dict):
        raise RuntimeError("模型没有返回 JSON 计划")
    return obj


OPENING = {
    "say": "这一门课想讲什么？我说课名，先出细大纲：每章只教一件事，带要点和验收。你改完点确认大纲，我就按章出片。",
    "chips": ["LangChain", "FastAPI", "帮我定"],
    "ready": False,
    "brief": {"topic": "", "audience": "", "goal": "", "minutes": "", "note": ""},
}

CHAT_SYSTEM = """你是录课导播。中文口语，短。一次只问一件。只输出一个 JSON 对象。
流程：细大纲（每章要点+验收）→ 你改 → 确认后自动出片 → 学生端。
还没大纲：主题清楚或「帮我定」：brief.topic 填具体项目（默认 LangChain），ready=true。
已有大纲：改代码/LCEL 则 action=rewrite_demo；ready 默认 false。
{"say":"2到5句","chips":["短句"],"ready":false,"action":"","brief":{"topic":"","audience":"","goal":"","minutes":"","note":""},"patch":{}}
action 只能是空或 rundown/web/term/cursor/pack/regenerate/rewrite_demo。
"""


def brief_note(brief: dict) -> str:
    brief = brief or {}
    parts = []
    for key, label in (
        ("audience", "听众"),
        ("goal", "目标"),
        ("minutes", "时长"),
        ("note", "补充"),
    ):
        value = str(brief.get(key) or "").strip()
        if value:
            parts.append(f"{label}：{value}")
    return "\n".join(parts)


def detect_action(text: str) -> str:
    raw = (text or "").strip()
    table = {
        "按这个录": "rundown",
        "按分镜录": "rundown",
        "开始录": "rundown",
        "开录": "rundown",
        "就这样录": "rundown",
        "可以录了": "rundown",
        "录吧": "rundown",
        "开始录制": "rundown",
        "开始录制吧": "rundown",
        "录制吧": "rundown",
        "只录网页": "web",
        "录网页": "web",
        "只录终端": "term",
        "录终端": "term",
        "只录 cursor": "cursor",
        "录 agent": "cursor",
        "出讲义": "pack",
        "出讲义作业": "pack",
    }
    hit = table.get(raw.lower()) or table.get(raw)
    if hit:
        return hit
    if re.search(r"(不|别|不要).{0,4}录", raw):
        return ""
    if re.search(r"(只录|录)网页", raw):
        return "web"
    if re.search(r"(只录|录)终端", raw):
        return "term"
    if re.search(r"(开始|按这个|就这样|可以|好了|现在).{0,8}(录制|开录|录)", raw):
        return "rundown"
    if re.fullmatch(r"录(制|吧|一下)?", raw):
        return "rundown"
    return ""


VAGUE = {"开源项目", "前端", "后端", "ai", "人工智能", "教程", "编程", "python", "帮我想", "帮我想一个"}
DECIDE = {
    "帮我定",
    "你定",
    "随便",
    "帮我想",
    "帮我想一个",
    "就这样",
    "定一个",
    "你来定",
    "你看着办",
    "都可以",
}
NAMED = (
    ("langchain", "LangChain"),
    ("fastapi", "FastAPI"),
    ("vite", "Vite"),
    ("vue", "Vue"),
    ("redis", "Redis"),
)


def named_project(text: str) -> str:
    low = (text or "").lower()
    for key, title in NAMED:
        if key in low:
            return title
    return ""


def last_user_text(history: list[dict] | None) -> str:
    for item in reversed(history or []):
        if (item.get("role") or "") == "user":
            return str(item.get("text") or item.get("say") or "").strip()
    return ""


def wants_decide(text: str) -> bool:
    compact = re.sub(r"\s+", "", (text or "").strip()).lower()
    if compact in DECIDE:
        return True
    return bool(re.search(r"(帮我定|帮我想一个|你来定|你看着办)", text or ""))


def resolve_topic(text: str, brief: dict | None = None) -> str:
    named = named_project(text)
    if named:
        return named
    topic = str((brief or {}).get("topic") or "").strip()
    if topic and topic not in VAGUE and topic not in DECIDE and not wants_decide(topic):
        return topic
    raw = (text or "").strip()
    if wants_decide(raw) or raw in VAGUE or raw in DECIDE:
        return "LangChain"
    if raw and raw not in VAGUE:
        return raw
    return "LangChain"


def should_emit_outline(text: str, brief: dict | None = None) -> bool:
    if named_project(text) or wants_decide(text):
        return True
    topic = str((brief or {}).get("topic") or "").strip()
    return bool(topic) and wants_decide(text)


REWRITE_SYSTEM = """你按老师一句话，重写这一集发给 Cursor 的演示代码和提示词。
只输出 JSON，不要 markdown。

{
  "say": "口语，说明这次代码跟上次有什么不同",
  "path": "lcel_chain.py",
  "content": "完整可运行源码",
  "prompt": "发给 Cursor 的完整中文任务，必须描述要写的 API，禁止套「标准库最小文件」",
  "expect": "问："
}

硬规则：
- 必须落实老师这句要求，不能复用上一份代码。
- 老师说 LCEL / 管道 / Runnable：必须 from langchain_core.runnables import RunnableLambda 或 RunnablePassthrough，用 | 组链，chain.invoke(...) 跑通。禁止手写 def chain()。
- 禁止 pip install、禁止 API Key、禁止 ChatOpenAI、禁止访问网络。
- 没有密钥时用 RunnableLambda 当假模型，但写法必须是真 LCEL。
- 跑起来要打印一行「问：」一行「答：」。
"""


def rewrite_demo(plan: dict, instruction: str) -> dict:
    plan = plan or {}
    demo = ((plan.get("demo_files") or [{}])[0]) or {}
    payload = {
        "instruction": (instruction or "").strip(),
        "title": plan.get("title"),
        "objective": plan.get("objective"),
        "homepage": plan.get("homepage"),
        "current_path": demo.get("path"),
        "current_content": str(demo.get("content") or "")[:2500],
        "current_prompt": str(plan.get("prompt") or "")[:800],
    }
    raw = chat_json(REWRITE_SYSTEM, [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], max_tokens=2500)
    data = raw
    path = str(data.get("path") or demo.get("path") or "lcel_chain.py").strip().lstrip("./")
    content = str(data.get("content") or "").strip()
    prompt = str(data.get("prompt") or "").strip()
    if not content:
        raise RuntimeError("模型没写出新代码，再说一次要改成什么样。")
    if not prompt:
        prompt = (
            f"在当前项目只新建 {path}。\n"
            f"{instruction}\n"
            "不要 pip install，不要密钥，不要 ChatOpenAI。\n"
            f"保存后执行 python3 {path}，把终端完整输出发回来。\n"
            "打印两行，分别以「问：」「答：」开头。"
        )
    cleaned = lesson_tools.safe_demo_files([{"path": path, "content": content}])
    if not cleaned:
        raise RuntimeError("新代码里有密钥、装包或 ChatOpenAI，我没收下。换一种不调网的写法。")
    return {
        "say": str(data.get("say") or "代码已按你的要求重写。").strip(),
        "path": cleaned[0]["path"],
        "content": cleaned[0]["content"],
        "prompt": prompt,
        "expect": str(data.get("expect") or plan.get("expect") or "问：")[:24] or "问：",
    }


def interview(history: list[dict], brief: dict | None = None, plan: dict | None = None) -> dict:
    brief = dict(OPENING["brief"] if not brief else brief)
    user = last_user_text(history)
    if not plan:
        if should_emit_outline(user, brief):
            topic = resolve_topic(user, brief)
            next_brief = dict(brief)
            next_brief["topic"] = topic
            return {
                "say": f"就讲 {topic}，我去拆课纲，稍等。",
                "chips": [],
                "ready": True,
                "action": "",
                "brief": next_brief,
                "patch": {},
            }
        if (user or "").strip() in VAGUE:
            return {
                "say": "太大了。点一个具体项目，或点「帮我定」我按 LangChain 拆课纲。",
                "chips": ["LangChain", "FastAPI", "帮我定"],
                "ready": False,
                "action": "",
                "brief": brief,
                "patch": {},
            }
    lines = []
    if any(brief.values()):
        lines.append("当前 brief：" + json.dumps(brief, ensure_ascii=False))
    if plan:
        slim = {
            "title": plan.get("title"),
            "audience": plan.get("audience"),
            "objective": plan.get("objective"),
            "homepage": plan.get("homepage"),
            "outline": [b.get("beat") for b in (plan.get("outline") or [])[:6] if isinstance(b, dict)],
            "tools": {
                "use_web": (plan.get("tools") or {}).get("use_web"),
                "use_cursor": (plan.get("tools") or {}).get("use_cursor"),
                "use_term": (plan.get("tools") or {}).get("use_term"),
                "code_via": (plan.get("tools") or {}).get("code_via"),
            },
            "prompt": (plan.get("prompt") or "")[:240],
            "demo": ((plan.get("demo_files") or [{}])[0] or {}).get("path"),
            "demo_head": str(((plan.get("demo_files") or [{}])[0] or {}).get("content") or "")[:240],
            "rundown": (plan.get("rundown") or "")[:240],
            "course_title": plan.get("course_title"),
            "chapter": plan.get("chapter"),
            "chapters": [
                f"{c.get('n')}.{c.get('title')}"
                for c in (plan.get("chapters") or [])[:10]
                if isinstance(c, dict)
            ],
        }
        lines.append("已有大纲（机器会按这个录）：" + json.dumps(slim, ensure_ascii=False))
        lines.append("有大纲时 ready 默认 false。用户要重写代码或改成 LCEL 时 action=rewrite_demo，不要只口头答应。")
    else:
        lines.append("还没有大纲。")
    transcript = []
    for item in history[-14:]:
        role = item.get("role") or "user"
        if role not in {"user", "assistant"}:
            continue
        text = str(item.get("text") or item.get("say") or "").strip()
        if text:
            transcript.append({"role": role, "content": text})
    if not transcript:
        return {**OPENING, "brief": brief, "patch": {}}
    context = "\n".join(lines)
    try:
        data = chat_json(
            CHAT_SYSTEM,
            [{"role": "user", "content": context}, *transcript],
            max_tokens=900,
            loose=True,
        )
    except Exception as exc:
        return {
            "say": "模型这轮没给出能用的回复（" + str(exc)[:80] + "）。再说一次，或点「重写代码」。",
            "chips": ["重写代码", "怪怪的是标题", "你定"],
            "ready": False,
            "action": "",
            "brief": brief,
            "patch": {},
        }
    next_brief = dict(brief)
    incoming = data.get("brief") if isinstance(data.get("brief"), dict) else {}
    for key in ("topic", "audience", "goal", "minutes", "note"):
        value = str(incoming.get(key) or "").strip()
        if value:
            next_brief[key] = value
    chips = [str(x).strip() for x in (data.get("chips") or []) if str(x).strip()][:4]
    action = str(data.get("action") or "").strip().lower()
    if action not in {"", "rundown", "web", "term", "cursor", "pack", "regenerate", "rewrite_demo"}:
        action = ""
    patch_in = data.get("patch") if isinstance(data.get("patch"), dict) else {}
    patch = {}
    for key in (
        "title",
        "audience",
        "objective",
        "homepage",
        "prompt",
        "expect",
        "note",
        "demo_path",
        "demo_content",
        "rundown",
        "narration",
        "outline",
        "chapters",
        "course_title",
        "chapter",
    ):
        value = patch_in.get(key)
        if value is None:
            continue
        if isinstance(value, (list, dict)):
            patch[key] = value
            continue
        text_val = str(value).strip()
        if text_val:
            patch[key] = text_val
    ready = bool(data.get("ready"))
    if plan and ready and action not in {"regenerate", "rundown"}:
        ready = False
    if not plan and should_emit_outline(user, next_brief):
        ready = True
        next_brief["topic"] = resolve_topic(user, next_brief)
    if not plan and (wants_decide(next_brief.get("topic") or "") or (next_brief.get("topic") or "") in VAGUE):
        if ready:
            next_brief["topic"] = "LangChain"
    return {
        "say": str(data.get("say") or "接着说一句就行。").strip(),
        "chips": chips or (["确认这样录", "重写代码", "重写大纲"] if plan else ["LangChain", "FastAPI", "帮我定"]),
        "ready": ready,
        "action": action,
        "brief": next_brief,
        "patch": patch,
    }


COURSE_SYSTEM = """你是录课策划。把主题拆成学生能跟着学完的细课纲。只输出一个 JSON 对象。
{"course_title":"具体课名","audience":"已经会什么的人","chapter":1,"chapters":[{"n":1,"title":"只讲一件具体的事","goal":"看完能当场做出来","kind":"ppt","need_code":false,"beats":["要点1","要点2","要点3"]}]}
规则：
- 框架课 6～8 章，小技能 4～6 章。禁止只出 1 章。
- 课名要具体。禁止「从入门到精通」「全面详解」这种空课名。
- 一章只教一件能验收的事。禁止把 Model/Prompt/Chain/Agent/Memory 这类一串概念塞进同一章。
- 标题禁止含：概览、总览、核心概念、入门到、全面、详解、[PPT]、[录屏]、[解说]。
- kind：对比/边界/一个概念 → ppt；动手/代码/官网/跑通 → record；坑/对照清单 → narrate。
- need_code：record 且要写代码时 true。
- goal 必须可观察：能指出 / 能写出 / 能跑出 / 能改一处。禁止「理解」「掌握」「了解」。
- beats 必须 3～5 条，每条是这一章要讲的专有名词、步骤或反例，不要「先认路」「看要点」。
- 相邻两章不要重复同一组概念。
- 这一步不要写口播全文和 PPT 页。
"""

SCRIPT_SYSTEM = """给解说章、PPT 章和录屏章写口播稿。只输出 JSON。
{"scripts":[{"n":1,"lines":["句子"]}]}
每章 6～10 句，每句 40～90 字，口语。不要写几分钟、不要代码块。
录屏章只写 PPT 上的概念口播，不要写「打开编辑器」「打开 Cursor」「开始写代码」——实操衔接词由导播在 PPT 讲完后另配。
"""

MATERIAL_SYSTEM = """给整门课备素材。只输出一个 JSON 对象。
{"audience":"一句话听众","chapters":[{"n":1,"kind":"ppt","tool":"ppt","script":["口播句"],"slides":[{"title":"页标题","bullets":["要点"]}],"prompt":""}]}
规则：
- 保留各章 n / title / kind / beats，不要改课名，不要把上一章的幻灯片挪到下一章。
- 口播是对着学生讲，禁止导播指令（不要写「打开编辑器」「截一段」「进入下一章」）。
- narrate：tool=tts，script 6～10 句，slides 空数组。
- ppt 和 record：都必须有 4～6 页只属于本章的 slides（title + 3～5 条 bullets），script 6～10 句按页能念。
- record 的 script 只讲 PPT 概念，最后不要写打开 Cursor；导播会在 PPT 讲完后另配衔接口播再录实操。
- record 要代码：tool=cursor，另写 prompt（真框架 API，禁止 pip/密钥/ChatOpenAI）。只逛官网：tool=web，prompt 空。
- slides 标题和 bullets 必须落在本章 beats 上，禁止复用「概念地图 / Model Prompt Chain」这类上一章内容。
- 不要写几分钟。
"""


def fill_chapter_scripts(course: dict) -> dict:
    course = dict(course or {})
    chapters = [dict(ch) for ch in (course.get("chapters") or [])]
    need = [
        ch
        for ch in chapters
        if lesson_tools.chapter_kind(ch) in {"narrate", "ppt", "record"}
        and not lesson_tools.as_script(ch.get("script"))
    ]
    if not need:
        course["chapters"] = chapters
        return course
    payload = {
        "course_title": course.get("course_title"),
        "chapters": [
            {"n": ch.get("n"), "title": ch.get("title"), "goal": ch.get("goal"), "kind": lesson_tools.chapter_kind(ch)}
            for ch in need
        ],
    }
    try:
        data = chat_json(SCRIPT_SYSTEM, [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], max_tokens=1800)
    except Exception:
        data = {}
    by_n = {}
    for row in data.get("scripts") or []:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("n") or 0)
        except (TypeError, ValueError):
            idx = 0
        lines = lesson_tools.as_script(row.get("lines") or row.get("script"))
        if idx and lines:
            by_n[idx] = lines
    for ch in chapters:
        if int(ch.get("n") or 0) in by_n:
            ch["script"] = by_n[int(ch["n"])]
    course["chapters"] = chapters
    return course


def plan_course(topic: str, note: str = "") -> dict:
    topic = (topic or "").strip() or "未命名课程"
    extra = (note or "").strip()
    user = f"主题：{topic}"
    if extra:
        user += f"\n补充：{extra}"
    try:
        data = chat_json(COURSE_SYSTEM, [{"role": "user", "content": user}], max_tokens=2200)
    except Exception:
        data = {}
    chapters = lesson_tools.parse_chapters(data.get("chapters") if isinstance(data, dict) else None, topic)
    course_title = str((data or {}).get("course_title") or topic).strip() or topic
    n = lesson_tools.detect_chapter(extra) or lesson_tools.detect_chapter(topic)
    if not n:
        try:
            n = int((data or {}).get("chapter") or 1)
        except (TypeError, ValueError):
            n = 1
    n = max(1, min(n, len(chapters)))
    packed = {"course_title": course_title, "chapters": chapters, "chapter": n, "pipeline": "outline"}
    audience = str((data or {}).get("audience") or "").strip()
    if audience:
        packed["audience"] = audience
    return packed


def outline_plan(topic: str, note: str = "") -> dict:
    packed = plan_course(topic, note)
    title = packed["course_title"]
    n = int(packed.get("chapter") or 1)
    chapters = packed["chapters"]
    ch = chapters[n - 1] if chapters else {}
    for item in chapters:
        item["tool"] = lesson_tools.chapter_tool(item)
        item["beats"] = lesson_tools.as_beats(item.get("beats"))
        item.setdefault("slides", [])
        item.setdefault("script", [])
        item.setdefault("status", "todo")
        item.setdefault("video", "")
    beats = lesson_tools.as_beats(ch.get("beats"))
    return {
        "course_title": title,
        "title": title,
        "slug": course_mod.slugify(title),
        "audience": packed.get("audience") or "",
        "objective": ch.get("goal") or "",
        "homepage": "",
        "outline": [{"beat": b, "seconds": 0} for b in beats] or [
            {"beat": item.get("title") or "", "seconds": 0} for item in chapters
        ],
        "narration": [],
        "demo_files": [],
        "rundown": "",
        "prompt": "",
        "expect": "",
        "chapters": chapters,
        "chapter": n,
        "chapter_kind": lesson_tools.chapter_kind(ch),
        "pipeline": "outline",
        "confirmed": False,
        "gate": "review_outline",
        "tools": {
            "use_web": False,
            "use_term": False,
            "use_cursor": False,
            "need_code": False,
            "code_via": "none",
            "use": [],
            "skip": [],
            "gate": "review_outline",
        },
        "slides": [],
    }


def stub_materials(ch: dict) -> dict:
    ch = dict(ch)
    kind = lesson_tools.chapter_kind(ch)
    title = str(ch.get("title") or "本集")
    goal = str(ch.get("goal") or "能说出这一章在讲什么")
    beats = lesson_tools.as_beats(ch.get("beats"))
    if not lesson_tools.as_script(ch.get("script")):
        if beats:
            ch["script"] = [f"这一章只讲「{title}」。"] + [f"记住：{b}。" for b in beats[:4]] + [f"看完你要能：{goal}。"]
        elif kind == "ppt":
            ch["script"] = [
                f"这一章用几页幻灯片讲清「{title}」。",
                f"看完你要能：{goal}。",
                "先把这件事说清楚，再落到能带走的一句。",
            ]
        elif kind == "narrate":
            ch["script"] = [
                f"这一段只口播「{title}」。",
                f"记住一件事：{goal}。",
                "对照自己会不会说。",
            ]
        else:
            ch["script"] = [
                f"这一章先用几页幻灯片讲清「{title}」。",
                f"看完要能：{goal}。",
                "先把这件事说清楚，代码放到后面动手。",
            ]
    if kind != "narrate" and not lesson_tools.as_slides(ch.get("slides")):
        ch["slides"] = lesson_tools.slides_for_chapter(ch)
    ch["tool"] = ch.get("tool") if ch.get("tool") in lesson_tools.TOOL_LABEL else lesson_tools.chapter_tool(ch)
    ch["beats"] = beats
    return ch


def prepare_materials(plan: dict) -> dict:
    plan = dict(plan or {})
    chapters = [dict(ch) for ch in (plan.get("chapters") or [])]
    payload = {
        "course_title": plan.get("course_title"),
        "chapters": [
            {
                "n": ch.get("n"),
                "title": ch.get("title"),
                "goal": ch.get("goal"),
                "kind": lesson_tools.chapter_kind(ch),
                "need_code": ch.get("need_code"),
                "beats": lesson_tools.as_beats(ch.get("beats")),
            }
            for ch in chapters
        ],
    }
    try:
        data = chat_json(
            MATERIAL_SYSTEM,
            [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            max_tokens=2800,
        )
    except Exception:
        data = {}
    by_n: dict[int, dict] = {}
    for row in data.get("chapters") or []:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("n") or 0)
        except (TypeError, ValueError):
            idx = 0
        if idx:
            by_n[idx] = row
    audience = str(data.get("audience") or plan.get("audience") or "").strip()
    out = []
    for ch in chapters:
        row = by_n.get(int(ch.get("n") or 0), {})
        merged = dict(ch)
        if row.get("kind") in lesson_tools.KINDS:
            merged["kind"] = row["kind"]
        script = lesson_tools.as_script(row.get("script"))
        if script:
            merged["script"] = script
        slides = lesson_tools.as_slides(row.get("slides"))
        if slides:
            merged["slides"] = slides
        tool = str(row.get("tool") or "").strip().lower()
        if tool in lesson_tools.TOOL_LABEL:
            merged["tool"] = tool
        prompt = str(row.get("prompt") or "").strip()
        if prompt:
            merged["prompt"] = prompt
        out.append(stub_materials(merged))
    plan["chapters"] = out
    if audience:
        plan["audience"] = audience
    plan["pipeline"] = "materials"
    plan["gate"] = "review_materials"
    cur = lesson_tools.current_chapter(plan)
    plan["narration"] = lesson_tools.as_script(cur.get("script"))
    plan["slides"] = lesson_tools.as_slides(cur.get("slides"))
    plan["chapter_kind"] = lesson_tools.chapter_kind(cur)
    if cur.get("prompt"):
        plan["prompt"] = cur["prompt"]
    return plan


def generate(topic: str, note: str = "", *, course: dict | None = None, chapter: int | None = None) -> dict:
    topic = resolve_topic(topic)
    if not topic:
        raise RuntimeError("先写这一集要讲什么，例如：LangChain")
    extra = (note or "").strip()
    if course is None:
        packed = plan_course(topic, extra)
        packed = fill_chapter_scripts(packed)
        packed["pipeline"] = "produce"
    else:
        chapters = lesson_tools.parse_chapters(course.get("chapters"), topic)
        packed = {
            "course_title": str(course.get("course_title") or topic).strip() or topic,
            "chapters": chapters,
            "chapter": int(chapter or course.get("chapter") or 1),
            "pipeline": str(course.get("pipeline") or "produce"),
            "audience": course.get("audience") or "",
            "slug": course.get("slug") or "",
        }
        need_script = any(
            lesson_tools.chapter_kind(c) in {"narrate", "ppt", "record"}
            and not lesson_tools.as_script(c.get("script"))
            for c in chapters
        )
        if need_script:
            packed = fill_chapter_scripts(packed)
    n = int(chapter or packed.get("chapter") or 1)
    n = max(1, min(n, len(packed["chapters"])))
    ch = packed["chapters"][n - 1]
    kind = lesson_tools.chapter_kind(ch)
    user = (
        f"主题：{topic}\n"
        f"整门课：《{packed['course_title']}》共 {len(packed['chapters'])} 章。\n"
        f"本集只备第 {n} 章「{ch.get('title')}」。看完：{ch.get('goal') or '能验收这一章'}。\n"
        "不要讲其他章的内容。"
    )
    if kind == "narrate":
        user += "\n这一章是解说章：不要代码、不要官网分镜，只要 narration 口播稿（6～10 句口语）。"
    elif kind == "ppt":
        user += "\n这一章是 PPT 章：不要录屏分镜，只要 narration，outline 按幻灯片页来。"
    if extra:
        user += f"\n补充：{extra}"
    raw = chat(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
        max_tokens=3200,
    )
    plan = extract_json(raw)
    plan["course_title"] = packed["course_title"]
    plan["chapters"] = packed["chapters"]
    plan["chapter"] = n
    if not str(plan.get("title") or "").strip():
        plan["title"] = str(ch.get("title") or topic)
    out = normalize(plan, topic)
    out["course_title"] = packed["course_title"]
    out["chapters"] = packed["chapters"]
    out["chapter"] = n
    out["chapter_kind"] = kind
    out["pipeline"] = packed.get("pipeline") or "produce"
    out["slug"] = packed.get("slug") or course_mod.slugify(packed["course_title"])
    if packed.get("audience") and not out.get("audience"):
        out["audience"] = packed["audience"]
    if kind == "narrate":
        out = lesson_tools.force_narrate(out)
        script = lesson_tools.as_script(ch.get("script") or out.get("narration"))
        if script:
            out["narration"] = script
    elif kind == "ppt":
        out = lesson_tools.force_ppt(out)
        script = lesson_tools.as_script(ch.get("script") or out.get("narration"))
        if script:
            out["narration"] = script
        slides = lesson_tools.as_slides(ch.get("slides") or out.get("slides"))
        if slides:
            out["slides"] = slides
    return out


def as_text(value) -> str:
    if isinstance(value, list):
        return "\n".join(str(x).strip() for x in value if str(x).strip())
    return str(value or "").strip()


def short_expect(text: str, fallback: str) -> str:
    raw = (text or "").strip()
    if 1 <= len(raw) <= 24 and " " not in raw[:12]:
        return raw
    token = re.split(r"[\s，。；：、]", raw)[0].strip()
    if 2 <= len(token) <= 24:
        return token
    return (fallback or "完成")[:24]


HEAVY_TERM = re.compile(
    r"pip install|poetry add|npm i|npm install|openai|api[_ ]?key|ChatOpenAI|"
    r"langchain\.llms|from langchain|huggingface|export OPENAI",
    re.I,
)


def term_is_heavy(text: str) -> bool:
    return bool(HEAVY_TERM.search(text or ""))


def sanitize_term(text: str) -> str:
    keep: list[str] = []
    for ln in as_text(text).splitlines():
        cmd = ln.split(" ", 1)[0]
        if cmd not in {"run", "执行", "命令", "look", "看", "wait", "等待", "clear", "清屏", "card", "章节"}:
            continue
        if term_is_heavy(ln):
            continue
        keep.append(ln)
    return "\n".join(keep)


def lighten_rundown(text: str) -> str:
    """Drop terminal blocks that would hang (pip install, API keys, LLM calls)."""
    lines = as_text(text).splitlines()
    out: list[str] = []
    block: list[str] = []
    kind = "other"

    def flush() -> None:
        nonlocal block, kind
        if not block:
            return
        body = "\n".join(block)
        if kind == "term" and term_is_heavy(body):
            block = []
            kind = "other"
            return
        out.extend(block)
        block = []
        kind = "other"

    for ln in lines:
        token = ln.strip().split(" ", 1)[0].lower()
        if token in {"term", "终端"}:
            flush()
            kind = "term"
            block = [ln]
            continue
        if token in {"web", "网页", "card", "章节", "take", "split", "对比", "片段", "cursor", "agent"}:
            flush()
            kind = "web" if token in {"web", "网页"} else "other"
            block = [ln]
            continue
        block.append(ln)
    flush()
    return "\n".join(out).strip()


def sanitize_web(text: str, homepage: str) -> str:
    keep: list[str] = []
    for raw in as_text(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        cmd = line.split(" ", 1)[0]
        if wd.ALIASES.get(cmd) or wd.ALIASES.get(cmd.lower()):
            key = wd.ALIASES.get(cmd) or wd.ALIASES.get(cmd.lower())
            rest = line.partition(" ")[2].strip()
            if key in {"look", "wait"} and rest and not rest.replace(".", "", 1).isdigit():
                keep.append(f"{cmd} 2")
            elif key == "scroll" and rest and not rest.split()[0].replace(".", "", 1).isdigit():
                keep.append(f"{cmd} 4")
            else:
                keep.append(line)
    has_open = any((ln.split()[0] in {"open", "打开"}) for ln in keep)
    if homepage and not has_open:
        keep = [f"open {homepage}", "look 2", "scroll 4", "look 1.2"] + [ln for ln in keep if ln.split()[0] not in {"open", "打开"}]
    if not keep and homepage:
        keep = [f"open {homepage}", "look 2", "scroll 4", "look 1.2"]
    if keep:
        try:
            wd.parse_script("\n".join(keep))
        except Exception:
            keep = [f"open {homepage}", "look 2", "scroll 4", "look 1.2"] if homepage else keep
    return "\n".join(keep)


def normalize(plan: dict, topic: str = "") -> dict:
    title = str(plan.get("title") or "未命名一集").strip()
    homepage = str(plan.get("homepage") or "").strip()
    outline = plan.get("outline") or []
    beats = []
    for item in outline:
        if isinstance(item, dict):
            beats.append(
                {
                    "beat": str(item.get("beat") or item.get("title") or "").strip(),
                    "seconds": int(item.get("seconds") or 20),
                }
            )
        else:
            beats.append({"beat": str(item).strip(), "seconds": 20})
    beats = [b for b in beats if b["beat"]][:7]
    rundown = as_text(plan.get("rundown"))
    web_script = sanitize_web(plan.get("web_script") or "", homepage)
    term_script = sanitize_term(plan.get("term_script") or "")
    rundown = lighten_rundown(rundown)
    expect = lesson_tools.honest_expect(plan, str(plan.get("expect") or ""))
    if not rundown or rundown.startswith("["):
        rundown = _fallback_rundown(title, homepage, web_script, term_script, beats)
    try:
        segs = asm.parse_rundown(rundown)
        web_ok = all(
            seg["kind"] != "web"
            or any((ln.split() + [""])[0] in {"open", "打开"} for ln in seg.get("lines") or [])
            for seg in segs
        )
        if not web_ok:
            raise RuntimeError("分镜缺 open")
    except Exception:
        rundown = _fallback_rundown(title, homepage, web_script, term_script, beats)
        asm.parse_rundown(rundown)
    if not web_script:
        web_lines = [ln for ln in rundown.splitlines() if ln.strip() and not ln.strip().lower() in {"web", "term", "网页", "终端"}]
        # keep only web-ish commands
        keep = []
        in_term = False
        for ln in rundown.splitlines():
            s = ln.strip()
            low = s.split(" ", 1)[0].lower()
            if low in {"term", "终端"}:
                in_term = True
                continue
            if low in {"web", "网页", "card", "章节", "take", "split", "对比", "片段"}:
                in_term = False
                if low in {"web", "网页"}:
                    continue
            if in_term:
                continue
            if s:
                keep.append(s)
        web_script = "\n".join(keep)
    if web_script:
        try:
            wd.parse_script(web_script)
        except Exception:
            if homepage:
                web_script = f"open {homepage}\nlook 2\nscroll 4\nlook 1.2"
                wd.parse_script(web_script)
    narration = plan.get("narration") or []
    if isinstance(narration, str):
        narration = [n.strip() for n in narration.split("\n") if n.strip()]
    return lesson_tools.attach_tools(
        {
            "audience": str(plan.get("audience") or "").strip(),
            "title": title,
            "objective": str(plan.get("objective") or "").strip(),
            "expect": expect,
            "homepage": homepage,
            "outline": beats,
            "prompt": str(plan.get("prompt") or "").strip(),
            "web_script": web_script,
            "term_script": term_script,
            "rundown": rundown,
            "narration": [str(n).strip() for n in narration if str(n).strip()],
            "demo_files": plan.get("demo_files") or [],
            "course_title": str(plan.get("course_title") or "").strip(),
            "chapters": lesson_tools.parse_chapters(plan.get("chapters"), topic or title),
            "chapter": plan.get("chapter") or 1,
        },
        topic,
    )


def _fallback_rundown(title: str, homepage: str, web_script: str, term_script: str, beats: list[dict]) -> str:
    lines = [f"card {title}"]
    if homepage or web_script:
        lines.append("web")
        if web_script:
            lines.extend(web_script.splitlines())
        elif homepage:
            lines += [f"open {homepage}", "look 2", "scroll 4", "look 1"]
    elif beats:
        lines.append("web")
        lines += ["open https://github.com/", "look 2", "scroll 3"]
    if term_script:
        lines.append("term")
        lines.extend(term_script.splitlines())
    return "\n".join(lines)
