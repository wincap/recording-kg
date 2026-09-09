#!/usr/bin/env python3
"""Match a lesson topic to recordable tools, and keep a human in the loop."""

from __future__ import annotations

import re
from typing import Any


MINUTE_RE = re.compile(r"用?\s*\d+\s*分钟")

CURSOR_MARKS = (
    "cursor",
    "copilot",
    "composer",
    "agent 写",
    "用 agent",
    "ide 演示",
    "提示词工程",
)
CODE_MARKS = (
    "langchain",
    "fastapi",
    "vite",
    "vue",
    "python",
    "sdk",
    "框架",
    "库",
    "chain",
    "llm",
    "agent",
    "怎么用",
    "写代码",
    "api",
    "调用",
    "入门",
)
WEB_MARKS = (
    "官网",
    "文档",
    "docs",
    "github",
    "开源",
    "产品",
    "介绍",
    "是什么",
    "导览",
)
WEB_ONLY_PROJECTS = ("redis",)
CODE_PROJECTS = ("langchain", "fastapi", "vite", "vue")
HOMEPAGES = {
    "langchain": "https://www.langchain.com/",
    "fastapi": "https://fastapi.tiangolo.com/",
    "vite": "https://vite.dev/",
    "vue": "https://vuejs.org/",
    "redis": "https://redis.io/",
}

LCEL_CHAIN = '''"""最小 LCEL：Runnable 用 | 串起来。
没有密钥，假模型用 RunnableLambda，写法仍是真 LCEL。"""

from langchain_core.runnables import RunnableLambda

to_prompt = RunnableLambda(lambda q: "你是助教。用两句话回答：" + q)
fake_model = RunnableLambda(lambda prompt: "LCEL 就是用 | 把 Runnable 串成一条链。")
chain = to_prompt | fake_model

if __name__ == "__main__":
    q = "什么是 LCEL？"
    print("问：", q)
    print("答：", chain.invoke(q))
'''

TINY_CHAIN = LCEL_CHAIN

TINY_ROUTE = '''"""最小 FastAPI：真实路由，TestClient 打真实请求。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    client = TestClient(app)
    print("问：GET /health")
    print("答：", client.get("/health").json())
'''

TINY_PIPE = '''"""最小流水线：读源码 → 转换 → 输出。
打包工具（Vite 这类）就是在做这件事，只是步骤更多。"""


def transform(src: str) -> str:
    return src.replace("msg", "hello")


if __name__ == "__main__":
    src = "console.log(msg)"
    print("问：", src)
    print("答：", transform(src))
'''


def blob_of(plan: dict | None, extra: str = "") -> str:
    plan = plan or {}
    parts = [
        extra,
        str(plan.get("course_title") or ""),
        str(plan.get("title") or ""),
        str(plan.get("objective") or ""),
        str(plan.get("homepage") or ""),
        str(plan.get("audience") or ""),
        str(plan.get("prompt") or ""),
    ]
    for item in plan.get("outline") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("beat") or ""))
        else:
            parts.append(str(item))
    return " ".join(parts).strip()


def honest_text(text: str) -> str:
    raw = str(text or "")
    raw = re.sub(r"一个\s*\d+\s*分钟", "一集", raw)
    raw = MINUTE_RE.sub("", raw)
    raw = re.sub(r"（约?\s*\d+\s*分钟）", "", raw)
    raw = re.sub(r"[ \t]{2,}", " ", raw)
    return raw.strip(" ，,：:")


def guess_secs(text: str) -> float:
    n = len(re.sub(r"\s+", "", text or ""))
    return min(36.0, max(10.0, n / 3.5))


def parse_card_arg(arg: str) -> tuple[float, str]:
    raw = (arg or "").strip()
    hit = re.match(r"^(\d+(?:\.\d+)?)\s+(.+)$", raw)
    if hit:
        return max(2.0, float(hit.group(1))), hit.group(2).strip() or "本集"
    return 2.2, raw or "本集"


def _has(blob: str, marks: tuple[str, ...]) -> bool:
    low = (blob or "").lower()
    return any(mark in low for mark in marks)


def classify(plan: dict | None = None, topic: str = "") -> dict[str, Any]:
    blob = blob_of(plan, topic).lower()
    homepage = str((plan or {}).get("homepage") or "").strip()
    want_cursor = _has(blob, CURSOR_MARKS)
    named_code = any(name in blob for name in CODE_PROJECTS)
    named_web_only = any(name in blob for name in WEB_ONLY_PROJECTS) and not named_code
    want_code = (named_code or _has(blob, CODE_MARKS)) and not named_web_only
    if want_cursor and "langchain" not in blob:
        want_code = True
    want_web = bool(homepage) or named_code or named_web_only or _has(blob, WEB_MARKS)
    if want_cursor and not homepage and not named_code:
        want_web = False
    if want_code and not want_cursor:
        want_web = want_web or named_code or bool(homepage)

    # 需要代码演示时，默认录 Cursor 写和跑，不在终端 cat。
    via = "none"
    if want_code:
        via = "cursor"
    use, skip = tool_lists(want_web, via)
    return {
        "use_web": want_web,
        "use_term": via == "local",
        "use_cursor": via == "cursor",
        "need_code": want_code,
        "code_via": via,
        "use": use,
        "skip": skip,
        "gate": "review_tools",
    }


