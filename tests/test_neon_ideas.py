"""YouTube's own captions, excited words as a signal, "clip the last N seconds", and the free local AI."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from clipper import said, ytcaptions
from clipper.config import load_config


def _cfg(tmp_path):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    return cfg


# ---- YouTube's captions ----

AUTO = {"events": [
    {"tStartMs": 1000, "dDurationMs": 3000, "segs": [{"utf8": "oh"}, {"utf8": " my", "tOffsetMs": 400}, {"utf8": " god", "tOffsetMs": 800}]},
    {"tStartMs": 4000, "dDurationMs": 100, "segs": [{"utf8": "\n"}]},
    {"tStartMs": 5000, "dDurationMs": 2000, "segs": [{"utf8": "[Music]"}]},
    {"tStartMs": 8000, "dDurationMs": 4000, "segs": [{"utf8": "[Music"}, {"utf8": " playing]", "tOffsetMs": 300}, {"utf8": " no", "tOffsetMs": 900}]},
    {"tStartMs": 13000, "dDurationMs": 2000, "segs": [{"utf8": "no"}, {"utf8": " no", "tOffsetMs": 200}, {"utf8": " no", "tOffsetMs": 400}, {"utf8": " no", "tOffsetMs": 600},
                                                       {"utf8": " यार।", "tOffsetMs": 900}]},
]}


def test_captions_become_timed_words_without_sound_notes_or_loops():
    words = ytcaptions.parse_json3(AUTO)
    assert [w["word"] for w in words] == ["oh", "my", "god", "no", "no", "no", "यार."]  # [Music] gone, the fifth "no" dropped, the Hindi full stop a dot
    assert words[0]["start"] == 1.0 and words[1]["start"] == 1.4 and words[0]["end"] == 1.4  # a word lasts until the next one starts
    assert words[2]["end"] <= words[2]["start"] + ytcaptions.MAX_WORD_S  # ... but never across a long pause
    clip = ytcaptions.window(words, 1.2, 9.0)
    assert [w["word"] for w in clip] == ["my", "god", "no"] and clip[0]["start"] == pytest.approx(0.2)  # counted from the clip's start
    assert ytcaptions.as_lines(words)[0] == {"t": 1.0, "e": 2.6, "text": "oh my god"}


def test_a_line_written_by_a_person_is_spread_over_its_time():
    words = ytcaptions.parse_json3({"events": [{"tStartMs": 0, "dDurationMs": 4000, "segs": [{"utf8": "he said no way"}]}]})
    assert [w["start"] for w in words] == [0.0, 1.0, 2.0, 3.0]


def test_the_track_in_the_language_spoken_is_chosen_never_a_translation():
    j = [{"ext": "json3", "url": "u"}]
    info = {"language": "hi", "automatic_captions": {"hi-orig": j, "hi": j, "en": j, "fr": j}, "subtitles": {"live_chat": j}}
    assert ytcaptions.pick_track(info, "hi") == ("u", "hi", "automatic")
    assert ytcaptions.pick_track(info, "ur") == ("u", "hi", "automatic")  # spoken Urdu and Hindi are one language to YouTube
    assert ytcaptions.pick_track(info, "en") is None  # English here would be a machine translation of Hindi speech
    assert ytcaptions.pick_track({"subtitles": {"en-GB": j}, "automatic_captions": {"en-orig": j}}, "en")[2] == "written"
    assert ytcaptions.pick_track({"automatic_captions": {"en": j}}, "en") is None  # no "-orig": not what was heard
    assert ytcaptions.pick_track({}, "en") is None


def test_youtube_captions_can_be_switched_off(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.transcribe.youtube_captions = "off"
    assert ytcaptions.video_words("https://www.youtube.com/watch?v=x", tmp_path, cfg) is None  # no request is made


# ---- what is said ----


def _w(text, start=0.0, gap=0.4):
    return [{"start": start + i * gap, "word": t} for i, t in enumerate(text.split())]


def test_excited_words_score_and_everyday_talk_does_not():
    calm = _w("so today we are going to drive to the shop and buy some food for the trip")
    assert said.strength(calm, 30) == 0
    wild = _w("oh my god no way bro run run police oh shit what the hell let's go")
    assert said.strength(wild, 30) >= 7
    hindi = _w("अरे यार भागो भागो पुलिस बचाओ मर गया अरे बाप रे")
    assert said.strength(hindi, 30) >= 5
    roman = _w("arre yaar bhaago bhaago pulis bachao mar gaya oh teri")
    assert said.strength(roman, 30) >= 5
    assert said.strength(_w("yaar yaar bro"), 30) < 2  # a habit word alone is not a moment


def test_moments_of_a_whole_stream_are_found_from_the_words():
    words = _w("we walk and talk about the weather " * 40, 0, 0.5) + _w("oh my god no way run run police oh shit", 300) + \
        _w("nothing much happens here at all " * 40, 320, 0.5) + _w("what the hell bro let's go insane crazy wow", 900)
    found = said.peaks(words, floor=4.0)
    assert [round(t / 100) for t, _ in found] == [3, 9] and all(s >= 4 for _, s in found)
    assert said.peaks(words, since_s=600, floor=4.0)[0][0] > 600  # the start of the stream can be left out


def test_excited_words_count_in_a_clips_score():
    from clipper.scoring import combined

    quiet = {"trigger": "chat", "score": 3.0}
    loud = {"trigger": "chat", "score": 3.0, "signals": {"said": 8.0}}
    assert combined(loud) > combined(quiet)
    assert combined({"trigger": "said", "score": 7.0, "signals": {"said": 7.0}}) > 20


def test_said_moments_join_chat_moments_or_stand_alone():
    from clipper.signals import add_signal_moments

    chat = [{"peak_s": 100.0, "start_s": 80.0, "end_s": 110.0, "score": 4.0, "rank": 1}]
    out = add_signal_moments(chat, {"said": [(95.0, 8.0), (900.0, 7.0)]}, extra=2)
    assert out[0]["trigger"] == "chat+said" and out[0]["signals"]["said"] == 8.0
    assert out[1]["trigger"] == "said" and out[1]["start_s"] < 900 < out[1]["end_s"]


# ---- clip the last N seconds ----


def test_a_clip_now_press_becomes_the_stretch_before_it(tmp_path):
    from clipper import watch

    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError, match="between 5 and 180"):
        watch.ask_clip_now(tmp_path, 2)
    watch.ask_clip_now(tmp_path, 30)
    (request,) = watch.take_asked(tmp_path)
    assert watch.take_asked(tmp_path) == []  # taken once

    class W:
        meta = {"started_at": request["at"] - 1000.0}  # the stream began 1000 s before the press
        buffer = None
        asked: list = []

    m = watch.asked_moment(W, request, cfg)
    assert m["trigger"] == "manual" and m["end_s"] == pytest.approx(1000 - cfg.watch.feed_delay_s, abs=0.2)
    assert m["end_s"] - m["start_s"] == pytest.approx(35, abs=0.2)  # the 30 s asked for and a little before
    W.asked = [m]
    assert watch.due_asked(W, cfg) == []  # the recording has not reached it yet
    W.meta = {"started_at": time.time() - m["end_s"] - cfg.fetch.pad_s - cfg.watch.vod_delay_s - 5}
    assert watch.due_asked(W, cfg) == [m] and W.asked == []


def test_an_old_press_from_before_a_restart_is_dropped(tmp_path):
    from clipper import watch

    (tmp_path / watch.ASKED_FILE).write_text(json.dumps({"at": time.time() - 900, "seconds": 30}) + "\nnot json\n", encoding="utf-8")
    assert watch.take_asked(tmp_path) == []


def test_clip_now_needs_a_stream_being_watched(tmp_path):
    from clipper.control import Control

    with pytest.raises(ValueError, match="nothing is being watched"):
        Control(_cfg(tmp_path)).clip_now({"seconds": 30})


# ---- the free local AI ----


def test_a_small_computer_never_gets_the_local_ai(tmp_path, monkeypatch):
    from clipper import localai
    from clipper.control import Control

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(localai, "_ram_gb", lambda: 4.0)
    ok, why = localai.supported(cfg)
    assert not ok and "4 GB of memory" in why
    with pytest.raises(ValueError, match="Nothing was downloaded"):
        Control(cfg).start_local_ai()
    with pytest.raises(ValueError, match="Nothing was downloaded"):
        localai.setup(cfg, lambda line: None)
    monkeypatch.setattr(localai, "_ram_gb", lambda: 16.0)
    assert localai.supported(cfg)[0]
    cfg.llm.local = "off"
    assert not localai.supported(cfg)[0]


def test_the_local_ai_writes_a_clips_text_when_claude_cannot_be_reached(tmp_path, monkeypatch):
    from clipper import llm, localai

    asked = {}

    class Fake(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"models": [{"name": "llama3.2:3b"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            asked.update(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            answer = {"hook": "He drives into the lake?!", "title": "Lake", "keep": True, "reason": "funny", "rating": 60}
            body = json.dumps({"message": {"content": json.dumps(answer)}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        cfg = _cfg(tmp_path)
        monkeypatch.setattr(localai, "HOST", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setattr(localai, "_ram_gb", lambda: 16.0)
        monkeypatch.setitem(localai._seen, "value", None)
        monkeypatch.setattr(llm, "why_unavailable", lambda cfg: "no Claude on this computer")
        assert localai.status(cfg, fresh=True)["ready"] and llm.text_available(cfg) and not llm.available(cfg)
        data = llm.ask(cfg, "claude-opus-5-5", "system", "the clip's words", llm.SCHEMA, "copy for a clip", local_ok=True)
        assert data["hook"] == "He drives into the lake?!" and llm.last_used == "local"
        assert asked["model"] == "llama3.2:3b" and asked["format"] == llm.SCHEMA and asked["messages"][1]["content"] == "the clip's words"
        with pytest.raises(llm.LlmUnavailable, match="no Claude"):  # reading a whole stream is not a job for the small model
            llm.ask(cfg, "claude-haiku-5-5", "system", "a whole stream", llm.SCHEMA, "moments")
    finally:
        server.shutdown()
        monkeypatch.setitem(localai._seen, "value", None)
