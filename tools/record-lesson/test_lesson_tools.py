#!/usr/bin/env python3
import unittest

import lesson_tools


class ToolMatchTests(unittest.TestCase):
    def test_langchain_uses_web_and_cursor(self):
        tools = lesson_tools.classify(
            {"title": "LangChain 是什么", "homepage": "https://www.langchain.com/"}
        )
        self.assertTrue(tools["use_web"])
        self.assertTrue(tools["need_code"])
        self.assertTrue(tools["use_cursor"])
        self.assertFalse(tools["use_term"])
        self.assertEqual(tools["code_via"], "cursor")
        skip = {item["id"] for item in tools["skip"]}
        self.assertIn("term", skip)
        self.assertIn("image", skip)

    def test_redis_intro_is_web_only(self):
        tools = lesson_tools.classify({"title": "Redis 是什么", "homepage": "https://redis.io/"})
        self.assertTrue(tools["use_web"])
        self.assertFalse(tools["need_code"])
        self.assertFalse(tools["use_cursor"])

    def test_cursor_topic_uses_cursor(self):
        tools = lesson_tools.classify({"title": "用 Cursor Agent 写 helloworld"})
        self.assertTrue(tools["use_cursor"])
        self.assertTrue(tools["need_code"])
        self.assertFalse(tools["use_web"])

    def test_attach_tools_writes_runnable_demo(self):
        plan = lesson_tools.attach_tools(
            {
                "title": "LangChain 是什么：一个 5 分钟官网导览",
                "homepage": "https://www.langchain.com/",
                "narration": ["大家好，今天我们用 5 分钟看看官网。", "再跑一段最小的链。"],
            }
        )
        self.assertNotIn("5 分钟", plan["title"])
        self.assertTrue(plan["demo_files"])
        self.assertIn("lcel_chain.py", plan["rundown"])
        self.assertIn("\ncursor\n", "\n" + plan["rundown"] + "\n")
        self.assertNotIn("python3 lcel_chain.py", plan["rundown"])
        self.assertIn("RunnableLambda", plan["demo_files"][0]["content"])
        self.assertIn("langchain_core", plan["prompt"])
        self.assertTrue(plan["tools"]["use_cursor"])
        self.assertFalse(plan["confirmed"])
        self.assertEqual(plan["gate"], "review_tools")

    def test_web_only_drops_term(self):
        plan = lesson_tools.attach_tools({"title": "LangChain", "homepage": "https://www.langchain.com/"})
        plan = lesson_tools.apply_choice(plan, "web_only")
        self.assertFalse(plan["tools"]["need_code"])
        self.assertNotIn("term", plan["rundown"])
        self.assertNotIn("cursor", plan["rundown"])
        self.assertEqual(plan["demo_files"], [])

    def test_switch_to_term(self):
        plan = lesson_tools.attach_tools({"title": "LangChain", "homepage": "https://www.langchain.com/"})
        plan = lesson_tools.apply_choice(plan, "use_term")
        self.assertTrue(plan["tools"]["use_term"])
        self.assertFalse(plan["tools"]["use_cursor"])
        self.assertIn("python3 lcel_chain.py", plan["rundown"])

    def test_detect_choice(self):
        self.assertEqual(lesson_tools.detect_choice("确认这样录"), "confirm_record")
        self.assertEqual(lesson_tools.detect_choice("让 Cursor 写演示"), "cursor_demo")
        self.assertEqual(lesson_tools.detect_choice("改用终端跑"), "use_term")

    def test_revise_title(self):
        plan = lesson_tools.attach_tools({"title": "LangChain", "homepage": "https://www.langchain.com/"})
        patched, say = lesson_tools.apply_revision(plan, "标题改成只讲链式调用")
        self.assertEqual(patched["title"], "只讲链式调用")
        self.assertIn("已改", say)

    def test_merge_custom_rundown(self):
        plan = lesson_tools.attach_tools({"title": "LangChain", "homepage": "https://www.langchain.com/"})
        plan = lesson_tools.merge_plan(plan, {"rundown": "card 开场\nweb\nopen https://www.langchain.com/\nlook 8"})
        self.assertTrue(plan["rundown_custom"])
        self.assertIn("look 8", plan["rundown"])
        self.assertNotIn("cursor", plan["rundown"])
        files = lesson_tools.pick_demo({"title": "LangChain"})
        body = files[0]["content"]
        self.assertNotIn("pip", body.lower())
        self.assertNotIn("ChatOpenAI", body)
        self.assertIn("RunnableLambda", body)
        self.assertIn("|", body)
        compile(body, files[0]["path"], "exec")

    def test_keeps_custom_prompt_and_lcel_files(self):
        plan = lesson_tools.attach_tools(
            {
                "title": "LangChain LCEL",
                "homepage": "https://www.langchain.com/",
                "prompt": "写一份真 LCEL，用 PromptTemplate 和 RunnableLambda。",
                "demo_files": [
                    {
                        "path": "lcel_pipe.py",
                        "content": "from langchain_core.runnables import RunnableLambda\n"
                        "from langchain_core.prompts import PromptTemplate\n"
                        "prompt = PromptTemplate.from_template('{q}')\n"
                        "model = RunnableLambda(lambda x: 'ok')\n"
                        "chain = prompt | model\n",
                    }
                ],
            }
        )
        self.assertEqual(plan["prompt"], "写一份真 LCEL，用 PromptTemplate 和 RunnableLambda。")
        self.assertEqual(plan["demo_files"][0]["path"], "lcel_pipe.py")
        self.assertIn("PromptTemplate", plan["demo_files"][0]["content"])

    def test_safe_demo_allows_langchain_core(self):
        kept = lesson_tools.safe_demo_files(
            [{"path": "a.py", "content": "from langchain_core.runnables import RunnableLambda\nchain = RunnableLambda(str)\n"}]
        )
        self.assertEqual(len(kept), 1)
        dropped = lesson_tools.safe_demo_files(
            [{"path": "a.py", "content": "from langchain_openai import ChatOpenAI\nllm = ChatOpenAI()\n"}]
        )
        self.assertEqual(dropped, [])

    def test_wants_code_rewrite(self):
        self.assertTrue(lesson_tools.wants_code_rewrite("重写下lcel代码"))
        self.assertTrue(lesson_tools.wants_code_rewrite("改成 LCEL"))
        self.assertTrue(lesson_tools.wants_code_rewrite("重写代码"))
        self.assertFalse(lesson_tools.wants_code_rewrite("确认这样录"))

    def test_cursor_prompt_not_stdlib_boilerplate(self):
        plan = lesson_tools.attach_tools({"title": "LangChain LCEL", "homepage": "https://www.langchain.com/"})
        prompt, _ = lesson_tools.cursor_demo_prompt(plan)
        self.assertNotIn("必须是标准库", prompt)
        self.assertNotIn("不要 import 第三方包", prompt)
        self.assertIn("LCEL", prompt)

    def test_fastapi_demo_is_real_framework(self):
        files = lesson_tools.pick_demo({"title": "FastAPI 入门"})
        self.assertEqual(files[0]["path"], "tiny_route.py")
        self.assertIn("from fastapi import FastAPI", files[0]["content"])
        self.assertIn("TestClient", files[0]["content"])
        self.assertNotIn("ROUTES", files[0]["content"])
        prompt, _ = lesson_tools.cursor_demo_prompt({"title": "FastAPI 入门", "demo_files": files})
        self.assertIn("TestClient", prompt)
        self.assertIn(".venv/bin/python", prompt)

    def test_chapter_cursor_prompt_appends_run(self):
        plan = {
            "course_title": "FastAPI",
            "chapter": 1,
            "chapters": [
                {
                    "n": 1,
                    "title": "写出第一个 GET 接口",
                    "goal": "能拿到 JSON",
                    "kind": "record",
                    "beats": ["FastAPI()", "@app.get", "TestClient"],
                    "prompt": "写一个 FastAPI GET /health。",
                }
            ],
        }
        prompt, expect = lesson_tools.chapter_cursor_prompt(plan)
        self.assertIn("问：", prompt)
        self.assertIn(".venv/bin/python", prompt)
        self.assertIn("必须覆盖", prompt)
        self.assertIn("不要再讲概念", prompt)
        self.assertIn("tiny_route.py", prompt)
        self.assertNotIn("tiny_chain.py", prompt)
        self.assertEqual(expect, "问：")

    def test_record_chapter_not_ready_without_cursor_take(self):
        import tempfile
        from pathlib import Path

        folder = Path(tempfile.mkdtemp(prefix="rec-ready-"))
        (folder / "lesson.mp4").write_bytes(b"x" * 2000)
        self.assertFalse(lesson_tools.chapter_media_ready(folder, "record"))
        self.assertTrue(lesson_tools.chapter_media_ready(folder, "ppt"))
        (folder / "cursor.mp4").write_bytes(b"x" * 2000)
        self.assertTrue(lesson_tools.chapter_media_ready(folder, "record"))

    def test_merge_rewrites_demo(self):
        plan = lesson_tools.attach_tools({"title": "LangChain", "homepage": "https://www.langchain.com/"})
        old = plan["demo_files"][0]["content"]
        plan = lesson_tools.merge_plan(
            plan,
            {"demo_path": "lcel_pipe.py", "demo_content": "from langchain_core.runnables import RunnablePassthrough\n"},
        )
        self.assertNotEqual(plan["demo_files"][0]["content"], old)
        self.assertEqual(plan["demo_files"][0]["path"], "lcel_pipe.py")

    def test_parse_chapters_and_detect(self):
        chapters = lesson_tools.parse_chapters(
            [
                {"n": 1, "title": "是什么", "goal": "认路", "need_code": False},
                {"title": "LCEL", "goal": "跑通管道"},
            ]
        )
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[1]["title"], "LCEL")
        text = lesson_tools.parse_chapters("1. 官网认路 — 能打开文档\n2. LCEL — 写出 Runnable")
        self.assertEqual(len(text), 2)
        self.assertEqual(text[0]["goal"], "能打开文档")
        self.assertEqual(lesson_tools.detect_chapter("录第3章"), 3)
        self.assertEqual(lesson_tools.detect_chapter("先讲第二章"), 2)
        self.assertEqual(lesson_tools.detect_chapter("做第4章"), 4)
        self.assertEqual(lesson_tools.detect_chapter("确认这样录"), 0)
        fallback = lesson_tools.fallback_chapters("LangChain")
        self.assertGreaterEqual(len(fallback), 4)

    def test_course_speech_and_merge_chapters(self):
        plan = lesson_tools.attach_tools({"title": "LangChain", "homepage": "https://www.langchain.com/"})
        plan = lesson_tools.merge_plan(
            plan,
            {
                "course_title": "LangChain 入门",
                "chapters": "1. 是什么 — 认路\n2. LCEL — 跑通\n3. 记忆 — 多轮",
                "chapter": 2,
            },
        )
        self.assertEqual(len(plan["chapters"]), 3)
        self.assertEqual(plan["chapter"], 2)
        speech = lesson_tools.course_speech(plan)
        self.assertIn("3 章", speech)
        self.assertIn("本集", speech)
        self.assertTrue("录屏" in speech or "解说" in speech or "PPT" in speech)
        chips = lesson_tools.chapter_chips(plan)
        self.assertIn("打开学生端", chips)
        self.assertTrue("确认大纲" in chips or "开始出片" in chips or "把剩下的章做完" in chips)
        self.assertEqual(lesson_tools.detect_choice("确认大纲"), "confirm_outline")
        self.assertEqual(lesson_tools.detect_choice("素材没问题，开始出片"), "confirm_materials")
        self.assertEqual(lesson_tools.detect_choice("把剩下的章做完"), "produce_chapter")
        leftover = {
            "pipeline": "produce",
            "chapter": 3,
            "chapters": [
                {"n": 1, "status": "ready", "title": "a"},
                {"n": 2, "status": "skip", "title": "b"},
                {"n": 3, "status": "todo", "title": "c"},
            ],
        }
        self.assertEqual(lesson_tools.next_todo_chapter(leftover), 3)
        self.assertEqual(lesson_tools.pipeline_chips(leftover)[0], "把剩下的章做完")
        parsed = lesson_tools.parse_chapters(
            [
                {
                    "n": 1,
                    "title": "用 LCEL 串一条链",
                    "goal": "能写出 prompt | model",
                    "kind": "record",
                    "beats": ["竖线连接", "invoke 字典", "假模型也是真写法"],
                }
            ]
        )
        self.assertEqual(parsed[0]["beats"][0], "竖线连接")
        speech = lesson_tools.course_speech({"pipeline": "outline", "course_title": "LCEL", "chapters": parsed})
        self.assertIn("竖线连接", speech)
        self.assertIn("看完：", speech)
        slides = lesson_tools.slides_for_chapter(parsed[0])
        self.assertEqual(slides[0]["title"], "用 LCEL 串一条链")
        self.assertIn("竖线连接", slides[0]["bullets"])
        fallback = lesson_tools.fallback_chapters("LangChain")
        self.assertTrue(any("LCEL" in (c.get("title") or "") for c in fallback))
        self.assertGreaterEqual(len(fallback[0]["beats"]), 3)
        self.assertEqual(lesson_tools.guess_kind("LangChain 是什么"), "ppt")
        self.assertEqual(lesson_tools.guess_kind("最小用法", "跑通", True), "record")
        outline = {"pipeline": "outline", "chapter": 1, "chapters": plan["chapters"]}
        self.assertEqual(lesson_tools.pipeline_chips(outline)[0], "确认大纲")

    def test_decide_chip_emits_outline_topic(self):
        import plan_lesson

        self.assertTrue(plan_lesson.wants_decide("帮我定"))
        self.assertTrue(plan_lesson.wants_decide("帮我想一个"))
        self.assertTrue(plan_lesson.should_emit_outline("帮我定"))
        self.assertTrue(plan_lesson.should_emit_outline("LangChain"))
        self.assertEqual(plan_lesson.resolve_topic("帮我定"), "LangChain")
        self.assertEqual(plan_lesson.resolve_topic("FastAPI 官网"), "FastAPI")
        turn = plan_lesson.interview(
            [
                {"role": "assistant", "text": plan_lesson.OPENING["say"]},
                {"role": "user", "text": "帮我定"},
            ],
            plan_lesson.OPENING["brief"],
            None,
        )
        self.assertTrue(turn["ready"])
        self.assertEqual(turn["brief"]["topic"], "LangChain")
        self.assertNotIn("帮我定", turn.get("chips") or [])

    def test_guess_kind(self):
        self.assertEqual(lesson_tools.guess_kind("LangChain 是什么"), "ppt")
        self.assertEqual(lesson_tools.guess_kind("最小用法", "跑通", True), "record")
        self.assertEqual(lesson_tools.guess_kind("常见坑和验收"), "narrate")
        chapters = lesson_tools.parse_chapters(
            [{"title": "是什么", "goal": "说明白", "kind": "narrate", "script": ["大家好。"]}]
        )
        self.assertEqual(chapters[0]["kind"], "narrate")
        self.assertEqual(chapters[0]["script"], ["大家好。"])
        ppt = lesson_tools.parse_chapters([{"title": "概念", "kind": "ppt", "slides": [{"title": "封面", "bullets": ["a"]}]}])
        self.assertEqual(ppt[0]["kind"], "ppt")
        self.assertEqual(ppt[0]["slides"][0]["title"], "封面")
        import plan_lesson
        stub = plan_lesson.stub_materials({"n": 1, "title": "是什么", "goal": "认路", "kind": "ppt"})
        self.assertEqual(stub["tool"], "ppt")
        self.assertTrue(stub["slides"])
        self.assertTrue(stub["script"])

    def test_honest_expect_and_marker(self):
        import record_lesson as rl

        plan = {
            "expect": "能运行第一个",
            "demo_files": [{"path": "a.py", "content": 'print("问：")\nprint("答：")'}],
        }
        self.assertEqual(lesson_tools.honest_expect(plan), "问：")
        self.assertTrue(rl.marker_in_result("问： 什么是 LCEL？", "问："))
        self.assertTrue(rl.marker_in_result("LCEL SAYS: HELLO", "LCEL SAYS"))
        self.assertFalse(rl.marker_in_result("我会 print hello world", "hello world"))
        self.assertTrue(rl.looks_like_goal_expect("能运行第一个"))

    def test_cursor_agent_prompt_is_ask_mode(self):
        import plan_lesson

        prompt = plan_lesson.render_prompt(
            [
                {"role": "system", "content": "只输出 JSON"},
                {"role": "user", "content": "帮我定"},
            ],
            json_mode=True,
        )
        self.assertIn("只回答", prompt)
        self.assertIn("JSON", prompt)
        self.assertIn("帮我定", prompt)
        cmd = plan_lesson.agent_cmd("ping")
        self.assertEqual(cmd[1:6], ["--print", "--mode", "ask", "--trust", "--sandbox"])
        self.assertNotIn("deepseek", " ".join(cmd).lower())

    def test_engine_switch_cursor_or_deepseek(self):
        import plan_lesson

        self.assertEqual(plan_lesson._normalize_engine("cursor-agent"), "deepseek")
        self.assertEqual(plan_lesson._normalize_engine("DeepSeek"), "deepseek")
        ids = {item["id"] for item in plan_lesson.list_engines()}
        self.assertEqual(ids, {"deepseek"})
        self.assertEqual(plan_lesson.current_engine(), "deepseek")
        if plan_lesson.deepseek_ready():
            plan_lesson.set_engine("deepseek")
            self.assertEqual(plan_lesson.current_engine(), "deepseek")
        else:
            with self.assertRaises(RuntimeError):
                plan_lesson.set_engine("deepseek")

    def test_publish_same_file_is_ok(self):
        import tempfile
        from pathlib import Path
        import course
        import studio

        root = Path(tempfile.mkdtemp(prefix="course-pub-"))
        plan = {"course_title": "LangChain", "slug": "LangChain", "chapter": 1, "chapters": [{"n": 1, "title": "是什么"}]}
        folder = course.chapter_dir(root, plan, 1)
        folder.mkdir(parents=True, exist_ok=True)
        video = folder / "lesson.mp4"
        video.write_bytes(b"x" * 2000)
        out = course.publish_folder(root, plan, folder, 1)
        self.assertTrue(video.is_file())
        self.assertTrue((folder / "timeline.json").is_file())
        self.assertEqual(out["chapters"][0]["status"], "ready")
        self.assertIn("已经在了", studio.Studio.friendly_error(Exception("are the same file")))
        self.assertIn("断了", studio.Studio.friendly_error(Exception("Error: [aborted] socket hang up")))

    def test_course_project_open_last(self):
        import tempfile
        from pathlib import Path
        import course

        root = Path(tempfile.mkdtemp(prefix="proj-"))
        plan = {
            "course_title": "FastAPI 入门",
            "slug": "FastAPI-入门",
            "pipeline": "produce",
            "chapter": 2,
            "chapters": [{"n": 1, "title": "GET", "status": "ready", "video": "ch-01/lesson.mp4"}, {"n": 2, "title": "参数", "goal": "能指出路径参数"}],
        }
        course.save_course(root, plan)
        self.assertEqual(course.last_slug(root), "FastAPI-入门")
        rows = course.list_courses(root)
        self.assertEqual(rows[0]["last"], True)
        self.assertEqual(rows[0]["course_title"], "FastAPI 入门")
        loaded = course.plan_from_saved(course.load_course(root, "FastAPI-入门"))
        self.assertEqual(loaded["pipeline"], "produce")
        self.assertEqual(loaded["title"], "参数")
        self.assertEqual(loaded["objective"], "能指出路径参数")
        course.remember_slug(root, "")
        self.assertEqual(course.last_slug(root), "")

    def test_public_course_plays_parts_without_concat(self):
        import tempfile
        from pathlib import Path
        import course

        root = Path(tempfile.mkdtemp(prefix="tl-"))
        plan = {
            "course_title": "时间轴课",
            "slug": "时间轴课",
            "chapters": [{"n": 1, "title": "POST", "kind": "record", "script": ["先讲概念。"]}],
        }
        folder = course.chapter_dir(root, plan, 1)
        (folder / "intro.mp4").write_bytes(b"i" * 2000)
        (folder / "cursor.mp4").write_bytes(b"c" * 2000)
        course.write_play_timeline(folder, [folder / "intro.mp4", folder / "cursor.mp4"])
        course.publish_folder(root, plan, folder, 1)
        pub = course.public_course(root, "时间轴课")
        clips = pub["chapters"][0]["timeline"]
        self.assertTrue(pub["chapters"][0]["ready"])
        self.assertEqual([c["id"] for c in clips], ["intro", "cursor"])
        self.assertTrue(clips[0]["src"].endswith("/ch-01/intro.mp4"))
        self.assertFalse((folder / "lesson.mp4").is_file())

    def test_timeline_slides_kind_without_video(self):
        import tempfile
        from pathlib import Path
        import course

        root = Path(tempfile.mkdtemp(prefix="slides-tl-"))
        plan = {
            "course_title": "幻灯片课",
            "slug": "幻灯片课",
            "chapters": [{"n": 1, "title": "概念", "kind": "ppt"}],
        }
        folder = course.chapter_dir(root, plan, 1)
        slides = folder / "slides"
        slides.mkdir(parents=True, exist_ok=True)
        (slides / "01.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")
        (folder / "speech.m4a").write_bytes(b"a" * 2000)
        course.write_play_timeline(
            folder,
            [
                {
                    "id": "speech",
                    "kind": "slides",
                    "title": "PPT",
                    "audio": "speech.m4a",
                    "dur": 10,
                    "pages": [{"src": "slides/01.svg", "at": 0, "dur": 10}],
                }
            ],
        )
        course.publish_folder(root, plan, folder, 1)
        pub = course.public_course(root, "幻灯片课")
        clip = pub["chapters"][0]["timeline"][0]
        self.assertEqual(clip["kind"], "slides")
        self.assertTrue(clip["audio"].endswith("/speech.m4a"))
        self.assertTrue(clip["pages"][0]["src"].endswith("/slides/01.svg"))
        self.assertNotIn("src", clip)

    def test_student_coach_saves_notes(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import course
        import student_coach

        root = Path(tempfile.mkdtemp(prefix="coach-"))
        plan = {
            "course_title": "助手课",
            "slug": "助手课",
            "chapters": [{"n": 1, "title": "入门", "script": ["先认识 FastAPI。"]}],
        }
        course.save_course(root, plan)
        with patch.object(
            student_coach.plan_lesson,
            "chat_json",
            return_value={
                "status": "卡住了",
                "understanding": 40,
                "questions": ["什么是路径参数？"],
                "blockers": ["路径参数"],
                "reply": "路径参数写在花括号里，我们下一段会演示。",
                "lecture_md": "## 路径参数\n\n写在 `{id}` 里。",
                "asset_kind": "text",
                "asset_title": "提问",
                "tags": ["路径参数"],
            },
        ):
            out = student_coach.analyze_utterance(
                root,
                slug="助手课",
                text="路径参数这段我没听懂",
                chapter=1,
                player={"t": 12, "clip_id": "speech", "clip_title": "PPT", "kind": "slides"},
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["entry"]["status"], "卡住了")
        self.assertIn("路径参数", out["summary"]["open_questions"][0])
        self.assertIn("lecture_md", out["tutor"])
        self.assertEqual(out["tutor"]["kind"], "temp_timeline")
        notes = student_coach.load_notes(root, "助手课")
        self.assertEqual(len(notes["entries"]), 1)

    def test_student_asset_ingest_and_recent_notes(self):
        import base64
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import course
        import student_coach

        root = Path(tempfile.mkdtemp(prefix="coach-asset-"))
        plan = {
            "course_title": "素材课",
            "slug": "素材课",
            "chapters": [{"n": 1, "title": "入门", "script": ["先跑通接口。"]}],
        }
        course.save_course(root, plan)
        # seed one prior note
        student_coach.save_notes(
            root,
            "素材课",
            {
                "slug": "素材课",
                "entries": [
                    {
                        "status": "卡住了",
                        "text": "上次问过 422",
                        "questions": ["为什么 422？"],
                        "reply": "校验失败会 422",
                    }
                ],
            },
        )
        code = "from fastapi import FastAPI\napp = FastAPI()\n"
        with patch.object(
            student_coach.plan_lesson,
            "chat_json",
            return_value={
                "status": "在提问",
                "understanding": 55,
                "questions": ["这段路由对吗？"],
                "blockers": [],
                "reply": "路由可以再补一个 GET。",
                "lecture_md": "## 看你的代码\n\n可以。",
                "asset_kind": "code",
                "asset_title": "tiny_route.py",
                "tags": ["FastAPI"],
            },
        ) as mocked:
            out = student_coach.analyze_utterance(
                root,
                slug="素材课",
                text="请看我上传的代码",
                chapter=1,
                player={"t": 3, "kind": "slides"},
                asset={"kind": "code", "title": "tiny_route.py", "content": code},
            )
            prompt = mocked.call_args[0][1][0]["content"]
            self.assertIn("近期学习记录", prompt)
            self.assertIn("422", prompt)
            self.assertIn("FastAPI", prompt)
        self.assertTrue(out["ok"])
        self.assertTrue(out["entry"]["asset_path"].startswith("student-assets/"))
        saved = student_coach.assets_dir(root, "素材课") / Path(out["entry"]["asset_path"]).name
        self.assertTrue(saved.is_file())
        self.assertIn("FastAPI", saved.read_text(encoding="utf-8"))

        png = base64.b64encode(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        ).decode("ascii")
        ingested = student_coach.ingest_asset(
            root,
            "素材课",
            {
                "kind": "image",
                "title": "err.png",
                "content": "这是报错截图",
                "image_data_url": "data:image/png;base64," + png,
            },
        )
        self.assertEqual(ingested["kind"], "image")
        self.assertTrue(ingested["path"].endswith(".png"))
        self.assertTrue(ingested["image_url"].startswith("/media/courses/"))

    def test_ppt_master_picks_svg_layouts(self):
        import sys
        from pathlib import Path

        studio = Path(__file__).resolve().parents[1] / "ppt-studio"
        sys.path.insert(0, str(studio))
        import generate

        files = [
            Path("01_title_slide.svg"),
            Path("02_title_content.svg"),
            Path("04_two_content.svg"),
            Path("10_hero_statement.svg"),
            Path("12_three_card.svg"),
        ]
        self.assertEqual(generate.choose_layout(files, {"bullets": ["a"]}, 1, 4).name, "01_title_slide.svg")
        self.assertEqual(generate.choose_layout(files, {"bullets": ["a", "b", "c"]}, 2, 4).name, "12_three_card.svg")
        self.assertEqual(generate.choose_layout(files, {"bullets": ["左", "右"]}, 3, 4).name, "04_two_content.svg")
        svg = '<text id="t" x="88" y="214" fill="#1E293B">甲  ·  乙  ·  丙</text>'
        out = generate.expand_joined_text(svg)
        self.assertIn("<tspan", out)
        self.assertIn("乙", out)

    def test_as_slides_keeps_code(self):
        pages = lesson_tools.as_slides(
            [{"title": "LCEL", "bullets": ["竖线"], "code": "chain = prompt | model"}]
        )
        self.assertEqual(pages[0]["code"], "chain = prompt | model")

    def test_write_pptx_uses_deepseek_lesson(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import ppt_deck

        root = Path(tempfile.mkdtemp(prefix="ppt-"))
        dest = root / "deck.pptx"
        (root / "src.pptx").write_bytes(b"pptx")
        (root / "svg").mkdir()
        (root / "png").mkdir()
        (root / "svg" / "01_cover.svg").write_text("<svg></svg>", encoding="utf-8")
        (root / "png" / "01_cover.png").write_bytes(b"png")
        fake = {
            "pptx": str(root / "src.pptx"),
            "svg_dir": str(root / "svg"),
            "png_dir": str(root / "png"),
        }
        with patch.object(ppt_deck, "_run_deepseek_lesson", return_value=fake) as run:
            out = ppt_deck.write_pptx(dest, "LCEL", [{"title": "封面", "bullets": ["a"]}])
        self.assertEqual(out, dest)
        self.assertTrue(dest.is_file())
        self.assertTrue((dest.parent / "slides" / "01_cover.png").is_file())
        payload = run.call_args[0][0]
        self.assertEqual(payload["slides"][0]["title"], "封面")
        self.assertFalse(payload["stock"])

    def test_deepseek_roster_locks_lesson_pages(self):
        import sys
        from pathlib import Path

        studio = Path(__file__).resolve().parents[1] / "ppt-studio"
        sys.path.insert(0, str(studio))
        import deepseek_generate as dg

        slides = [
            {
                "title": "消息类型",
                "bullets": ["SystemMessage"],
                "code": "from langchain_core.messages import SystemMessage",
            }
        ]
        facts = dg.facts_from_slides("LCEL", slides)
        self.assertIn("SystemMessage", facts)
        self.assertNotIn("新能源", facts)
        self.assertNotIn("乘联", facts)
        roster = dg.roster_from_slides(slides, "LCEL")
        self.assertEqual(len(roster), 1)
        self.assertIn("from langchain_core.messages", roster[0]["job"])
        locked = dg.locked_slides({"slides": slides, "facts": dg.DEFAULT_FACTS})
        self.assertEqual(locked, slides)


class PracticeHandoffTests(unittest.TestCase):
    def test_record_chapter_keeps_concept_then_adds_practice_slide(self):
        ch = {
            "title": "写出第一个 GET 接口",
            "goal": "能拿到 JSON",
            "kind": "record",
            "beats": ["FastAPI()", "@app.get", "TestClient"],
            "script": [
                "这一章先讲 FastAPI 怎么声明路由。",
                "接下来打开 Cursor 写代码。",
            ],
            "slides": [{"title": "路由", "bullets": ["@app.get"]}],
        }
        self.assertEqual(lesson_tools.concept_script(ch), ["这一章先讲 FastAPI 怎么声明路由。"])
        handoff = lesson_tools.practice_handoff(ch)
        self.assertTrue(any("打开 Cursor" in ln for ln in handoff["into"]))
        self.assertIn("FastAPI()", " ".join(handoff["into"]))
        self.assertTrue(any("PPT" in ln for ln in handoff["after"]))
        pages = lesson_tools.with_practice_slide(ch["slides"], ch)
        self.assertEqual(pages[0]["title"], "路由")
        self.assertEqual(pages[-1]["title"], "接下来动手")
        again = lesson_tools.with_practice_slide(pages, ch)
        self.assertEqual(sum(1 for p in again if p["title"] == "接下来动手"), 1)
        ppt_only = lesson_tools.with_practice_slide(
            [{"title": "概念", "bullets": ["a"]}],
            {"title": "概念", "kind": "ppt"},
        )
        self.assertEqual(len(ppt_only), 1)

    def test_practice_slide_svg(self):
        import tempfile
        from pathlib import Path
        import ppt_deck

        dest = Path(tempfile.mkdtemp(prefix="practice-")) / "practice.svg"
        ppt_deck.write_practice_slide(dest, "接下来动手", ["不换题目", "跑出问和答"])
        body = dest.read_text(encoding="utf-8")
        self.assertIn("接下来动手", body)
        self.assertIn("不换题目", body)
        self.assertIn("PPT 讲完", body)


class ConcatAudioTests(unittest.TestCase):
    def test_concat_keeps_audio_when_asked(self):
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        import assemble

        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("需要 ffmpeg")
        work = Path(tempfile.mkdtemp(prefix="concat-a-"))
        voiced = work / "voiced.mp4"
        silent = work / "silent.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0xeef4fb:s=320x180:d=0.4:r=30",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=0.4",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(voiced),
            ],
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0xd5edea:s=320x180:d=0.4:r=30",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(silent),
            ],
            check=True,
        )
        dropped = work / "dropped.mp4"
        assemble.concat([voiced, silent], dropped, keep_audio=False)
        self.assertFalse(assemble.has_audio_stream(dropped))
        kept = work / "kept.mp4"
        assemble.concat([voiced, silent], kept, keep_audio=True)
        self.assertTrue(assemble.has_audio_stream(kept))


if __name__ == "__main__":
    unittest.main()