def tool_lists(want_web: bool, via: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    use: list[dict[str, str]] = []
    skip: list[dict[str, str]] = []
    if want_web:
        use.append({"id": "web", "label": "官网", "why": "有真实页面，学生要对着认路。"})
    else:
        skip.append({"id": "web", "label": "官网", "why": "这一集没有必须打开的站点。"})
    if via == "cursor":
        use.append(
            {
                "id": "cursor",
                "label": "Cursor",
                "why": "演示代码要让学生看见 Agent 写出来、跑起来，不是终端里 cat 一份现成文件。",
            }
        )
        skip.append({"id": "term", "label": "终端", "why": "默认不在终端演示。要改用终端再说。"})
    elif via == "local":
        use.append({"id": "term", "label": "终端跑代码", "why": "按你的选择，用终端看现成文件再跑。"})
        skip.append({"id": "cursor", "label": "Cursor", "why": "你选了不录 Agent。"})
    else:
        skip.append({"id": "term", "label": "终端", "why": "不是动手课，硬跑代码会跑偏。"})
        skip.append({"id": "cursor", "label": "Cursor", "why": "这一集没有代码演示。"})
    skip.append({"id": "image", "label": "文生图", "why": "讲课要真页面和真输出，生成图对不上。"})
    return use, skip


def pick_demo(plan: dict | None = None, topic: str = "") -> list[dict[str, str]]:
    blob = blob_of(plan, topic).lower()
    if "fastapi" in blob:
        return [{"path": "tiny_route.py", "content": TINY_ROUTE}]
    if "vite" in blob or "vue" in blob:
        return [{"path": "tiny_pipe.py", "content": TINY_PIPE}]
    if "langchain" in blob or "lcel" in blob or "chain" in blob:
        return [{"path": "lcel_chain.py", "content": LCEL_CHAIN}]
    return [{"path": "lcel_chain.py", "content": LCEL_CHAIN}]


def safe_demo_files(items) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    banned = re.compile(
        r"pip install|poetry add|npm install|ChatOpenAI|ChatAnthropic|"
        r"huggingface|openai\s*\.|sk-[A-Za-z0-9]{8,}|api[_ ]?key\s*=",
        re.I,
    )
    for item in items or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip().lstrip("./")
        if not path or ".." in path or path.startswith("/") or "\\" in path:
            continue
        content = str(item.get("content") or "")
        if not content.strip() or banned.search(content):
            continue
        out.append({"path": path, "content": content[:12000]})
    return out


def guess_click(plan: dict | None) -> str:
    text = "\n".join(
        [
            str((plan or {}).get("web_script") or ""),
            str((plan or {}).get("rundown") or ""),
        ]
    )
    for line in text.splitlines():
        cmd, _, rest = line.strip().partition(" ")
        if cmd in {"click", "点", "点击"} and rest.strip():
            return rest.strip()
    homepage = str((plan or {}).get("homepage") or "").lower()
    if "fastapi" in homepage:
        return "Docs"
    if "vite" in homepage:
        return "Guide"
    if "vue" in homepage:
        return "Docs"
    return "Docs"


def fit_rundown(plan: dict, durs: list[float] | None = None) -> str:
    plan = plan or {}
    tools = plan.get("tools") or classify(plan)
    lines = [str(n).strip() for n in (plan.get("narration") or []) if str(n).strip()]
    if not durs:
        durs = [guess_secs(x) for x in lines]
    if not durs:
        durs = [12.0, 16.0, 16.0, 14.0, 18.0, 14.0]
    durs = [max(8.0, float(d) + 0.6) for d in durs]
    while tools.get("need_code") and len(durs) < 6:
        durs.append(14.0)
    pad = durs + [12.0] * 8
    title = honest_text(str(plan.get("title") or "本集")) or "本集"
    homepage = str(plan.get("homepage") or "").strip()
    click = guess_click(plan)
    parts: list[str] = [f"card {pad[0]:.1f} {title}"]
    idx = 1
    if tools.get("use_web") and homepage:
        parts += [
            "web",
            f"open {homepage}",
            "wait 2.5",
            f"look {pad[idx]:.1f}",
            "scroll 5",
            f"look {pad[idx + 1]:.1f}",
            f"click {click}",
            "wait 2",
            f"look {pad[idx + 2]:.1f}",
        ]
        idx += 3
    demo = plan.get("demo_files") or []
    path = demo[0]["path"] if demo else "lcel_chain.py"
    if tools.get("use_cursor"):
        parts += ["cursor", path]
    elif tools.get("use_term") and demo:
        parts += [
            "term",
            f"run nl -ba {path}",
            f"look {pad[idx]:.1f}",
            f"run python3 {path}",
            f"look {pad[idx + 1]:.1f}",
        ]
    return "\n".join(parts)


def term_script_from(plan: dict) -> str:
    demo = plan.get("demo_files") or []
    if not demo or not (plan.get("tools") or {}).get("use_term"):
        return ""
    path = demo[0]["path"]
    return f"run nl -ba {path}\nlook 14\nrun python3 {path}\nlook 12"


def web_script_from(rundown: str) -> str:
    keep: list[str] = []
    in_web = False
    for raw in (rundown or "").splitlines():
        token = raw.strip().split(" ", 1)[0].lower()
        if token in {"web", "网页"}:
            in_web = True
            continue
        if token in {"term", "终端", "card", "章节", "take", "split", "cursor", "agent"}:
            in_web = False
            continue
        if in_web and raw.strip():
            keep.append(raw.strip())
    return "\n".join(keep)


def attach_tools(plan: dict, topic: str = "") -> dict:
    plan = dict(plan or {})
    home = str(plan.get("homepage") or "").strip()
    if not home:
        blob = blob_of(plan, topic).lower()
        for key, url in HOMEPAGES.items():
            if key in blob:
                home = url
                break
    plan["homepage"] = home
    plan["title"] = honest_text(str(plan.get("title") or "未命名一集")) or "未命名一集"
    plan["objective"] = honest_text(str(plan.get("objective") or ""))
    narr = plan.get("narration") or []
    if isinstance(narr, str):
        narr = [n.strip() for n in narr.splitlines() if n.strip()]
    plan["narration"] = [honest_text(str(n)) for n in narr if str(n).strip()]
    tools = classify(plan, topic)
    plan["tools"] = tools
    plan["confirmed"] = False
    plan["gate"] = "review_tools"
    if tools.get("need_code"):
        incoming = safe_demo_files(plan.get("demo_files"))
        plan["demo_files"] = incoming or pick_demo(plan, topic)
    else:
        plan["demo_files"] = []
    if not plan.get("rundown_custom"):
        rundown = fit_rundown(plan)
        plan["rundown"] = rundown
    else:
        rundown = str(plan.get("rundown") or "")
    plan["web_script"] = web_script_from(rundown)
    plan["term_script"] = term_script_from(plan)
    incoming_prompt = str(plan.get("prompt") or "").strip()
    if tools.get("use_cursor"):
        if incoming_prompt:
            plan["prompt"] = incoming_prompt
            plan["expect"] = honest_expect(plan)
        else:
            prompt, expect = cursor_demo_prompt(plan)
            plan["prompt"] = prompt
            plan["expect"] = expect
    elif plan.get("demo_files"):
        plan["expect"] = honest_expect(plan)
    return plan


def apply_choice(plan: dict, choice: str) -> dict:
    plan = dict(plan or {})
    tools = dict(plan.get("tools") or classify(plan))
    want_web = bool(tools.get("use_web") or plan.get("homepage"))
    if choice in {"web_only", "drop_code", "accept_web"}:
        want_web = True
        via = "none"
        plan["demo_files"] = []
        tools["need_code"] = False
    elif choice in {"drop_cursor", "accept_demo", "use_local", "use_term"}:
        via = "local"
        tools["need_code"] = True
        want_web = bool(tools.get("use_web") or plan.get("homepage"))
        if not plan.get("demo_files"):
            plan["demo_files"] = pick_demo(plan)
    elif choice == "cursor_demo":
        via = "cursor"
        tools["need_code"] = True
        if not plan.get("demo_files"):
            plan["demo_files"] = pick_demo(plan)
    else:
        via = str(tools.get("code_via") or "none")
    use, skip = tool_lists(want_web, via)
    tools.update(
        {
            "use_web": want_web,
            "use_term": via == "local",
            "use_cursor": via == "cursor",
            "code_via": via,
            "use": use,
            "skip": skip,
        }
    )
    plan["tools"] = tools
    plan["confirmed"] = False
    plan["gate"] = "review_tools"
    plan["rundown"] = fit_rundown(plan)
    plan["web_script"] = web_script_from(plan["rundown"])
    plan["term_script"] = term_script_from(plan)
    if via == "cursor":
        prompt, expect = cursor_demo_prompt(plan)
        plan["prompt"] = prompt
        plan["expect"] = expect
    return plan


GOAL_EXPECT = re.compile(r"^(能|看完|会|理解|掌握|说出|运行第|第一个)")


def expect_from_demo(plan: dict | None) -> str:
    demo = ((plan or {}).get("demo_files") or [{}])[0] or {}
    content = str(demo.get("content") or "")
    for match in re.finditer(r"print\(\s*(['\"])(.*?)\1", content):
        token = str(match.group(2) or "").strip()
        if not token:
            continue
        if "问" in token:
            return "问："
        return token[:24]
    return ""


def honest_expect(plan: dict | None, raw: str = "") -> str:
    text = (raw or str((plan or {}).get("expect") or "")).strip()
    if text and not GOAL_EXPECT.match(text) and text not in {"完成", "ok", "OK"}:
        return text[:24]
    return expect_from_demo(plan) or "问："


def _framework_shape(blob: str) -> str:
    if "langchain" in blob or "lcel" in blob or "chain" in blob:
        return (
            "必须用真 LCEL：from langchain_core.runnables import RunnableLambda，"
            "用 | 把 Runnable 串起来，chain.invoke(...) 跑通。"
            "禁止手写 def chain() 假装成链。没有密钥就用 RunnableLambda 当假模型。"
        )
    if "fastapi" in blob:
        return (
            "必须 from fastapi import FastAPI，用装饰器写真实路由，"
            "用 fastapi.testclient.TestClient 发真实请求并 print JSON。"
            "禁止用字典 ROUTES 模拟。禁止 uvicorn.run 挂起不退出。"
        )
    if "vite" in blob or "vue" in blob:
        return "用最小转换函数演示打包流水线的形状，并打印输入输出。"
    return "按标题写最小可跑示例，API 必须像真的，不要用标准库假货顶替框架。"


def run_after_write(path: str) -> str:
    return (
        "不要 pip install，不要密钥，不要访问网络，不要 ChatOpenAI。\n"
        f"保存后立刻执行 `.venv/bin/python {path}`（没有 .venv 再用 python3），"
        "把完整终端输出发回来。不要只写文件不跑。\n"
        "文件里要打印两行真实结果：一行以「问：」开头，一行以「答：」开头。"
        "答必须是程序跑出来的，禁止手写假输出。"
    )


def cursor_demo_prompt(plan: dict) -> tuple[str, str]:
    custom = str((plan or {}).get("prompt") or "").strip()
    expect = honest_expect(plan)
    demo = (plan or {}).get("demo_files") or pick_demo(plan)
    path = demo[0]["path"] if demo else "lcel_chain.py"
    run = run_after_write(path)
    if custom:
        body = custom.rstrip()
        if "问：" not in body or ("执行" not in body and "跑" not in body):
            body = body + "\n" + run
        return body, expect[:24]
    title = str((plan or {}).get("title") or "最小演示")
    objective = str((plan or {}).get("objective") or "").strip()
    blob = blob_of(plan).lower()
    extra = f"看完要能：{objective}\n" if objective else ""
    prompt = (
        f"在当前项目只新建 {path}。\n"
        f"这一集标题是「{title}」。\n"
        f"{extra}"
        f"{_framework_shape(blob)}\n"
        f"{run}"
    )
    return prompt, expect[:24]


def chapter_cursor_prompt(plan: dict | None, ch: dict | None = None) -> tuple[str, str]:
    plan = dict(plan or {})
    ch = ch or current_chapter(plan)
    title = str(ch.get("title") or plan.get("title") or "最小演示")
    goal = str(ch.get("goal") or plan.get("objective") or "").strip()
    beats = as_beats(ch.get("beats"))
    merged = {
        **plan,
        **ch,
        "title": title,
        "objective": goal,
        "prompt": str(ch.get("prompt") or "").strip(),
        "course_title": plan.get("course_title") or "",
        "audience": plan.get("audience") or "",
    }
    merged["demo_files"] = pick_demo(merged)
    prompt, expect = cursor_demo_prompt(merged)
    if beats and "必须覆盖" not in prompt:
        prompt = prompt.rstrip() + "\n必须覆盖：" + "、".join(beats[:5]) + "。"
    if "不要再讲概念" not in prompt:
        prompt = (
            "这是 PPT 讲完之后的实操，不要再讲概念、不要开新题目。"
            "只把刚才幻灯片上的要点写成能跑的代码并立刻执行。\n"
            + prompt
        )
    return prompt, expect[:24]


def chapter_media_ready(folder, kind: str) -> bool:
    from pathlib import Path

    import course

    folder = Path(folder)
    if str(kind or "").strip().lower() == "record":
        take = folder / "cursor.mp4"
        return take.is_file() and take.stat().st_size > 1000
    return course.timeline_ready(folder)


def looks_like_code(text: str) -> bool:
    raw = text or ""
    if "```" in raw:
        return True
    if re.search(r"^\s*(def |class |from |import |async def )", raw, re.M):
        return True
    return raw.count("\n") >= 3 and "(" in raw and ")" in raw


def wants_code_rewrite(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if re.search(r"重写.{0,16}(代码|演示|lcel|链|prompt|提示词)", raw, re.I):
        return True
    if re.search(r"(改成|换成|用|写成).{0,12}(lcel|真链|langchain_core|runnable)", raw, re.I):
        return True
    if re.search(r"(代码|演示|提示词).{0,10}(重写|不对|假的|怪怪|不是)", raw):
        return True
    if re.fullmatch(r"(重写代码|改代码|改演示代码|重写演示|重写提示词|改提示词)", raw):
        return True
    return False


def detect_choice(text: str) -> str:
    raw = (text or "").strip()
    low = raw.lower()
    table = {
        "确认这样录": "confirm_record",
        "确认并录": "confirm_record",
        "工具没问题": "confirm_record",
        "按这个录": "confirm_record",
        "确认大纲": "confirm_outline",
        "大纲没问题": "confirm_outline",
        "大纲可以": "confirm_outline",
        "生成素材": "prepare_materials",
        "写解说词": "prepare_materials",
        "出脚本": "prepare_materials",
        "素材没问题，开始出片": "confirm_materials",
        "素材没问题": "confirm_materials",
        "开始出片": "confirm_materials",
        "做这一章": "produce_chapter",
        "实现这一章": "produce_chapter",
        "生成PPT": "make_ppt",
        "组成PPT": "make_ppt",
        "做下一章": "next_chapter",
        "把剩下的章做完": "produce_chapter",
        "把剩下的做完": "produce_chapter",
        "这一章改成PPT": "kind_ppt",
        "改成PPT": "kind_ppt",
        "这一章改成解说": "kind_narrate",
        "改成解说": "kind_narrate",
        "这一章改成录屏": "kind_record",
        "改成录屏": "kind_record",
        "生成解说并配音": "narrate",
        "生成本章解说": "narrate",
        "生成解说词": "narrate",
        "打开学生端": "watch",
        "只要官网": "web_only",
        "只看官网": "web_only",
        "不要代码": "drop_code",
        "不要代码演示": "drop_code",
        "让 cursor 写": "cursor_demo",
        "让 cursor 写演示": "cursor_demo",
        "用 cursor 写": "cursor_demo",
        "让cursor写": "cursor_demo",
        "让cursor写演示": "cursor_demo",
        "用这份": "accept_demo",
        "用这份代码": "accept_demo",
        "改用本地这份": "accept_demo",
        "先看演示代码": "show_demo",
        "改用终端": "use_term",
        "改用终端跑": "use_term",
        "用终端跑": "use_term",
        "不要 cursor": "use_term",
        "不要录 cursor": "use_term",
    }
    if raw in table:
        return table[raw]
    if low in table:
        return table[low]
    if re.search(r"只要官网|只看官网|不要终端", raw):
        return "web_only"
    if re.search(r"不要代码", raw):
        return "drop_code"
    if re.search(r"让\s*cursor|用\s*cursor\s*写", raw, re.I):
        return "cursor_demo"
    if re.search(r"用这份|改用本地", raw):
        return "accept_demo"
    if "演示代码" in raw and ("看" in raw or "先" in raw):
        return "show_demo"
    if re.search(r"改用终端|用终端跑", raw):
        return "use_term"
    if re.search(r"不要\s*cursor", raw, re.I):
        return "use_term"
    if re.search(r"确认大纲|大纲没问题|大纲可以", raw):
        return "confirm_outline"
    if re.search(r"素材没问题|开始出片", raw):
        return "confirm_materials"
    if re.search(r"做这一章|实现这一章|把剩下的章做完|把剩下的做完", raw):
        return "produce_chapter"
    if re.search(r"改成\s*PPT|组成PPT", raw):
        return "kind_ppt"
    if re.search(r"改成解说", raw):
        return "kind_narrate"
    if re.search(r"改成录屏", raw):
        return "kind_record"
    return ""


def tool_speech(plan: dict) -> str:
    tools = (plan or {}).get("tools") or {}
    lines = ["工具我按主题配了一版，先别录，你看合不合理。", "用："]
    for item in tools.get("use") or []:
        lines.append(f"- {item.get('label')}：{item.get('why')}")
    skipped = tools.get("skip") or []
    if skipped:
        lines.append("不用：")
        for item in skipped:
            lines.append(f"- {item.get('label')}：{item.get('why')}")
    demo = (plan or {}).get("demo_files") or []
    if demo:
        via = tools.get("code_via") or "none"
        if via == "cursor":
            lines.append(f"演示代码由 Cursor 当场写 {demo[0]['path']}，写完立刻跑。卡片里是期望形状，不会预写进目录。")
        else:
            lines.append(f"演示代码已备好：{demo[0]['path']}（终端里跑）。")
    lines.append("卡片里的字都能改，改完点保存。也可以说「录第 2 章」「标题改成…」。行了再确认这样录。")
    return "\n".join(lines)


KINDS = ("narrate", "record", "ppt")
KIND_LABEL = {"narrate": "解说", "record": "录屏", "ppt": "PPT"}
TOOL_LABEL = {"tts": "解说配音", "web": "录官网", "cursor": "录 Cursor", "ppt": "组成 PPT", "term": "录终端"}
STAGES = ("outline", "materials", "produce")
STAGE_LABEL = {"outline": "1.大纲", "materials": "2.素材脚本", "produce": "3.逐章出片"}


def as_beats(raw) -> list[str]:
    out: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                text = str(item.get("beat") or item.get("title") or item.get("point") or "").strip()
            else:
                text = str(item).strip()
            if text:
                out.append(text)
        return out
    return [ln.strip() for ln in str(raw or "").splitlines() if ln.strip()]


def slides_for_chapter(ch: dict | None) -> list[dict]:
    ch = ch or {}
    slides = as_slides(ch.get("slides"))
    if slides:
        return slides
    title = str(ch.get("title") or "本集").strip() or "本集"
    goal = str(ch.get("goal") or "").strip()
    beats = as_beats(ch.get("beats"))
    if beats:
        pages = [{"title": title, "bullets": beats[:5]}]
        for beat in beats:
            pages.append({"title": beat, "bullets": [goal] if goal else [beat]})
        return pages
    if goal:
        return [{"title": title, "bullets": [goal]}]
    return [{"title": title, "bullets": ["这一章只讲这一件事"]}]


def fallback_chapters(topic: str = "") -> list[dict]:
    name = honest_text((topic or "这一课").strip()) or "这一课"
    name = re.sub(r"[：:].+$", "", name)[:18] or "这一课"
    blob = f"{topic} {name}".lower()
    if "langchain" in blob:
        rows = [
            {
                "n": 1,
                "title": "直接调模型 API 和用 LangChain 差在哪",
                "goal": "能指出编排层解决的是多步骤维护，不是把模型换掉",
                "need_code": False,
                "kind": "ppt",
                "beats": ["单次补全 vs 多步骤链路", "LangChain 管编排不管训练", "适合原型、不适合核心交易"],
            },
            {
                "n": 2,
                "title": "用 ChatPromptTemplate 把变量填进提示",
                "goal": "能写出带 topic 变量的模板并说出最终提示长什么样",
                "need_code": False,
                "kind": "ppt",
                "beats": ["系统消息和人类消息分开", "变量用花括号占位", "同一模板换 topic 能复用"],
            },
            {
                "n": 3,
                "title": "用 LCEL 把提示和模型串成一条链",
                "goal": "能写出 prompt | model 并 invoke 一组字典输入",
                "need_code": True,
                "kind": "record",
                "beats": ["Runnable 用竖线连接", "invoke 传入字典", "假模型也要真 LCEL 写法"],
            },
            {
                "n": 4,
                "title": "让链吐出固定格式",
                "goal": "能给链加上格式约束并指出输出是否合格",
                "need_code": True,
                "kind": "record",
                "beats": ["在提示里写清输出形状", "不合格就收紧约束", "不要把格式写进业务代码里拼"],
            },
            {
                "n": 5,
                "title": "最小 RAG：本地文本检索再回答",
                "goal": "能把一篇本地文档切块检索，并让回答引用片段",
                "need_code": True,
                "kind": "record",
                "beats": ["加载与切分", "向量检索只看相关片段", "文档外问题要说不知道"],
            },
            {
                "n": 6,
                "title": "给 Agent 接一个自定义工具",
                "goal": "能写出一个 @tool 并指出模型何时该调用它",
                "need_code": True,
                "kind": "record",
                "beats": ["工具描述决定会不会被调", "必须用工具才能答对的题", "不需要工具时不该乱调"],
            },
            {
                "n": 7,
                "title": "对照清单：密钥、幻觉、链路日志",
                "goal": "能按清单判断这条演示链路能不能拿出去讲",
                "need_code": False,
                "kind": "narrate",
                "beats": ["密钥只走环境变量", "有检索就要接地", "能看到提示、检索、工具和最终输出"],
            },
        ]
    else:
        rows = [
            {
                "n": 1,
                "title": f"{name}解决什么问题",
                "goal": f"能用一件具体的事说明 {name} 解决谁的痛",
                "need_code": False,
                "kind": "ppt",
                "beats": ["谁会用到它", "没有它时怎么凑合", "有了之后哪一步变短"],
            },
            {
                "n": 2,
                "title": f"{name}里最关键的一个概念",
                "goal": "能用自己的话讲清这一个概念，不和其他概念混着背",
                "need_code": False,
                "kind": "ppt",
                "beats": ["这个概念管哪一段", "它不负责什么", "和一个易混概念的差别"],
            },
            {
                "n": 3,
                "title": f"跑通 {name} 的最小例子",
                "goal": "能跟着做出一个可复现的最小例子",
                "need_code": True,
                "kind": "record",
                "beats": ["准备什么输入", "最小代码或页面路径", "成功时长什么样"],
            },
            {
                "n": 4,
                "title": "改一处再跑通",
                "goal": "能改一个输入或参数并指出结果怎么变",
                "need_code": True,
                "kind": "record",
                "beats": ["改哪一处", "改之前的结果", "改之后的结果"],
            },
            {
                "n": 5,
                "title": "这一课最常见的坑和过关标准",
                "goal": "能说出一个坑，以及怎样算这课过关",
                "need_code": False,
                "kind": "narrate",
                "beats": ["最容易做错的一步", "做错时长什么样", "过关时必须能演示的一件事"],
            },
        ]
    for row in rows:
        row["tool"] = chapter_tool(row)
        row["slides"] = []
        row["script"] = []
        row["status"] = "todo"
        row["video"] = ""
        row["beats"] = as_beats(row.get("beats"))
    return rows


def guess_kind(title: str, goal: str = "", need_code: bool | None = None) -> str:
    blob = f"{title} {goal}".lower()
    if need_code:
        return "record"
    if any(mark in blob for mark in ("官网", "动手", "代码", "跑通", "用法", "cursor", "演示", "改一处", "文档", "docs")):
        return "record"
    if any(mark in blob for mark in ("坑", "验收", "小结", "收尾", "口播")):
        return "narrate"
    return "ppt"


def as_script(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [ln.strip() for ln in str(raw or "").splitlines() if ln.strip()]


HANDOFF_MARKS = ("接下来打开", "接下来动手", "打开 Cursor", "接下来实操", "动手写", "开始写代码")
PRACTICE_SLIDE_MARKS = ("接下来动手", "接下来实操", "动手写")


def looks_like_handoff(text: str) -> bool:
    raw = str(text or "")
    return any(mark in raw for mark in HANDOFF_MARKS)


def concept_script(ch: dict | None) -> list[str]:
    lines = as_script((ch or {}).get("script"))
    kept = [ln for ln in lines if not looks_like_handoff(ln)]
    return kept or lines


def practice_handoff(ch: dict | None) -> dict:
    ch = ch or {}
    title = str(ch.get("title") or "这一章").strip() or "这一章"
    goal = str(ch.get("goal") or "").strip()
    beats = as_beats(ch.get("beats"))
    beat = "、".join(beats[:3])
    into = [
        f"这几页把「{title}」讲清楚了。",
        "接下来不换题目：打开 Cursor，只把刚才说的写成能跑的代码。",
    ]
    if beat:
        into.append(f"对照刚才的要点：{beat}。")
    into.append(f"跑通的样子：{goal}。" if goal else "终端里要出现真实的问和答。")
    after = [
        "刚才屏幕上跑出来的，就是 PPT 里讲的那件事。",
        "概念和代码对得上，这一章就算过了。",
    ]
    bullets = ["不换题目，只写刚才那几页"]
    if beats:
        bullets.extend(beats[:2])
    else:
        bullets.append(title)
    bullets.append(f"跑通：{goal}" if goal else "终端出现真实输出")
    return {
        "into": into,
        "after": after,
        "card": f"实操 · {title}",
        "slide": {"title": "接下来动手", "bullets": bullets[:4]},
    }


def with_practice_slide(slides: list[dict] | None, ch: dict | None = None) -> list[dict]:
    pages = [dict(p) for p in (slides or [])]
    if chapter_kind(ch) != "record":
        return pages
    last = str((pages[-1].get("title") if pages else "") or "")
    if any(mark in last for mark in PRACTICE_SLIDE_MARKS):
        return pages
    return pages + [practice_handoff(ch)["slide"]]


def as_slides(raw) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            bullets = [str(x).strip() for x in (item.get("bullets") or []) if str(x).strip()]
            if title:
                page = {"title": title, "bullets": bullets}
                code = str(item.get("code") or "").strip()
                if code:
                    page["code"] = code
                out.append(page)
        elif str(item).strip():
            out.append({"title": str(item).strip(), "bullets": []})
    return out


def chapter_kind(ch: dict | None) -> str:
    ch = ch or {}
    kind = str(ch.get("kind") or "").strip().lower()
    if kind in KINDS:
        return kind
    return guess_kind(str(ch.get("title") or ""), str(ch.get("goal") or ""), ch.get("need_code"))


def kind_label(kind: str) -> str:
    return KIND_LABEL.get(str(kind or "").strip().lower(), "解说")


def chapter_tool(ch: dict | None) -> str:
    ch = ch or {}
    kind = chapter_kind(ch)
    if kind == "ppt":
        return "ppt"
    if kind == "narrate":
        return "tts"
    tool = str(ch.get("tool") or "").strip().lower()
    if tool in {"cursor", "web", "term"}:
        return tool
    if ch.get("need_code"):
        return "cursor"
    return "web"


def tool_label(tool: str) -> str:
    return TOOL_LABEL.get(str(tool or "").strip().lower(), str(tool or ""))


def pipeline_of(plan: dict | None) -> str:
    stage = str((plan or {}).get("pipeline") or "").strip().lower()
    if stage in STAGES:
        return stage
    if (plan or {}).get("chapters"):
        return "produce"
    return "outline"


def force_narrate(plan: dict) -> dict:
    plan = dict(plan or {})
    use, skip = tool_lists(False, "none")
    plan["tools"] = {
        "use_web": False,
        "use_term": False,
        "use_cursor": False,
        "need_code": False,
        "code_via": "none",
        "use": use,
        "skip": skip,
        "gate": "review_tools",
    }
    plan["demo_files"] = []
    plan["chapter_kind"] = "narrate"
    ch = current_chapter(plan)
    script = as_script(ch.get("script") or plan.get("narration"))
    if script:
        plan["narration"] = script
        chapters = [dict(item) for item in (plan.get("chapters") or [])]
        for item in chapters:
            if int(item.get("n") or 0) == int(ch.get("n") or 0):
                item["script"] = script
                item["kind"] = "narrate"
        plan["chapters"] = chapters
    return plan


def force_ppt(plan: dict) -> dict:
    plan = dict(plan or {})
    use, skip = tool_lists(False, "none")
    plan["tools"] = {
        "use_web": False,
        "use_term": False,
        "use_cursor": False,
        "need_code": False,
        "code_via": "none",
        "use": use,
        "skip": skip,
        "gate": "review_tools",
    }
    plan["demo_files"] = []
    plan["chapter_kind"] = "ppt"
    ch = current_chapter(plan)
    script = as_script(ch.get("script") or plan.get("narration"))
    slides = as_slides(ch.get("slides") or plan.get("slides"))
    if script:
        plan["narration"] = script
    if slides:
        plan["slides"] = slides
        plan["outline"] = [{"beat": s["title"], "seconds": 12} for s in slides]
    chapters = [dict(item) for item in (plan.get("chapters") or [])]
    for item in chapters:
        if int(item.get("n") or 0) == int(ch.get("n") or 0):
            item["kind"] = "ppt"
            item["tool"] = "ppt"
            if script:
                item["script"] = script
            if slides:
                item["slides"] = slides
    plan["chapters"] = chapters
    return plan


def parse_chapters(raw, topic: str = "") -> list[dict]:
    items: list[dict] = []
    if isinstance(raw, list):
        for i, item in enumerate(raw, 1):
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("beat") or "").strip()
                goal = str(item.get("goal") or item.get("objective") or "").strip()
                need = item.get("need_code")
                try:
                    n = int(item.get("n") or i)
                except (TypeError, ValueError):
                    n = i
                kind = str(item.get("kind") or "").strip().lower()
                script = as_script(item.get("script") or item.get("lines"))
                slides = as_slides(item.get("slides"))
                beats = as_beats(item.get("beats") or item.get("points"))
                status = str(item.get("status") or "").strip() or "todo"
                video = str(item.get("video") or "").strip()
                tool = str(item.get("tool") or "").strip().lower()
            else:
                title, goal, need, n = str(item).strip(), "", None, i
                kind, script, slides, beats, status, video, tool = "", [], [], [], "todo", "", ""
            if title:
                need_code = True if need is None else bool(need)
                if kind not in KINDS:
                    kind = guess_kind(title, goal, need_code)
                row = {
                    "n": n,
                    "title": honest_text(title) or title,
                    "goal": goal,
                    "need_code": need_code if kind == "record" else False,
                    "kind": kind,
                    "script": script,
                    "slides": slides,
                    "beats": beats,
                    "status": status,
                    "video": video,
                }
                row["tool"] = tool if tool in TOOL_LABEL else chapter_tool(row)
                items.append(row)
    else:
        for i, line in enumerate(str(raw or "").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            line = re.sub(r"^\s*第?\s*\d+\s*章?\s*[\.、:：)\]）]\s*", "", line)
            parts = re.split(r"\s*[—–]\s*|\s+：\s*|\s+:\s*", line, maxsplit=1)
            title = (parts[0] or "").strip()
            goal = (parts[1] or "").strip() if len(parts) > 1 else ""
            if title:
                kind = guess_kind(title, goal, None)
                row = {
                    "n": i,
                    "title": honest_text(title) or title,
                    "goal": goal,
                    "need_code": kind == "record",
                    "kind": kind,
                    "script": [],
                    "slides": [],
                    "beats": [],
                    "status": "todo",
                    "video": "",
                }
                row["tool"] = chapter_tool(row)
                items.append(row)
    if not items:
        items = fallback_chapters(topic)
    out = []
    for i, ch in enumerate(items[:10], 1):
        row = dict(ch)
        row["n"] = i
        if row.get("kind") not in KINDS:
            row["kind"] = guess_kind(row.get("title") or "", row.get("goal") or "", row.get("need_code"))
        row["script"] = as_script(row.get("script"))
        row["slides"] = as_slides(row.get("slides"))
        row["beats"] = as_beats(row.get("beats"))
        row.setdefault("status", "todo")
        row.setdefault("video", "")
        row["tool"] = row.get("tool") if row.get("tool") in TOOL_LABEL else chapter_tool(row)
        out.append(row)
    return out


def detect_chapter(text: str) -> int:
    raw = (text or "").strip()
    hit = re.search(r"第\s*([0-9一二三四五六七八九十]+)\s*章", raw)
    if not hit:
        return 0
    token = hit.group(1)
    if token.isdigit():
        return int(token)
    table = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    return table.get(token, 0)


def current_chapter(plan: dict | None) -> dict:
    plan = plan or {}
    chapters = plan.get("chapters") or []
    try:
        n = int(plan.get("chapter") or 1)
    except (TypeError, ValueError):
        n = 1
    for ch in chapters:
        if int(ch.get("n") or 0) == n:
            return ch
    return chapters[0] if chapters else {"n": 1, "title": plan.get("title") or "本集", "goal": "", "need_code": True, "kind": "narrate"}


def set_chapter_kind(plan: dict, kind: str) -> dict:
    plan = dict(plan or {})
    kind = str(kind or "").strip().lower()
    if kind not in KINDS:
        return plan
    n = int(plan.get("chapter") or current_chapter(plan).get("n") or 1)
    chapters = []
    for item in plan.get("chapters") or []:
        row = dict(item)
        if int(row.get("n") or 0) == n:
            row["kind"] = kind
            row["need_code"] = kind == "record" and bool(row.get("need_code") or kind == "record")
            if kind == "record" and "最小" in str(row.get("title") or ""):
                row["need_code"] = True
            if kind != "record":
                row["need_code"] = False
            row["tool"] = chapter_tool(row)
            plan["chapter_kind"] = kind
        chapters.append(row)
    plan["chapters"] = chapters
    return plan


def next_todo_chapter(plan: dict | None) -> int:
    plan = plan or {}
    for ch in plan.get("chapters") or []:
        n = int(ch.get("n") or 0)
        if n and ch.get("status") not in {"ready", "skip"}:
            return n
    return 0


def course_speech(plan: dict) -> str:
    chapters = (plan or {}).get("chapters") or []
    if not chapters:
        return ""
    n = int((plan or {}).get("chapter") or 1)
    title = str((plan or {}).get("course_title") or "这门课").strip() or "这门课"
    stage = pipeline_of(plan)
    lines = [f"《{title}》拆成 {len(chapters)} 章 · 现在走到「{STAGE_LABEL.get(stage, stage)}》："]
    for ch in chapters:
        mark = " ← 本集" if int(ch.get("n") or 0) == n else ""
        ready = " · 已出片" if ch.get("status") == "ready" else ""
        tool = tool_label(ch.get("tool") or chapter_tool(ch))
        goal = f"（看完：{ch.get('goal')}）" if ch.get("goal") else ""
        lines.append(f"{ch.get('n')}. [{kind_label(chapter_kind(ch))} · {tool}] {ch.get('title')}{goal}{ready}{mark}")
        for beat in as_beats(ch.get("beats"))[:5]:
            lines.append(f"    · {beat}")
    if stage == "outline":
        lines.append("看每章要点和验收。太粗就说「第N章拆开」或改标题。点「确认大纲」之后按章出片，不用再一章章点。")
    elif stage == "materials":
        lines.append("素材已经按章备好。点「开始出片」我会把剩下的章都做完。")
    else:
        cur = current_chapter(plan)
        kind = chapter_kind(cur)
        tool = tool_label(cur.get("tool") or chapter_tool(cur))
        left = sum(1 for c in chapters if c.get("status") not in {"ready", "skip"})
        if left:
            lines.append(f"正在自动出片，还剩 {left} 章。学生端随时能看已经做好的章。")
        else:
            lines.append("各章都齐了。打开学生端按顺序看。")
        if kind == "narrate":
            lines.append("解说章：口播稿 → 配音。")
        elif kind == "ppt":
            lines.append("PPT章：组成幻灯片，再配解说。")
        else:
            lines.append(f"录屏章：先 PPT 讲概念，口播接到实操，再 Cursor（{tool}），最后对照 PPT。")
    return "\n".join(lines)


def pipeline_chips(plan: dict | None) -> list[str]:
    plan = plan or {}
    stage = pipeline_of(plan)
    if stage == "outline":
        return ["确认大纲", "改章节", "这一章改成PPT", "这一章改成录屏", "这一章改成解说"]
    if stage == "materials":
        return ["开始出片", "改口播", "改章节"]
    chips: list[str] = ["打开学生端"]
    if next_todo_chapter(plan):
        chips.insert(0, "把剩下的章做完")
    return chips


def chapter_chips(plan: dict | None) -> list[str]:
    return pipeline_chips(plan)


EDIT_LABELS = {
    "title": "标题",
    "audience": "听众",
    "objective": "目标",
    "homepage": "官网",
    "prompt": "提示词",
    "expect": "验收词",
    "rundown": "分镜",
    "narration": "口播",
    "demo_content": "演示代码",
    "demo_path": "文件名",
    "chapters": "章节",
    "course_title": "课名",
}

EDIT_CHIPS = ["重写代码", "改章节", "改标题", "改提示词", "改分镜", "改口播"]


def detect_edit(text: str) -> str:
    raw = (text or "").strip()
    table = {
        "改标题": "title",
        "改听众": "audience",
        "改目标": "objective",
        "改官网": "homepage",
        "换个网站": "homepage",
        "改提示词": "prompt",
        "改验收": "expect",
        "改验收词": "expect",
        "改分镜": "rundown",
        "改代码": "demo_content",
        "改演示代码": "demo_content",
        "重写代码": "demo_content",
        "重写演示": "demo_content",
        "重写提示词": "prompt",
        "改口播": "narration",
        "改文件名": "demo_path",
        "改章节": "chapters",
        "改课程结构": "chapters",
        "重写大纲": "regenerate",
        "重出大纲": "regenerate",
        "换个主题": "new_topic",
    }
    return table.get(raw) or table.get(raw.lower()) or ""


def merge_plan(plan: dict, patch: dict) -> dict:
    plan = dict(plan or {})
    patch = patch or {}
    for key in ("title", "audience", "objective", "homepage", "prompt", "expect"):
        if key in patch and patch[key] is not None:
            plan[key] = str(patch[key]).strip()
            if key == "prompt":
                plan["prompt_custom"] = True
    if "course_title" in patch and patch["course_title"] is not None:
        plan["course_title"] = str(patch["course_title"]).strip()
    if "chapters" in patch and patch["chapters"] is not None:
        parsed = parse_chapters(patch["chapters"], plan.get("course_title") or plan.get("title") or "")
        if parsed:
            plan["chapters"] = parsed
            try:
                n = int(plan.get("chapter") or 1)
            except (TypeError, ValueError):
                n = 1
            plan["chapter"] = max(1, min(n, len(parsed)))
    if "chapter" in patch and patch["chapter"] not in (None, ""):
        try:
            n = int(patch["chapter"])
        except (TypeError, ValueError):
            n = detect_chapter(str(patch["chapter"]))
        chapters = plan.get("chapters") or []
        if n and chapters:
            plan["chapter"] = max(1, min(n, len(chapters)))
    if "outline" in patch:
        raw = patch["outline"]
        beats = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    beat = str(item.get("beat") or "").strip()
                    if beat:
                        beats.append({"beat": beat, "seconds": int(item.get("seconds") or 20)})
                elif str(item).strip():
                    beats.append({"beat": str(item).strip(), "seconds": 20})
        else:
            for line in str(raw or "").splitlines():
                line = line.strip().lstrip("0123456789.、) ")
                if line:
                    beats.append({"beat": line, "seconds": 20})
        if beats:
            plan["outline"] = beats
    if "narration" in patch:
        raw = patch["narration"]
        if isinstance(raw, list):
            plan["narration"] = [str(x).strip() for x in raw if str(x).strip()]
        else:
            plan["narration"] = [ln.strip() for ln in str(raw).splitlines() if ln.strip()]
    if "rundown" in patch and str(patch.get("rundown") or "").strip():
        plan["rundown"] = str(patch["rundown"]).strip()
        plan["rundown_custom"] = True
    demo = dict(((plan.get("demo_files") or [{}])[0] or {}))
    changed_demo = False
    if patch.get("demo_path"):
        demo["path"] = str(patch["demo_path"]).strip().lstrip("./")
        changed_demo = True
    if "demo_content" in patch and patch["demo_content"] is not None:
        demo["content"] = str(patch["demo_content"])
        demo["path"] = demo.get("path") or "lcel_chain.py"
        plan["demo_custom"] = True
        changed_demo = True
    if changed_demo and demo.get("path"):
        plan["demo_files"] = [demo]
    if isinstance(patch.get("tools"), dict):
        t = patch["tools"]
        want_web = bool(t.get("use_web")) if "use_web" in t else bool((plan.get("tools") or {}).get("use_web"))
        if t.get("use_cursor"):
            via = "cursor"
        elif t.get("use_term"):
            via = "local"
        elif "use_cursor" in t or "use_term" in t:
            via = "none"
        else:
            via = str((plan.get("tools") or {}).get("code_via") or "none")
        use, skip = tool_lists(want_web, via)
        plan["tools"] = {
            "use_web": want_web,
            "use_term": via == "local",
            "use_cursor": via == "cursor",
            "need_code": via in {"cursor", "local"},
            "code_via": via,
            "use": use,
            "skip": skip,
            "gate": "review_tools",
        }
    if not plan.get("rundown_custom"):
        plan["rundown"] = fit_rundown(plan)
        plan["web_script"] = web_script_from(plan["rundown"])
        plan["term_script"] = term_script_from(plan)
    else:
        plan["web_script"] = web_script_from(plan.get("rundown") or "")
        plan["term_script"] = term_script_from(plan)
    if (
        (plan.get("tools") or {}).get("use_cursor")
        and not plan.get("prompt_custom")
        and "prompt" not in patch
        and not str(plan.get("prompt") or "").strip()
    ):
        prompt, expect = cursor_demo_prompt(plan)
        plan["prompt"] = prompt
        plan.setdefault("expect", expect)
    plan["confirmed"] = False
    plan["gate"] = "review_tools"
    return plan


def apply_revision(plan: dict, text: str) -> tuple[dict, str] | None:
    raw = (text or "").strip()
    if not raw or not plan:
        return None
    hit = re.match(
        r"(?:把)?(标题|听众|目标|官网|提示词|验收词|验收|分镜|口播|代码|演示代码|文件名|章节|课程结构)"
        r"(?:改成|换成|改为|是|：|:)\s*(.+)",
        raw,
        re.S,
    )
    if not hit:
        return None
    label, value = hit.group(1), hit.group(2).strip()
    key = {
        "标题": "title",
        "听众": "audience",
        "目标": "objective",
        "官网": "homepage",
        "提示词": "prompt",
        "验收词": "expect",
        "验收": "expect",
        "分镜": "rundown",
        "口播": "narration",
        "代码": "demo_content",
        "演示代码": "demo_content",
        "文件名": "demo_path",
        "章节": "chapters",
        "课程结构": "chapters",
    }[label]
    patched = merge_plan(plan, {key: value})
    return patched, f"已改{label}。再改就直说，或改卡片后保存。行了再确认这样录。"
