#!/usr/bin/env python3
"""Course webpage demo: open, search, click, type, scroll, pause, then optionally record.

Script (one action per line):

  open https://www.langchain.com/
  look 2
  click Start building
  type langchain
  enter
  baidu langchain
  scroll 4
  scroll-to Evaluation
  hover Docs
  back
  top
  wait 1

Chinese verbs work too: 打开 / 看 / 点击 / 输入 / 搜索 / 滑动 / 回车 / 返回 / 顶部
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import record_lesson as rl

Emit = Callable[[str], None]

OVERLAY_JS = (
    "(function(){if(window.__lessonHud)return 'ok';window.__lessonHud=true;"
    "var css=document.createElement('style');css.textContent="
    "'.lesson-ripple{position:fixed;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;"
    "border:3px solid #d6453d;pointer-events:none;z-index:2147483646;animation:lessonRipple .55s ease-out forwards}"
    "@keyframes lessonRipple{to{transform:scale(3.2);opacity:0}}"
    ".lesson-toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:rgba(26,35,50,.88);"
    "color:#fff;padding:8px 16px;border-radius:999px;font:16px/1.3 sans-serif;z-index:2147483646;pointer-events:none}"
    ".lesson-card{position:fixed;inset:0;background:#eef4fb;color:#1a2332;display:flex;align-items:center;"
    "justify-content:center;font:600 42px/1.35 sans-serif;z-index:2147483645;text-align:center;padding:48px}"
    ".lesson-card.fail{background:#fff4f2;color:#a32018}';"
    "document.documentElement.appendChild(css);"
    "document.addEventListener('click',function(e){var d=document.createElement('div');d.className='lesson-ripple';"
    "d.style.left=e.clientX+'px';d.style.top=e.clientY+'px';document.body.appendChild(d);"
    "setTimeout(function(){d.remove();},600);},true);"
    "window.__lessonToast=function(t){var el=document.querySelector('.lesson-toast');"
    "if(!el){el=document.createElement('div');el.className='lesson-toast';document.body.appendChild(el);}"
    "el.textContent=t;el.style.opacity='1';clearTimeout(window.__lessonToastT);"
    "window.__lessonToastT=setTimeout(function(){el.style.opacity='0';},1200);};"
    "window.__lessonCard=function(t,fail){var el=document.querySelector('.lesson-card');"
    "if(!el){el=document.createElement('div');el.className='lesson-card';document.body.appendChild(el);}"
    "el.className='lesson-card'+(fail?' fail':'');el.textContent=t;el.style.display='flex';return 'ok';};"
    "window.__lessonCardHide=function(){var el=document.querySelector('.lesson-card');if(el)el.remove();return 'ok';};"
    "return 'ok';})()"
)


def inject_overlay() -> None:
    _js(OVERLAY_JS)


def show_page_card(text: str, fail: bool = False, secs: float = 2.2) -> None:
    inject_overlay()
    _js("window.__lessonCard(" + json.dumps(text or "看这里") + "," + ("true" if fail else "false") + ")")
    time.sleep(secs)
    _js("window.__lessonCardHide()")


def toast(text: str) -> None:
    _js("window.__lessonToast&&window.__lessonToast(" + json.dumps(text) + ")")

ALIASES = {
    "打开": "open",
    "open": "open",
    "等待": "wait",
    "wait": "wait",
    "看": "look",
    "look": "look",
    "停顿": "look",
    "给学生看": "look",
    "滑动": "scroll",
    "下滑": "scroll",
    "scroll": "scroll",
    "上滑": "scrollup",
    "scrollup": "scrollup",
    "点击": "click",
    "click": "click",
    "输入": "type",
    "type": "type",
    "搜索": "search",
    "search": "search",
    "百度": "baidu",
    "baidu": "baidu",
    "谷歌": "google",
    "google": "google",
    "回车": "enter",
    "enter": "enter",
    "返回": "back",
    "back": "back",
    "顶部": "top",
    "top": "top",
    "悬停": "hover",
    "hover": "hover",
    "跳到": "scrollto",
    "滚到": "scrollto",
    "scroll-to": "scrollto",
    "scrollto": "scrollto",
    "章节": "card",
    "card": "card",
    "片头": "card",
    "失败": "fail",
    "错题": "fail",
    "fail": "fail",
}


def parse_script(text: str) -> list[dict]:
    steps: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cmd, _, rest = line.partition(" ")
        key = ALIASES.get(cmd, ALIASES.get(cmd.lower()))
        if key is None:
            raise RuntimeError(f"不认识这步：{line}")
        steps.append({"cmd": key, "arg": rest.strip(), "raw": line})
    if not steps:
        raise RuntimeError("演示步骤是空的。")
    return steps


def _js(code: str) -> bool:
    return rl.chrome_js(code)


def _key(code: int) -> None:
    rl.raise_process((rl.pick_browser_window() or {}).get("owner") or "Google Chrome")
    time.sleep(0.15)
    rl.run_osascript(f'tell application "System Events" to key code {code}\n')


def run_step(step: dict, emit: Emit | None = None) -> None:
    cmd = step["cmd"]
    arg = step.get("arg") or ""
    log = emit or (lambda m: print(m, flush=True))

    if cmd == "open":
        if not arg:
            raise RuntimeError("打开 后面要跟网址")
        log(f"打开 {arg}")
        rl.open_browser(arg)
        time.sleep(0.4)
        inject_overlay()
        return
    inject_overlay()
    if cmd in {"wait", "look"}:
        secs = float(arg or "1.5")
        log("给学生看一眼" if cmd == "look" else f"等 {secs}s")
        time.sleep(secs)
        return
    if cmd == "scroll":
        parts = arg.split()
        n = 4
        if parts and parts[0].replace(".", "", 1).isdigit():
            n = max(1, int(float(parts[0])))
        log(f"往下滑动 {n} 次")
        rl.scroll_front_browser(n, 0.85)
        return
    if cmd == "scrollup":
        n = max(1, int(float(arg or "3")))
        log(f"往上滑动 {n} 次")
        for _ in range(n):
            _js("window.scrollBy(0, -Math.round(Math.max(window.innerHeight, 700) * 0.8))")
            time.sleep(0.7)
        return
    if cmd == "scrollto":
        if not arg:
            raise RuntimeError("跳到 后面要跟页面上的字")
        log(f"滚到「{arg}」")
        ok = _js(
            "(function(t){t=(t||'').trim();var nodes=Array.from(document.querySelectorAll('a,button,h1,h2,h3,h4,p,span,li,div'));"
            "var el=nodes.find(e=>((e.innerText||'').replace(/\\s+/g,' ').includes(t)) && (e.innerText||'').trim().length<80);"
            "if(!el) return 'miss'; el.scrollIntoView({block:'center',behavior:'smooth'}); return 'ok';})("
            + json.dumps(arg)
            + ")"
        )
        if not ok:
            log("滚到失败，继续")
        time.sleep(0.8)
        return
    if cmd == "click":
        if not arg:
            raise RuntimeError("点击 后面要跟按钮文字或选择器")
        log(f"点击「{arg}」")
        toast("点击 " + arg)
        _js(
            "(function(t){t=(t||'').trim();var el=null;"
            "if(t.startsWith('#')||t.startsWith('.')||t.includes('[')){el=document.querySelector(t);}"
            "if(!el){var nodes=Array.from(document.querySelectorAll('a,button,[role=button],input[type=submit],input[type=button],[role=tab],summary,label'));"
            "el=nodes.find(e=>((e.innerText||e.value||e.getAttribute('aria-label')||'').replace(/\\s+/g,' ')).includes(t));}"
            "if(!el) return 'miss'; el.scrollIntoView({block:'center',behavior:'smooth'}); el.click(); return 'ok';})("
            + json.dumps(arg)
            + ")"
        )
        time.sleep(1.1)
        return
    if cmd == "hover":
        if not arg:
            raise RuntimeError("悬停 后面要跟文字")
        log(f"悬停「{arg}」")
        _js(
            "(function(t){var nodes=Array.from(document.querySelectorAll('a,button,[role=button],nav *'));"
            "var el=nodes.find(e=>((e.innerText||'').replace(/\\s+/g,' ')).includes(t));"
            "if(!el) return 'miss'; el.scrollIntoView({block:'center'});"
            "el.dispatchEvent(new MouseEvent('mouseover',{bubbles:true})); return 'ok';})("
            + json.dumps(arg)
            + ")"
        )
        time.sleep(1.0)
        return
    if cmd == "type":
        if not arg:
            raise RuntimeError("输入 后面要跟文字")
        log(f"输入「{arg}」")
        toast("输入 " + arg)
        _js(
            "(function(t){var el=document.activeElement;"
            "if(!el||!/INPUT|TEXTAREA/.test(el.tagName)){"
            "el=document.querySelector('#chat-textarea,#kw,input[name=wd],input[type=search],textarea,input:not([type=hidden]):not([type=submit])');}"
            "if(!el) return 'miss'; el.focus(); el.value=t;"
            "el.dispatchEvent(new Event('input',{bubbles:true})); return 'ok';})("
            + json.dumps(arg)
            + ")"
        )
        time.sleep(0.4)
        return
    if cmd == "search":
        log(f"当前页搜索「{arg}」")
        _js(
            "(function(t){var box=document.querySelector('input[type=search],input[name=q],input[name=wd],#kw,#chat-textarea,textarea,input:not([type=hidden])');"
            "if(box){box.focus();box.value=t;box.dispatchEvent(new Event('input',{bubbles:true}));"
            "var form=box.form; if(form){form.submit(); return 'ok';}}"
            "var btn=document.querySelector('button[type=submit],input[type=submit],#su');"
            "if(btn){btn.click(); return 'ok';} return 'miss';})("
            + json.dumps(arg)
            + ")"
        )
        time.sleep(2.0)
        return
    if cmd == "baidu":
        log(f"百度搜索「{arg}」")
        rl.type_baidu_search(arg)
        time.sleep(2.4)
        return
    if cmd == "google":
        log(f"谷歌搜索「{arg}」")
        rl.open_browser("https://www.google.com/search?q=" + quote(arg))
        time.sleep(1.6)
        return
    if cmd == "enter":
        log("回车")
        _key(36)
        time.sleep(0.8)
        return
    if cmd == "back":
        log("返回上一页")
        _js("history.back()")
        time.sleep(1.2)
        return
    if cmd == "top":
        log("回到顶部")
        _js("window.scrollTo({top:0,behavior:'smooth'})")
        time.sleep(0.8)
        return
    if cmd == "card":
        title = arg or "看这里"
        log(f"章节：{title}")
        show_page_card(title, fail=False)
        return
    if cmd == "fail":
        title = arg or "这里会失败，先看错的"
        log(f"错题镜：{title}")
        show_page_card(title, fail=True, secs=2.4)
        return
    raise RuntimeError(f"还没做这步：{cmd}")


def run_steps_timed(steps: list[dict], emit: Emit | None = None, t0: float | None = None) -> list[dict]:
    log = emit or (lambda m: print(m, flush=True))
    started = t0 if t0 is not None else time.time()
    events: list[dict] = []
    for step in steps:
        events.append({"t": round(time.time() - started, 2), **step, "say": step.get("raw")})
        log(f"→ {step['raw']}")
        run_step(step, emit)
    return events


def run_script(text: str, emit: Emit | None = None) -> list[dict]:
    return run_steps_timed(parse_script(text), emit)


def begin_capture(output: Path):
    pick = rl.pick_browser_window()
    if pick is None:
        raise RuntimeError("找不到浏览器窗口。先打开 Chrome / Safari / Arc。")
    rl.raise_process(str(pick["owner"]))
    time.sleep(0.4)
    label = f"{pick['owner']} · {pick['name']}"
    rec = rl.start_capture_window_id(output, int(pick["id"]), label)
    if rec is None:
        raise RuntimeError("浏览器窗口录制没起来。看一下屏幕录制权限。")
    return rec, label


def record_script(text: str, output: Path, emit: Emit | None = None) -> tuple[Path, list[dict]]:
    log = emit or (lambda m: print(m, flush=True))
    steps = parse_script(text)
    first = steps[0]
    rest = steps
    if first["cmd"] == "open":
        run_step(first, emit)
        rest = steps[1:]
    rec, label = begin_capture(output)
    log(f"开始录：{label}")
    t0 = time.time()
    events: list[dict] = []
    if first["cmd"] == "open":
        events.append({"t": 0, **first, "say": first.get("raw")})
    try:
        time.sleep(1.0)
        inject_overlay()
        events.extend(run_steps_timed(rest, emit, t0))
        time.sleep(1.0)
    finally:
        rl.stop_ffmpeg(rec)
    path = rec.mp4_path or rec.mov_path or output
    log(f"录屏已保存：{path}")
    return Path(path), events


PRESETS = {
    "open-scroll": "open {url}\nlook 2\nscroll 5\nlook 1.2",
    "baidu": "open https://www.baidu.com/\nlook 1.5\nbaidu {query}\nlook 2.5\nscroll 3\nlook 1",
    "click-scroll": "open {url}\nlook 2\nclick {click}\nlook 2\nscroll 3\nlook 1",
    "search-on-page": "open {url}\nlook 1.5\ntype {query}\nenter\nlook 2.5\nscroll 3\nlook 1",
}


def fill_preset(name: str, url: str, query: str, click: str) -> str:
    tmpl = PRESETS.get(name)
    if not tmpl:
        raise RuntimeError(f"没有这种预设：{name}")
    return tmpl.format(
        url=url or "https://www.langchain.com/",
        query=query or "langchain",
        click=click or "Start building",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="录一段网页课程演示")
    parser.add_argument("--script", type=Path, default=None)
    parser.add_argument("-c", "--commands", default="")
    parser.add_argument("--preset", default="")
    parser.add_argument("--url", default="https://www.langchain.com/")
    parser.add_argument("--query", default="langchain")
    parser.add_argument("--click", default="Start building")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.script:
        text = args.script.read_text(encoding="utf-8")
    elif args.commands:
        text = args.commands.replace("\\n", "\n")
    elif args.preset:
        text = fill_preset(args.preset, args.url, args.query, args.click)
    else:
        raise SystemExit("请传 --script、-c 或 --preset")
    if args.record:
        out = args.output or (rl.project_root(None) / "recordings" / time.strftime("%Y-%m-%d-%H%M%S") / "lesson.mp4")
        record_script(text, out)
    else:
        run_script(text)


if __name__ == "__main__":
    main()
