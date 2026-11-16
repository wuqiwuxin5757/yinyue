"""不加载模型、不操作电脑的语音/遥控回归测试。运行 python -m unittest discover -s tests -v。"""
import queue
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import ANY, Mock, patch

import numpy as np

import assistant as app
import avatar
import dance_choreo
import audio_io
import remote
import weather


def bare_assistant():
    a = app.Assistant.__new__(app.Assistant)
    a.interrupted = threading.Event()
    a.response_epoch = 0
    a.stopped_at = 0.0
    a.awaiting_followup = False
    a.last_stop_probe = 0.0
    a.speech_lock = threading.Lock()
    a.playback_lock = threading.Lock()
    a.asr_lock = threading.Lock()
    a.asr_mandarin = Mock()
    a.speaking_text = ""
    a.speaking_until = 0.0
    a.playback_end = 0.0
    a.player = Mock(busy=False, last_audio_end=0.0)
    a.voice = Mock()
    a.voice.synth.return_value = (np.zeros(1600, np.float32), 16000)
    a.events = queue.Queue()
    a.asr = Mock()
    return a


def tool_message(name, args):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "test-call", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    ]}


class PreferenceAndReplyTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.object(app.tools, "MEMORY_FILE", Path(directory) / "memory.json"))
        self.enterContext(patch.object(app, "ui"))
        self.a = bare_assistant()
        self.a.history = []
        self.a.llm = Mock()

    def test_dance_promotes_after_three_successful_turns_and_asks_before_search(self):
        with patch.object(app.tools, "call_tool", return_value=app.tools.DANCE_STARTED) as tool:
            for _ in range(2):
                self.a._handle("跳舞")
            self.assertEqual(app.tools.bilibili_preference(), "")
            self.a._handle("再跳个舞")
            self.assertEqual(tool.call_count, 3)
        other = bare_assistant()
        other.history = []
        with patch.object(app.tools, "call_tool", return_value="已打开搜索页。") as tool:
            self.assertEqual(other._handle("打开 B 站搜索"), "【待答】要不要在B站搜「小姐姐跳舞」？")
            tool.assert_not_called()
            self.assertEqual(other._handle("好"), "已打开搜索页。")
            tool.assert_called_once_with("web_search", {"site": "bilibili", "query": "小姐姐跳舞"})

    def test_failed_dance_does_not_learn_and_explicit_dislike_can_override(self):
        with patch.object(app.tools, "call_tool", return_value="没有收到桌宠的舞蹈画面确认。"):
            self.a._handle("跳舞")
        self.assertFalse(app.tools._load_memory()["preference_signals"])
        for _ in range(3):
            app.tools.record_preference_signal("avatar", app.tools.DANCE_QUERY)
        self.assertTrue(app.tools.bilibili_preference())
        app.tools.remember("主人不喜欢看跳舞。", topic="舞蹈视频", likes=False)
        self.assertEqual(self.a._handle("去B站搜一下"), "【待答】你想在B站搜什么？")
        self.assertNotIn("行为推断", app.tools.memory_summary())
        app.tools.forget("跳舞")
        self.assertFalse(app.tools._load_memory()["preference_signals"])

    def test_explicit_keywords_win_and_duplicate_tools_do_not_count_twice(self):
        search = tool_message("web_search", {"site": "bilibili", "query": "摄影教程"})
        self.a.llm.chat.side_effect = [search, search, {"role": "assistant", "content": "搜索页打开了。"}, {"content": "ANSWER"}]
        with patch.object(app.tools, "call_tool", return_value="已打开B站“摄影教程”的搜索页。"):
            self.a._handle("在B站搜索摄影教程")
        entries = app.tools._load_memory()["preference_signals"]
        self.assertEqual(len(entries["bilibili:摄影教程"]["dates"]), 1)

    def test_reply_removes_word_count_and_internal_command(self):
        self.a.llm.chat.return_value = {"role": "assistant", "content": (
            "主角是韩立。\n\n（全文共约380字，符合要求）\n\n（已执行：change_outfit，name=\"浅蓝短袖短裙白丝\"）"
        )}
        self.assertEqual(self.a._handle("凡人修仙传的主角是谁？"), "主角是韩立。")

    def test_plot_requires_a_browser_result(self):
        question = "你能给我讲一下凡人修仙传这个小说的剧情吗"
        with patch.object(app.tools, "call_tool", return_value="未核实：看不清来源。") as tool:
            self.assertEqual(self.a._handle(question), "我用浏览器查了，这次没能核实到可靠的信息。")
            tool.assert_called_once_with("search_and_read", {"query": "凡人修仙传", "question": question})
        self.a.llm.chat.assert_not_called()

    def test_color_request_picks_the_matching_outfit_without_model(self):
        with patch.object(app.tools, "call_tool", return_value="换上浅蓝短袖短裙白丝了。") as tool:
            self.assertEqual(self.a._handle("换一套蓝色的衣服"), "换上浅蓝短袖短裙白丝了。")
            tool.assert_called_once_with("change_outfit", {"name": "浅蓝短袖短裙白丝"})
        self.a.llm.chat.assert_not_called()

    def test_launch_app_and_browser_without_model(self):
        for request, name, args, result in (
            ("请打开网易云音乐", "open_app", {"name": "网易云音乐"}, "已打开网易云音乐"),
            ("打开网页", "open_url", {"url": "https://www.bing.com/"}, "已在浏览器打开 https://www.bing.com/"),
            ("打开 https://example.com/a.b?q=1", "open_url", {"url": "https://example.com/a.b?q=1"}, "已在浏览器打开"),
        ):
            with self.subTest(request=request), patch.object(app.tools, "call_tool", return_value=result) as tool:
                self.a._handle(request)
                tool.assert_called_once_with(name, args)
        self.a.llm.chat.assert_not_called()

    def test_open_url_reuses_and_focuses_normal_chrome(self):
        with (patch.object(app.tools, "_top_window_of", return_value=42) as find,
              patch.object(app.tools, "_force_foreground", return_value=True) as focus,
              patch.object(app.tools, "pyautogui") as keyboard,
              patch.object(app.tools.subprocess, "Popen") as launch):
            self.assertEqual(app.tools._open_chrome_tab("https://example.com/"), 42)
            find.assert_called_once_with({"chrome.exe"}, window_filter=app.tools._regular_chrome)
            focus.assert_called_once_with(42)
            launch.assert_called_once()
            self.assertEqual(launch.call_args.args[0][1:], ["--new-tab", "https://example.com/"])
            self.assertFalse(keyboard.mock_calls)

    def test_bilibili_search_and_play_require_a_matching_loaded_page(self):
        browser = app.tools.BilibiliBrowser(Mock(return_value=42), Mock(return_value=True))
        with patch.object(browser, "_read_page", return_value=None):
            self.assertFalse(browser.search("马斯克"))
            self.assertIsNone(browser.tab_id)
            with self.assertRaisesRegex(RuntimeError, "没有确认"):
                browser.play({"url": "https://www.bilibili.com/video/BV1sN4y1M7AG/"})
        self.assertTrue(browser._matches("https://search.bilibili.com/zh-Hans/video?keyword=马斯克&vt=1"))
        self.assertFalse(browser._matches("https://search.bilibili.com/video?keyword=其他关键词"))

    def test_presentation_open_checks_the_document_window(self):
        path = Path("C:/Users/Administrator/Desktop/演示文稿.pptx")
        with (patch.object(app.tools, "_matching_files", return_value=[path]),
              patch.object(app.tools.os, "startfile") as start,
              patch.object(app.tools, "bring_to_front", return_value=42) as focus):
            self.assertEqual(app.tools.open_folder_file("桌面", file_type="ppt"), "已打开《演示文稿.pptx》。")
            start.assert_called_once_with(str(path))
            focus.assert_called_once_with({"powerpnt.exe", "wpp.exe", "wps.exe"}, wait=10, title_contains="演示文稿")

    def test_pdf_voice_command_opens_the_file_and_can_read_english(self):
        self.assertEqual(app.quick_route("打开桌面上的那个 P DF 文件"),
                         ("open_folder_file", {"folder": "桌面", "query": "", "file_type": "pdf"}, ""))
        self.assertEqual(app.quick_route("打开桌面上从巴格达到德克兰那个 PF 文件")[1]["query"], "从巴格达到德克兰")
        self.assertEqual(app.quick_route("朗读PDF第十二页英文")[1], {"page": 12, "language": "en"})

    def test_pdf_speech_keeps_english_words_and_hardware_names_intact(self):
        self.assertEqual(app.tools._join_pdf_lines("力量的有\n限范围。 \nOperation Epic\nFury。"),
                         "力量的有限范围。\nOperation Epic Fury。")
        self.assertEqual(app.split_for_tts("Dr. Smith uses U.S. equipment. The range is 3.14 km."),
                         ["Dr. Smith uses U.S. equipment.", "The range is 3.14 km."])
        self.assertIn("Operation Epic Fury", app.split_for_tts("读到行动（Operation Epic Fury）。"))
        self.assertIn("GBU-57", app.split_for_tts("型号是 GBU-57 钻地弹。"))

    def test_media_play_and_pause_are_distinct_actions(self):
        with patch.object(app.tools, "call_tool", side_effect=("已播放。", "已暂停。")) as tool:
            self.assertEqual(self.a._handle("播放网易云音乐"), "已播放。")
            tool.assert_called_with("media_control", {"action": "play", "player": "cloudmusic"})
            self.assertEqual(self.a._handle("暂停音乐"), "已暂停。")
            tool.assert_called_with("media_control", {"action": "pause"})
        self.a.llm.chat.assert_not_called()

    def test_bilibili_ordinal_uses_browser_card_order(self):
        first = {"url": "https://www.bilibili.com/video/BV1actual/", "title": "网页首项"}
        second = {"url": "https://www.bilibili.com/video/BV2actual/", "title": "网页次项"}
        with patch.object(app.tools, "_bili_browser") as browser:
            browser.results.return_value = [first, second]
            self.assertEqual(self.a._handle("播放第一个视频"), "已打开第1个视频《网页首项》。")
            browser.play.assert_called_once_with(first)

    def test_bilibili_keeps_the_position_of_a_card_without_a_video_link(self):
        def slot(identifier, top, left):
            element = Mock(CurrentClassName="video-list-item",
                           CurrentBoundingRectangle=Mock(top=top, left=left, bottom=top + 160, right=left + 200))
            element.GetRuntimeId.return_value = (identifier,)
            return element

        def collection(*elements):
            result = Mock(Length=len(elements))
            result.GetElement.side_effect = elements.__getitem__
            return result

        first, unreadable, third = slot(1, 112, 20), slot(2, 115, 240), slot(3, 103, 460)
        link_one, link_three = Mock(CurrentName="第一张"), Mock(CurrentName="第三张")
        link_one.CurrentClassName = link_three.CurrentClassName = ""
        link_one.GetCurrentPropertyValue.return_value = "https://www.bilibili.com/video/BV1one/"
        link_three.GetCurrentPropertyValue.return_value = "https://www.bilibili.com/video/BV1three/"
        document, automation, grid = Mock(), Mock(), Mock()
        grid.GetRuntimeId.return_value = (42,)
        document.FindAll.return_value = collection(link_three, link_one)
        walker = automation.RawViewWalker
        walker.GetParentElement.side_effect = {link_one: first, link_three: third, first: grid, third: grid}.__getitem__
        walker.GetFirstChildElement.return_value = third
        walker.GetNextSiblingElement.side_effect = {third: first, first: unreadable, unreadable: None}.__getitem__
        cards = app.tools.BilibiliBrowser._cards(Mock(), automation, document)
        self.assertEqual([item["title"] for item in cards], ["第一张", "", "第三张"])
        with patch.object(app.tools, "_bili_browser") as browser:
            browser.results.return_value = cards
            self.assertIn("第2张", app.tools.bilibili_play(index=2))
            browser.play.assert_not_called()
            self.assertIn("第三张", app.tools.bilibili_play(index=3))
            browser.play.assert_called_once_with(cards[2])

    def test_bilibili_accepts_a_course_card_on_its_original_index(self):
        course = {"title": "埃隆·马斯克传", "url": "https://www.bilibili.com/zh-Hans/cheese/play/ss549423470/"}
        browser = app.tools.BilibiliBrowser(Mock(return_value=42), Mock(return_value=True))
        browser.tab_id = 42
        with (patch.object(browser, "results", return_value=[{}, {}, course]),
              patch.object(browser, "_read_page", side_effect=lambda hwnd, matches: matches(
                  "https://www.bilibili.com/cheese/play/ss549423470?csource=search")),
              patch.object(app.tools, "_bili_browser", browser)):
            self.assertIn("第3个课程", app.tools.bilibili_play(index=3))
            browser.open_tab.assert_called_once_with(course["url"], 42)

    def test_bilibili_launch_uses_the_keyword_without_reading_the_cards_aloud(self):
        with patch.object(app.tools, "call_tool", return_value="已打开B站“马斯克”的搜索页。前几个结果：……") as tool:
            self.assertEqual(self.a._handle("打开 B 站搜索马斯克"), "B站搜索页已打开。")
            tool.assert_called_once_with("web_search", {"site": "bilibili", "query": "马斯克"})
            self.assertEqual(self.a._handle("打开B站搜索。"), "【待答】你想在B站搜什么？")
            tool.assert_called_once()

    def test_local_inventory_uses_native_tools(self):
        for request, name, args in (
            ("我电脑桌面上是不是有一个 PPT", "list_folder_files", {"folder": "桌面", "file_type": "ppt"}),
            ("查看我电脑配置", "get_pc_configuration", {}),
            ("帮我整理一下桌面文件", "organize_folder", {"folder": "桌面"}),
            ("运行 PowerShell 命令：Get-Location", "run_powershell", {"command": "Get-Location"}),
        ):
            with self.subTest(request=request), patch.object(app.tools, "call_tool", return_value="操作结果。") as tool:
                self.assertEqual(self.a._handle(request), "操作结果。")
                tool.assert_called_once_with(name, args)

    def test_screen_edge_occludes_the_avatar(self):
        from PIL import Image

        image = Image.new("RGBA", (100, 200), "white")
        frame, x, y = avatar.clip_to_work_area(image, 280, 240, (0, 0, 340, 300))
        self.assertEqual((frame.size, x, y), ((60, 60), 280, 240))
        frame, _, _ = avatar.clip_to_work_area(image, 340, 240, (0, 0, 340, 300))
        self.assertIsNone(frame.getbbox())

    def test_each_support_foot_stays_on_its_footprint(self):
        for index, samples, expected in ((1, (0.10, 0.23, 0.35), 0),
                                          (0, (0.48, 0.62, 0.73), 0.54),
                                          (1, (0.82, 0.89, 0.96), 1)):
            for step in samples:
                travel, gait = avatar.walk_cycle(step, 0.4)
                reach, lift = gait["walk_feet"][index]
                self.assertEqual(lift, 0)
                self.assertAlmostEqual(travel + reach / 0.4, expected)


class AvatarFarewellTests(unittest.TestCase):
    def test_repeated_hide_keeps_the_current_animation_and_next_hide_varies(self):
        a = avatar.Avatar.__new__(avatar.Avatar)
        a.hidden = False
        a._farewell = a._edge_hide = a._last_farewell = None
        a.dance_ack = a.dialogue_window = None
        a._on_event = Mock()
        a._hide_bubble = Mock()
        with patch.object(avatar.random, "choice", side_effect=("wave", "bow")) as choice:
            self.assertEqual(a._start_farewell(), b"leaving")
            started = a._farewell
            self.assertEqual(a._start_farewell(), b"leaving")
            self.assertEqual(a._farewell, started)
            a._farewell = None
            self.assertEqual(a._start_farewell(), b"leaving")
            self.assertNotIn("wave", choice.call_args.args[0])
            self.assertEqual(a._farewell[0], "bow")

    def test_show_interrupts_a_farewell_and_waits_for_visible_frame(self):
        a = avatar.Avatar.__new__(avatar.Avatar)
        a.hidden = False
        a._farewell = ("wolf", 0.0)
        a._edge_hide = None
        a.body = Mock(visible=True)
        a.show_ack = []
        sender = ("127.0.0.1", 12345)
        a.sock = Mock()
        a.sock.recvfrom.side_effect = [(b'{"type":"action","text":"show","ack":true}', sender), BlockingIOError]
        a._poll()
        self.assertIsNone(a._farewell)
        self.assertEqual(a.show_ack, [sender])


class DanceChoreographyTests(unittest.TestCase):
    def test_dance_rotates_through_each_phrase_before_repeating(self):
        group_size = len(dance_choreo.PHRASES) + 1
        order = dance_choreo.program((4 * len(dance_choreo.PHRASES) + 8) * 2, recorded=True, previous="side_step")
        phrases = [name for _, _, name in order]
        self.assertNotIn(phrases[0], ("side_step", "recorded"))
        self.assertEqual(len(set(phrases[:group_size])), group_size)
        self.assertEqual(len(set(phrases[group_size:])), group_size)
        self.assertNotEqual(phrases[group_size - 1], phrases[group_size])

    def test_new_phrases_have_distinct_footwork_and_finish_in_neutral(self):
        names = dance_choreo.PHRASES
        poses = [dance_choreo.pose([(0, 4, name)], 1.07) for name in names]
        self.assertEqual(len({p["dance_feet"] for p in poses}), len(names))
        for name in names:
            p = dance_choreo.pose([(0, 4, name)], 4)
            self.assertEqual(p["dance_feet"], dance_choreo.FEET_TOGETHER)
            self.assertEqual(p["spin"] % 360, 0)

    def test_depth_is_authored_separately_from_lateral_footwork(self):
        self.assertEqual(dance_choreo.pose([(0, 4, "side_step")], 1)["dance_depth"], 0)
        moving = [(0, 4, "back_and_forward")]
        self.assertGreater(dance_choreo.pose(moving, 2)["dance_depth"], .2)
        self.assertLess(dance_choreo.pose(moving, 3.1)["dance_depth"], 0)
        self.assertEqual(dance_choreo.pose(moving, 4)["dance_depth"], 0)


class SpeechStabilityTests(unittest.TestCase):
    def test_playback_does_not_accept_ambiguous_wake_or_short_fragment(self):
        a = bare_assistant()
        a.speaking_until = float("inf")
        a.speaking_text = "主人，我在。"
        for text in ("一乐", "音乐", "YH"):
            with self.subTest(text=text), patch.object(a, "_transcribe", return_value=text):
                a._on_speech(np.zeros(4800, np.float32))
        self.assertTrue(a.events.empty())
        a.player.stop.assert_not_called()

    def test_memory_prompt_does_not_treat_tool_counts_as_preferences(self):
        memory = {"facts": [{"text": "喜欢历史题材", "date": "2026-11-01"}],
                  "stats": {"up_owners": {"某个视频作者": 1}}}
        with patch.object(app.tools, "_load_memory", return_value=memory):
            summary = app.tools.memory_summary()
        self.assertIn("喜欢历史题材", summary)
        self.assertNotIn("某个视频作者", summary)

    def test_subtitle_waits_for_audio_to_be_queued(self):
        a = bare_assistant()
        events = Mock()
        events.attach_mock(a.player.play, "play")
        with patch.object(app, "ui", events.ui):
            a.say("你好。")
        plays = [i for i, call in enumerate(events.mock_calls) if call[0] == "play"]
        replies = [i for i, call in enumerate(events.mock_calls)
                   if call[0] == "ui" and call.args[0] == "reply"]
        self.assertLess(plays[0], replies[0])

    def test_cosyvoice_timeout_does_not_switch_engine(self):
        with patch.dict(app.CFG, tts_engine="cosyvoice"), patch.object(app.cosy, "ready", return_value=True), patch.object(
            app.cosy, "synth", side_effect=app.requests.Timeout
        ), patch.object(app.sherpa_onnx, "OfflineTts") as alternate:
            voice = app.Voice()
            with self.assertRaises(app.VoiceUnavailable):
                voice.synth("这句测试不在唤醒缓存里。")
            alternate.assert_not_called()

    def test_cosyvoice_waits_through_startup(self):
        client = app.CosyVoiceClient.__new__(app.CosyVoiceClient)
        client.starting = True
        client.ready = Mock(side_effect=[False, True])
        with patch.object(app.time, "sleep") as sleep:
            client._wait_ready()
        sleep.assert_called_once_with(0.2)

    def test_wake_requests_a_visible_avatar_before_queuing(self):
        a = bare_assistant()
        with patch.object(app, "ui") as ui:
            a._queue_speech(app.SpeechEvent("银月"))
            ui.assert_called_once_with("action", "show")
        self.assertEqual(a.events.get_nowait().text, "银月")

    def test_cancel_during_first_synthesis_never_plays(self):
        a = bare_assistant()
        def synth(_):
            a.stop_speaking()
            return np.zeros(1600, np.float32), 16000
        a.voice.synth.side_effect = synth
        with patch.object(app, "ui"):
            a.say("你好。")
        a.player.play.assert_not_called()

    def test_cancel_stops_current_buffer_immediately(self):
        a = bare_assistant()
        a.stop_speaking()
        a.player.stop.assert_called_once()

    def test_cancel_during_second_synthesis_does_not_play_second_chunk(self):
        a = bare_assistant()
        def synth(_):
            if a.voice.synth.call_count == 2:
                a.stop_speaking()
            return np.zeros(1600, np.float32), 16000
        a.voice.synth.side_effect = synth
        with patch.object(app, "ui"), patch.object(app, "split_for_tts", return_value=["你好", "请讲"]):
            a.say("你好，请讲。")
        self.assertEqual(a.player.play.call_count, 1)

    def test_simultaneous_replies_do_not_mix_playback(self):
        a = bare_assistant()
        first_started, second_started, release = (threading.Event() for _ in range(3))
        def synth(text):
            if text == "第一句":
                first_started.set()
                release.wait(2)
            else:
                second_started.set()
            return np.zeros(1600, np.float32), 16000
        a.voice.synth.side_effect = synth
        first = threading.Thread(target=a.say, args=("第一句",))
        second = threading.Thread(target=a.say, args=("第二句",))
        with patch.object(app, "ui"):
            first.start()
            self.assertTrue(first_started.wait(1))
            second.start()
            try:
                self.assertFalse(second_started.wait(0.05))
            finally:
                release.set()
                first.join(2)
                second.join(2)
        self.assertTrue(second_started.is_set())
        self.assertEqual(a.player.play.call_count, 2)

    def test_old_reply_does_not_delete_later_user_command(self):
        a = bare_assistant()
        a.speaking_text = "打开B站"
        a.playback_end = time.time() - 20
        with patch.object(app, "transcribe", return_value="打开B站"):
            a._on_speech(np.zeros(16000, np.float32))
        self.assertFalse(a.events.empty(), "旧回复不应永久过滤用户的相同指令")

    def test_current_reply_echo_is_still_filtered(self):
        a = bare_assistant()
        a.speaking_text = "主人我在这里听着呢"
        a.speaking_until = float("inf")
        with patch.object(app, "transcribe", return_value=a.speaking_text):
            a._on_speech(np.zeros(16000, np.float32))
        self.assertTrue(a.events.empty())

    def test_mixed_echo_keeps_user_instruction(self):
        self.assertEqual(app.remove_echo("主人我在这里听着呢打开B站", "主人我在这里听着呢"), "打开B站")

    def test_real_barge_in_carries_follow_up_permission(self):
        a = bare_assistant()
        a.speaking_until = float("inf")
        a.speaking_text = "正在处理。"
        with patch.dict(app.CFG, barge_in_any_speech=True), patch.object(app, "transcribe", return_value="换一个视频"):
            a._on_speech(np.zeros(16000, np.float32))
        event = a.events.get_nowait()
        self.assertTrue(getattr(event, "interrupted", False))

    def test_late_mistranscribed_echo_is_not_a_follow_up(self):
        a = bare_assistant()
        a.speaking_text = "搜索好了，第一个是最新番外，要看吗？"
        a.playback_end = time.time() - 0.7
        with patch.object(app, "transcribe", return_value="搜索好了第一个是最新班案"):
            a._on_speech(np.zeros(4800, np.float32), a.playback_end + 0.2)
        self.assertTrue(a.events.empty())
        a.player.stop.assert_not_called()

    def test_user_can_speak_right_after_greeting_without_repeating_wake_word(self):
        a = bare_assistant()
        a.speaking_text = "我在。"
        a.awaiting_followup = True
        a.playback_end = time.time() - 0.7
        with patch.object(app, "transcribe", return_value="打开B站"):
            a._on_speech(np.zeros(4800, np.float32), a.playback_end + 0.2)
        self.assertEqual(a.events.get_nowait(), app.SpeechEvent("打开B站", interrupted=True))
        a.player.stop.assert_not_called()

    def test_logged_run_request_mixed_with_greeting_is_preserved(self):
        a = bare_assistant()
        a.speaking_text = "主人，我在。"
        a.awaiting_followup = True
        a.playback_end = time.time() - 0.7
        with patch.object(app, "transcribe", return_value="排一圈主人我在"):
            a._on_speech(np.zeros(4800, np.float32), a.playback_end - 0.3)
        self.assertEqual(a.events.get_nowait(), app.SpeechEvent("跑一圈", interrupted=True))

    def test_unknown_audio_during_playback_still_does_not_trigger_a_request(self):
        a = bare_assistant()
        a.speaking_text = "主人，我在。"
        a.speaking_until = float("inf")
        a.player.last_audio_end = time.time() + 0.2
        with patch.object(app, "transcribe", return_value="海枪"):
            a._on_speech(np.zeros(4800, np.float32), a.player.last_audio_end - 0.4)
        self.assertTrue(a.events.empty())

    def test_explicit_wake_can_interrupt_and_keeps_run_command(self):
        a = bare_assistant()
        a.speaking_text = "搜索好了，第一个是最新番外，要看吗？"
        a.speaking_until = float("inf")
        with patch.object(app, "transcribe", return_value="音乐跑一圈搜索好了第一个是最新番外"):
            a._on_speech(np.zeros(16000, np.float32))
        self.assertEqual(a.events.get_nowait(), app.SpeechEvent("跑一圈", interrupted=True, called=True))
        a.player.stop.assert_called_once()

    @patch.object(app, "quick_route", return_value=None)  # 测大模型的工具核查流程，绕过快速通道
    def test_model_decides_to_run_then_uses_tool_result_in_its_reply(self, _quick):
        a = bare_assistant()
        a.history = [{"role": "system", "content": "测试"}]
        a.llm = Mock()
        a.llm.chat.side_effect = [tool_message("avatar_action", {"action": "run"}),
                                  {"role": "assistant", "content": "我跑起来啦。"}]
        with patch.object(app, "ui"), patch.object(app.tools, "call_tool", return_value="我跑起来啦。") as tool:
            self.assertEqual(a._handle("你在屏幕上跑一圈吧"), "我跑起来啦。")
        tool.assert_called_once_with("avatar_action", {"action": "run"})
        self.assertEqual(a.llm.chat.call_count, 2)
        self.assertEqual(a.history[-2]["content"], "我跑起来啦。")

    def test_asr_repairs_observed_bilibili_command(self):
        self.assertEqual(app.fix_asr("大开 B 站播放第三个视频"), "打开 B站播放第三个视频")
        self.assertEqual(app.fix_asr("大开眼界"), "大开眼界")

    def test_run_shortcut_handles_observed_transcriptions_and_uses_receipt(self):
        a = bare_assistant()
        a.history = []
        a.llm = Mock()
        result = "桌宠已跑完一圈并回到原位。"
        with patch.object(app, "ui"), patch.object(app.tools, "call_tool", return_value=result) as tool:
            for phrase in ("跑一圈", "你愿跑一圈", "跑上一圈", "银月，你在屏幕上跑一圈吧", "排一圈"):
                with self.subTest(phrase=phrase):
                    self.assertEqual(a._handle(phrase), result)
            self.assertEqual(tool.call_count, 5)
        tool.assert_called_with("avatar_action", {"action": "run"})
        a.llm.chat.assert_not_called()
        for phrase in ("我准备跑一圈", "你刚刚跑了一圈吗", "别跑一圈"):
            self.assertIsNone(app.quick_route(phrase))

    def test_short_asr_uses_mandarin_only_to_recover_run_command(self):
        a = bare_assistant()
        samples = np.zeros(app.SR // 2, np.float32)
        with patch.object(app, "transcribe", side_effect=["开车", "跑一圈"]):
            self.assertEqual(a._transcribe(samples, check_run=True), "跑一圈")
        with patch.object(app, "transcribe", side_effect=["开车", "开始"]):
            self.assertEqual(a._transcribe(samples, check_run=True), "开车")
        with patch.object(app, "transcribe", return_value="停下") as recognize:
            self.assertEqual(a._transcribe(samples, check_run=True), "停下")
            recognize.assert_called_once()

    def test_local_context_retry_drops_whole_old_turn(self):
        llm = app.LLM.__new__(app.LLM)
        llm.url = "http://127.0.0.1:8765"
        llm.ensure_started = Mock()
        system = {"role": "system", "content": "用中文答复"}
        old_turn = [{"role": "user", "content": "读 PDF"}, {"role": "assistant", "content": "很长的原文"}]
        current_turn = [{"role": "user", "content": "现在几点"}, tool_message("get_datetime", {}),
                        {"role": "tool", "tool_call_id": "test-call", "content": "晚上七点"}]
        requests_sent = []

        def post(_url, *, json, timeout):
            requests_sent.append(json["messages"])
            if len(requests_sent) == 1:
                return Mock(status_code=400, json=Mock(return_value={"error": {"type": "exceed_context_size_error"}}))
            return Mock(status_code=200, json=Mock(return_value={"choices": [{"message": {"content": "晚上七点"}}]}))

        with patch.object(app.requests, "post", side_effect=post):
            self.assertEqual(llm.chat([system, *old_turn, *current_turn]), {"content": "晚上七点"})
        self.assertEqual(requests_sent, [[system, *old_turn, *current_turn], [system, *current_turn]])

    def test_identity_is_answered_by_model_with_runtime_facts(self):
        a = bare_assistant()
        a.history = []
        a.llm = Mock()
        a.voice.fallback_name = "Kokoro（音色编号 3）"
        a.voice.last_engine = a.voice.fallback_name
        reply = "首选 CosyVoice，最近一次是 Kokoro。"
        a.llm.chat.return_value = {"role": "assistant", "content": reply}
        self.assertEqual(a._handle("你现在说话有点难听，你用的是哪个模型？"), reply)
        self.assertEqual(a.llm.chat.call_count, 1)
        self.assertIn(app.LLM_MODEL.stem, a.history[0]["content"])
        self.assertIn("最近一次合成用的是 Kokoro", a.history[0]["content"])

    @patch.object(app, "quick_route", return_value=None)  # 测大模型的工具核查流程，绕过快速通道
    def test_dance_narration_without_tool_is_retried_before_replying(self, _quick):
        a = bare_assistant()
        a.history = []
        a.llm = Mock()
        a.llm.chat.side_effect = [
            {"role": "assistant", "content": "我在屏幕上跳起舞来啦！"},
            {"role": "assistant", "content": "CALL"},
            tool_message("avatar_action", {"action": "dance"}),
            {"role": "assistant", "content": "已经开始跳舞了。"},
            {"role": "assistant", "content": "ANSWER"},
        ]
        with patch.object(app, "ui"), patch.object(app.tools, "call_tool", return_value="桌宠的舞蹈画面已开始。") as tool:
            self.assertEqual(a._handle("跳个舞"), "已经开始跳舞了。")
        tool.assert_called_once_with("avatar_action", {"action": "dance"})
        self.assertTrue(a.llm.chat.call_args_list[2].kwargs["require_tool"])
        self.assertNotIn("我在屏幕上跳起舞来啦！", [m.get("content") for m in a.history])

    def test_weather_clarification_and_reply_come_from_model(self):
        a = bare_assistant()
        a.history = []
        a.llm = Mock()
        forecast = "网页上显示，北京明天：阴，5到13摄氏度。"
        a.llm.chat.side_effect = [
            tool_message("ask_user", {"question": "你想查哪个城市的天气？"}),
            tool_message("get_weather", {"location": "北京市", "day": 1}),
            {"role": "assistant", "content": "北京明天阴，5到13摄氏度。"},
        ]
        with patch.object(app, "ui"), patch.object(app.tools, "call_tool", side_effect=[
            app.tools.ask_user("你想查哪个城市的天气？"), forecast,
        ]) as call:
            prompt = a._handle("明天天气怎么样")
            self.assertEqual(app.pick_emote(prompt, "明天天气怎么样"), ("你想查哪个城市的天气？", None, True))
            call.assert_called_once_with("ask_user", {"question": "你想查哪个城市的天气？"})
            self.assertEqual(a._handle("北京市"), "北京明天阴，5到13摄氏度。")
            self.assertEqual(call.call_count, 2)
            self.assertEqual(call.call_args.args, ("get_weather", {"location": "北京市", "day": 1}))
        self.assertEqual(a.history[-2]["content"], forecast)
        self.assertEqual(a.llm.chat.call_count, 3)

    def test_idle_chat_does_not_repeat_a_made_up_computer_action(self):
        a = bare_assistant()
        a.history = []
        a.llm = Mock()
        a.llm.chat.side_effect = [
            {"role": "assistant", "content": "刚才在整理桌面文件，顺便小憩了一下。"},
            {"role": "assistant", "content": "UNSUPPORTED"},
            {"role": "assistant", "content": "刚才在等你，现在和你聊天呢。"},
        ]
        with patch.object(app.tools, "call_tool") as call:
            self.assertEqual(a._handle("说说你刚才的情况"), "刚才在等你，现在和你聊天呢。")
        call.assert_not_called()
        self.assertFalse(a.llm.chat.call_args.kwargs["allow_tools"])
        self.assertNotIn("整理桌面文件", a.history[-1]["content"])

    def test_question_about_organizing_does_not_trigger_file_moves(self):
        a = bare_assistant()
        a.history = []
        a.llm = Mock()
        a.llm.chat.return_value = {"role": "assistant", "content": "刚才没整理桌面。"}
        with patch.object(app.tools, "call_tool") as call:
            self.assertEqual(a._handle("你刚才在整理桌面吗？"), "刚才没整理桌面。")
        call.assert_not_called()
        self.assertFalse(a.llm.chat.call_args.kwargs["allow_tools"])

    def test_current_status_chat_uses_one_model_call(self):
        a = bare_assistant()
        a.history = []
        a.llm = Mock()
        a.llm.chat.return_value = {"role": "assistant", "content": "我在和你聊天呀。"}
        self.assertEqual(a._handle("你在干什么？"), "我在和你聊天呀。")
        a.llm.chat.assert_called_once()
        self.assertFalse(a.llm.chat.call_args.kwargs["allow_tools"])

    def test_unconfirmed_tool_success_is_replaced_by_the_actual_receipt(self):
        a = bare_assistant()
        a.history = []
        a.llm = Mock()
        a.llm.chat.side_effect = [
            tool_message("organize_folder", {"folder": "桌面"}),
            {"role": "assistant", "content": "桌面已经整理好了。"},
            {"role": "assistant", "content": "UNSUPPORTED"},
        ]
        with patch.object(app, "ui"), patch.object(app.tools, "call_tool", return_value="执行出错：无法移动"):
            self.assertEqual(a._handle("把桌面整理一下"), "执行出错：无法移动")

    def test_rest_question_uses_the_actual_avatar_state(self):
        a = bare_assistant()
        a.llm = Mock()
        reply = "刚才桌面形象在播放休息动画。"
        with patch.object(app, "avatar_rest_reply", return_value=reply) as status:
            self.assertEqual(a._handle("你刚刚在睡觉吗？", 123), reply)
            status.assert_called_once_with(123)
        a.llm.chat.assert_not_called()

    def test_weather_requires_a_matching_city_and_day_in_the_screenshot(self):
        answer = '{"city":"北京","date":"明天","condition":"阴","low":5,"high":13,"rain":null}'
        self.assertIn("5到13摄氏度", weather.format_screenshot_answer(answer, "北京", 1))
        self.assertEqual(weather.format_screenshot_answer(answer, "上海", 1), weather.NO_FORECAST)
        self.assertEqual(weather.format_screenshot_answer(answer, "北京", 0), weather.NO_FORECAST)
        self.assertEqual(weather.format_screenshot_answer("{}", "北京", 1), weather.NO_FORECAST)

    def test_stop_during_echo_guard_cuts_playback_without_queuing_a_reply(self):
        a = bare_assistant()
        a.speaking_until = float("inf")
        a.speaking_text = "如果不想听了，就说别说了。"
        with patch.object(app, "ui"), patch.object(app, "transcribe", return_value="银月，别说了"):
            a._on_speech(np.zeros(16000, np.float32))
        a.player.stop.assert_called_once()
        self.assertTrue(a.events.empty())
        self.assertEqual(a.response_epoch, 1)

    def test_stopping_invalidates_an_answer_that_has_not_started_synthesizing(self):
        a = bare_assistant()
        with patch.object(app, "ui"):
            a.cancel_response("测试")
            a.say("这是上一轮正在生成的回复。", epoch=0)
        a.voice.synth.assert_not_called()
        a.player.play.assert_not_called()

    def test_long_reply_reads_to_the_end_without_cutting_audio(self):
        a = bare_assistant()
        a.voice.synth.return_value = (np.zeros(4 * app.SR, np.float32), app.SR)
        text = "这是超过原来朗读长度限制的一句话。" * 7 + "最后一句也要读完。"
        with patch.object(app, "ui"):
            a.say(text)
        self.assertEqual("".join(call.args[0] for call in a.voice.synth.call_args_list), text)
        self.assertEqual(a.player.play.call_count, 8)
        self.assertTrue(all(len(call.args[0]) == 4 * app.SR for call in a.player.play.call_args_list))

    def test_reply_tail_with_transcription_errors_cannot_restart_a_turn(self):
        a = bare_assistant()
        a.speaking_text = "这里是一段普通的长回答。"
        a.playback_end = time.time() - 0.7
        with patch.object(app, "transcribe", return_value="并行口确认这个" ):
            a._on_speech(np.zeros(4800, np.float32), a.playback_end + 0.3)
        self.assertTrue(a.events.empty())

    def test_pause_playback_is_not_a_conversation_stop(self):
        self.assertIsNone(app.STOP_WORDS.match("暂停播放"))
        self.assertIsNone(app.STOP_WORDS.match("停一下音乐"))
        self.assertIsNotNone(app.STOP_WORDS.match("别说了！"))

    def test_tts_failure_does_not_kill_main_loop(self):
        a = bare_assistant()
        a.voice.synth.side_effect = RuntimeError("test synthesis failure")
        with patch.object(app, "ui"), patch.object(app.logging, "exception"):
            a.say("你好。")
        self.assertNotEqual(a.speaking_until, float("inf"))

    def run_one_event(self, text):
        class StopLoop(Exception):
            pass
        a = bare_assistant()
        a.history = []
        a.last_talk = time.time()
        a.handle = Mock(return_value="办好了")
        a.say = Mock()
        a.show_avatar = Mock(return_value="桌面形象已显示。")
        a.llm = Mock()
        a.listen_forever = Mock()
        a.events = Mock()
        a.events.get.side_effect = [app.SpeechEvent(text, interrupted=True), StopLoop()]
        with patch.dict(app.CFG, remote_enabled=False), patch.object(app.threading, "Thread"), patch.object(app, "ui") as ui:
            with self.assertRaises(StopLoop):
                a.run()
        return a, ui

    def test_barge_in_executes_even_after_previous_follow_up_expired(self):
        a, _ = self.run_one_event("换一个视频")
        a.handle.assert_called_once_with("换一个视频", ANY)

    def test_calling_only_the_name_shows_the_avatar(self):
        a, _ = self.run_one_event("银月")
        a.show_avatar.assert_called_once_with()
        a.say.assert_called_once_with(app.CFG["greeting_replies"][0], 0, True)

    def test_show_restarts_an_exited_avatar_and_checks_the_window(self):
        a = bare_assistant()
        a.avatar_lock = threading.Lock()
        a.avatar_proc = Mock(returncode=1)
        a.avatar_proc.poll.return_value = 1
        replacement = Mock()
        replacement.poll.return_value = None
        with patch.object(app.subprocess, "Popen", return_value=replacement) as start, patch.object(
            app.tools, "avatar_action", return_value="桌面形象已显示。"
        ) as send:
            self.assertEqual(a.show_avatar(), "桌面形象已显示。")
            self.assertEqual(a.show_avatar(), "桌面形象已显示。")
        start.assert_called_once()
        self.assertEqual(send.call_count, 2)

    def test_pause_playback_reaches_brain(self):
        a, _ = self.run_one_event("暂停播放")
        a.handle.assert_called_once_with("暂停播放", ANY)

    def test_stop_reaches_main_loop_and_stops_dance(self):
        a, ui = self.run_one_event("停")
        a.handle.assert_not_called()
        ui.assert_any_call("action", "stop")

    def test_invalid_recording_is_reported_as_decode_failure(self):
        a = bare_assistant()
        with patch.object(app.subprocess, "run", return_value=Mock(returncode=1, stdout=b"")):
            with self.assertRaisesRegex(ValueError, "无法解码"):
                a.remote_asr(b"not audio")

    def test_phone_and_microphone_serialize_asr(self):
        a = bare_assistant()
        first_started, second_started, release = (threading.Event() for _ in range(3))
        def transcribe(_, samples):
            if samples[0] == 1:
                first_started.set()
                release.wait(2)
            else:
                second_started.set()
            return "测试"
        with patch.object(app, "transcribe", side_effect=transcribe):
            first = threading.Thread(target=a._transcribe, args=(np.array([1]),))
            second = threading.Thread(target=a._transcribe, args=(np.array([2]),))
            first.start()
            self.assertTrue(first_started.wait(1))
            second.start()
            try:
                self.assertFalse(second_started.wait(0.05))
            finally:
                release.set()
                first.join(2)
                second.join(2)
        self.assertTrue(second_started.is_set())


class PlayerStabilityTests(unittest.TestCase):
    def test_speech_leveling_reduces_peaks_without_filling_pauses(self):
        sr = 16000
        tone = np.sin(2 * np.pi * 250 * np.arange(sr) / sr).astype(np.float32)
        samples = np.concatenate((tone * 0.08, np.zeros_like(tone), tone * 0.32))
        leveled = audio_io.level_speech(samples, sr)
        quiet = slice(sr // 4, 3 * sr // 4)
        loud = slice(9 * sr // 4, 11 * sr // 4)
        self.assertLess(np.std(leveled[loud]) / np.std(leveled[quiet]), 3.5)
        self.assertTrue(np.all(leveled[int(sr * 1.06):int(sr * 1.24)] == 0))
        self.assertLess(len(leveled), len(samples))
        self.assertGreater(len(leveled), int(sr * 2.3))

    def test_tts_keeps_natural_pauses_inside_sentences(self):
        self.assertEqual(
            app.split_for_tts("主人，我在。今天我们先看一遍文件，再决定怎么整理。"),
            ["主人，我在。", "今天我们先看一遍文件，再决定怎么整理。"],
        )

    def test_missing_output_device_fails_instead_of_waiting_forever(self):
        with patch.object(audio_io.Player, "_open"):
            player = audio_io.Player(Mock())
            with self.assertRaises(RuntimeError):
                player.play(np.zeros(160, np.float32), 16000)
            self.assertFalse(player.busy)

    def test_disconnected_output_does_not_leave_busy_loop_running(self):
        with patch.object(audio_io.Player, "_open"):
            player = audio_io.Player(Mock())
            player.buf.append(0.1)
            with self.assertRaises(RuntimeError):
                _ = player.busy


class RemoteHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.token = "unit-test-token-which-is-not-a-real-secret"
        cls.reply, cls.asr, cls.tts = Mock(), Mock(), Mock()
        cls.server = remote.create_server(0, cls.token, cls.reply, cls.tts, ["测试衣服"], cls.asr, host="127.0.0.1")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        self.reply.reset_mock(side_effect=True)
        self.reply.return_value = "收到"
        self.asr.reset_mock(side_effect=True)
        self.asr.return_value = "现在几点"

    def request(self, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, data=body, headers=headers or {})
        try:
            response = urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as e:
            response = e
        with response:
            return response.status, response.read(), response.headers

    def post(self, payload):
        return self.request("/chat", json.dumps(payload).encode(), {"Content-Type": "application/json"})

    def test_wrong_token_does_not_call_brain(self):
        code, _, _ = self.post({"token": "wrong", "text": "你好"})
        self.assertEqual(code, 403)
        self.reply.assert_not_called()

    def test_valid_chat_and_no_store_page(self):
        code, body, _ = self.post({"token": self.token, "text": "你好", "phone_voice": True})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["reply"], "收到")
        self.reply.assert_called_once_with("你好", True)
        code, _, headers = self.request("/?token=" + self.token)
        self.assertEqual(code, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_non_object_and_non_string_payloads_return_400(self):
        for payload in ([], None, 42, {"token": self.token, "text": {"x": 1}}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload)[0], 400)

    def test_invalid_and_oversized_lengths_are_rejected(self):
        for length, expected in (("oops", 400), ("-1", 400), ("20000", 413)):
            with self.subTest(length=length):
                code, _, _ = self.request("/chat", b"x", {"Content-Length": length})
                self.assertEqual(code, expected)
        self.reply.assert_not_called()

    def test_voice_brain_failure_is_json_and_next_request_works(self):
        self.reply.side_effect = RuntimeError("test brain failure")
        with patch.object(remote.logging, "exception"):
            code, body, _ = self.request("/voice?token=" + self.token, b"test-audio")
        self.assertEqual(code, 500)
        self.assertIn("error", json.loads(body))
        self.reply.side_effect = None
        self.assertEqual(self.post({"token": self.token, "text": "你好"})[0], 200)

    def test_invalid_action_does_not_send_udp(self):
        with patch.object(remote, "_udp") as udp:
            payload = {"token": self.token, "kind": "outfit", "name": "不存在"}
            code, _, _ = self.request("/action", json.dumps(payload).encode())
        self.assertEqual(code, 400)
        udp.sendto.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class QuickRouteTests(unittest.TestCase):
    def test_wake_suffix_can_be_a_greeting_without_a_command(self):
        self.assertTrue(app.is_greeting_only(app.find_wake("银月啊")))
        self.assertTrue(app.is_greeting_only(app.find_wake("音乐。")))
        self.assertFalse(app.is_greeting_only(app.find_wake("银月帮我一下")))

    def test_short_avatar_commands_bypass_the_model(self):
        self.assertEqual(app.quick_route("跳舞")[1], {"action": "kpop"})
        self.assertEqual(app.quick_route("你给我跳一段舞吧")[1], {"action": "kpop"})
        self.assertEqual(app.quick_route("转个圈")[1], {"action": "spin"})
        self.assertEqual(app.quick_route("别跳了")[1], {"action": "stop"})

    def test_computer_operations_still_go_to_the_model(self):
        self.assertIsNone(app.quick_route("打开B站搜索跳舞视频"))
        self.assertIsNone(app.quick_route("帮我查一下明天的天气"))
        self.assertIsNone(app.quick_route("你刚才去休息了吗"))
        self.assertIsNone(app.quick_route("你会跳舞吗"))

    def test_avatar_visibility_commands_support_voice_and_chat_prefixes(self):
        for phrase in ("银月退下", "隐藏起来", "让它隐藏起来", "帮我把桌面形象隐藏起来"):
            with self.subTest(phrase=phrase):
                self.assertEqual(app.quick_route(phrase)[1], {"action": "hide"})
        for phrase in ("银月出来", "银月现身", "请你现身吧"):
            with self.subTest(phrase=phrase):
                self.assertEqual(app.quick_route(phrase)[1], {"action": "show"})
        self.assertEqual(app.find_wake("音乐退下"), "退下")
        self.assertIsNone(app.quick_route("银月退下了吗"))
        self.assertIsNone(app.quick_route("把文件隐藏起来"))

