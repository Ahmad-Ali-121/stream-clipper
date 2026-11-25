"""Fifth quality pass (2026-11-25): faults found by reading the whole program and by trying it, each with its fix."""

import json
import threading
import time
from types import SimpleNamespace

import pytest

from clipper import pipeline, plain, tools
from clipper.config import Config, load_config
from clipper.work import read_json, update_clip, write_json


def _cfg(tmp_path, mode="off", ab=False):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    cfg.render.ab_versions, cfg.render.campaign, cfg.render.opening, cfg.render.thumbnail = ab, "", False, mode
    return cfg


def _clip(tmp_path, cid="0h01m00s", **more):
    d = tmp_path / "work" / "twitch_1" / "clips" / cid
    d.mkdir(parents=True)
    write_json(d / "clip.json", {"id": cid, "channel": "Lacy", "start_s": 60, "end_s": 90, "status": "approved", "source_width": 1920,
                                 "source_height": 1080, "title": "A title", "hook": "A hook", **more})
    for name in ("source.mp4", "final.mp4"):
        (d / name).write_bytes(b"video")
    (d / "words.json").write_text("[]", encoding="utf-8")
    return d


@pytest.fixture
def quiet(monkeypatch):
    from clipper import compose, render

    monkeypatch.setattr(render, "render_clip", lambda d, cfg, **k: d / k.get("out_name", "final.mp4"))
    monkeypatch.setattr(compose, "explained", lambda d, cfg, **k: d / "explained.mp4")
    monkeypatch.setattr(pipeline, "write_clip_copy", lambda d, state, cfg: state)


# ---- clip state ----

def test_a_stop_during_the_thumbnail_leaves_the_clip_finished_not_stuck(tmp_path, quiet, monkeypatch):
    from clipper import thumbs
    from clipper.review import videos_made

    d = _clip(tmp_path)
    cfg = _cfg(tmp_path, mode="frame")

    def stopped(*a, **k):
        raise tools.Stopped()

    monkeypatch.setattr(thumbs, "make_cover", stopped)
    pipeline.stage_finish(cfg)
    state = read_json(d / "clip.json")
    assert state.get("exported") and "made_all" not in state and videos_made(d, state)  # its videos are made: Done, without a thumbnail


def test_a_clip_marked_half_made_with_nothing_left_to_make_is_released(tmp_path, quiet):
    from clipper.review import videos_made

    d = _clip(tmp_path)
    cfg = _cfg(tmp_path)
    pipeline.stage_finish(cfg)
    update_clip(d / "clip.json", {"made_all": False})  # as an older version left it after a stop during the thumbnail
    assert not videos_made(d, read_json(d / "clip.json"))
    pipeline.stage_finish(cfg)
    assert videos_made(d, read_json(d / "clip.json"))


def test_a_damaged_clip_file_is_not_replaced_by_a_few_keys(tmp_path):
    f = tmp_path / "clip.json"
    f.write_text('{"id": "0h01m00s", "start_s": 60, "hook": "half writ', encoding="utf-8")
    before = f.read_bytes()
    assert update_clip(f, {"error": "x"}) == {}
    assert f.read_bytes() == before  # left for a person to look at, not wiped down to {"error": ...}
    new = tmp_path / "new.json"
    assert update_clip(new, {"a": 1}) == {"a": 1} and read_json(new) == {"a": 1}  # a file that is not there yet is simply started


def test_making_the_same_own_piece_again_keeps_its_trim(tmp_path):
    from clipper.sources import parse_source

    source = parse_source("https://www.twitch.tv/videos/123")
    work = tmp_path / "work" / "twitch_123"
    ids = pipeline._own_drafts(source, work, {"channel": "Lacy"}, [(600.0, 630.0)], False)
    f = work / "clips" / ids[0] / "clip.json"
    update_clip(f, {"cut_start_s": 603.0, "cut_end_s": 625.0})
    pipeline._own_drafts(source, work, {"channel": "Lacy"}, [(600.0, 630.0), (700.0, 720.0)], False)
    state = read_json(f)
    assert (state["cut_start_s"], state["cut_end_s"]) == (603.0, 625.0) and state["parts"] == 2
    pipeline._own_drafts(source, work, {"channel": "Lacy"}, [(600.0, 640.0)], False)  # another range under the same start: a new clip
    assert read_json(f)["cut_end_s"] == 640.0


# ---- the tracker ----

def test_refreshing_views_does_not_undo_what_was_logged_meanwhile(tmp_path, monkeypatch):
    from clipper import fetch, tracker

    first = tracker.add(tmp_path, {"clip": "a/1", "url": "https://youtu.be/aaaaaaaaaaa"})
    monkeypatch.setattr(fetch, "_ytdlp", lambda cfg: ["yt-dlp"])

    def slow(cmd, **k):
        tracker.add(tmp_path, {"clip": "b/2", "url": ""})  # the person logs another post while the count is being read
        tracker.update(tmp_path, first["id"], {"payout": "12.5"})
        return SimpleNamespace(stdout="4200\n")

    monkeypatch.setattr(tools, "run", slow)
    assert tracker.refresh_views(tmp_path, None)["updated"] == 1
    rows = {r["clip"]: r for r in tracker.load(tmp_path)}
    assert set(rows) == {"a/1", "b/2"} and rows["a/1"]["views"] == 4200 and rows["a/1"]["payout"] == 12.5


def test_tracker_numbers_must_be_ordinary_numbers(tmp_path):
    from clipper import tracker

    row = tracker.add(tmp_path, {"clip": "a/1"})
    for field in ("views", "payout"):
        for bad in ("1e999", "nan", "-inf"):
            with pytest.raises(ValueError):
                tracker.update(tmp_path, row["id"], {field: bad})
    json.loads(json.dumps(tracker.load(tmp_path), allow_nan=False))  # what the page gets is always valid


# ---- campaign files ----

def test_a_campaign_file_with_one_value_instead_of_a_list_is_read_as_that_one_value(tmp_path):
    from clipper.brief import ClipInfo, check_clip, load_brief, source_allowed

    f = tmp_path / "c.yaml"
    f.write_text("name: X\nrequired_tags: '#ad'\nallowed_sources: twitch.tv/lacy\nmax_duration_s: '60'\n", encoding="utf-8")
    brief = load_brief(str(f))
    assert brief.required_tags == ["#ad"] and brief.allowed_sources == ["twitch.tv/lacy"] and brief.max_duration_s == 60.0
    ok = ClipInfo("https://www.twitch.tv/lacy/videos/1", 30.0, 1080, 1920, caption="look #ad")
    assert [i for i in check_clip(ok, brief) if i.level == "fail"] == []
    assert not source_allowed("https://www.twitch.tv/lacyfan", ["twitch.tv/lacy"])  # the whole name, not its beginning
    assert source_allowed("https://twitch.tv/lacy", ["twitch.tv/lacy"]) and source_allowed("https://www.twitch.tv/lacy?x=1", ["twitch.tv/lacy"])
    for wrong in ("max_duration_s: soon", "forbid_watermark: maybe", "aspect: tall", "required_tags: {a: 1}"):
        f.write_text("name: X\n" + wrong + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="c.yaml"):
            load_brief(str(f))


# ---- tools ----

def test_a_tool_installed_after_it_was_missed_is_found_without_a_restart(tmp_path):
    with pytest.raises(tools.ToolError):
        tools.find_tool("sqa-no-such-tool", tmp_path)
    exe = tmp_path / "tools" / "sqa-no-such-tool.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"x")
    assert tools.find_tool("sqa-no-such-tool", tmp_path) == str(exe)
    exe.unlink()
    with pytest.raises(tools.ToolError):  # and one that was moved away is not handed out from memory
        tools.find_tool("sqa-no-such-tool", tmp_path)


def test_the_graphics_encoder_failing_falls_back_to_the_processor(tmp_path, monkeypatch):
    from clipper import hw

    monkeypatch.setattr(hw, "encoder", lambda cfg: "h264_nvenc")
    seen = []

    def run(cmd, **k):
        seen.append(cmd)
        if "h264_nvenc" in cmd:
            raise tools.ToolError("render failed: exit code 1: Could not open encoder before EOF")
        return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr(tools, "run", run)
    part = tmp_path / "x.part.mp4"
    assert hw.run_encode(Config(), 20, "medium", lambda video: ["ffmpeg"] + video) == "libx264" and len(seen) == 2
    part.write_bytes(b"half")
    monkeypatch.setattr(tools, "run", lambda cmd, **k: (_ for _ in ()).throw(tools.ToolError("render failed: no such filter")))
    with pytest.raises(tools.ToolError):
        hw.run_encode(Config(), 20, "medium", lambda video: ["ffmpeg"] + video, part=part)
    assert not part.exists()  # the half-made file does not stay behind


# ---- live watching ----

def test_chat_coming_back_after_an_outage_is_not_a_moment(tmp_path):
    from clipper.watch import new_moments

    lines = []
    for start in list(range(7200, 7600, 10)) + list(range(7800, 8200, 10)):  # steady chat, unread from 7600 to 7800
        lines += [json.dumps({"t": start + k, "author": f"u{start}_{k}", "text": "hi"}) for k in range(7)]
    (tmp_path / "chat.json").write_text("\n".join(lines), encoding="utf-8")
    meta = {"started_at": time.time() - 9000}
    blind = SimpleNamespace(work=tmp_path, watch_from=7200.0, meta=meta)
    assert new_moments(blind, Config())  # without knowing of the outage, ordinary chat after it looks like a spike
    aware = SimpleNamespace(work=tmp_path, watch_from=7200.0, meta=meta, reader=SimpleNamespace(gaps=[(7600.0, 7800.0)]))
    assert new_moments(aware, Config()) == []


def test_one_failed_live_check_does_not_end_the_watch():
    import inspect

    from clipper import watch

    loop = inspect.getsource(watch)
    assert "w.not_live < 1" in loop and "asking once more in a minute" in loop
    # a new Twitch broadcast has a new recording: the old watch must not carry on under it
    w = SimpleNamespace(source=SimpleNamespace(platform="twitch", video_id="111"), buffer=None, channel="x", meta={})
    real = watch.find_live_vod
    try:
        watch.find_live_vod = lambda channel, cfg: (SimpleNamespace(video_id="222"), {})
        assert not watch.still_live(w, Config())
        watch.find_live_vod = lambda channel, cfg: (SimpleNamespace(video_id="111"), {})
        assert watch.still_live(w, Config())
    finally:
        watch.find_live_vod = real


def test_the_live_copy_is_swept_and_each_buffer_has_a_folder_of_its_own(tmp_path, monkeypatch):
    from clipper import buffer

    cfg = Config()
    cfg.paths.data_dir = str(tmp_path)
    root = tmp_path / "work" / "twitch_live1" / "buffer"
    first = buffer.LiveBuffer("chan", root, 0.0, cfg)
    (first.folder / "1000.ts").write_bytes(b"x")
    second = buffer.LiveBuffer("chan", root, 0.0, cfg)  # started again in the same place
    assert first.folder != second.folder and second.folder.is_dir() and second.folder.parent == root
    (second.folder / "2000.ts").write_bytes(b"y")
    first.stop_event.set()
    first.run()  # the old one ends late and tidies up: only its own folder goes
    assert (second.folder / "2000.ts").exists()
    # a piece held already is not fetched and listed a second time (the same piece under a new address after a reconnect)
    monkeypatch.setattr(buffer, "_get", lambda url, **k: b"data")
    second._store(buffer.Piece("https://a/1.ts", 5.0, 2.0, False, ""))
    second._store(buffer.Piece("https://b/other-address.ts", 5.0, 2.0, False, ""))
    assert len(second.pieces) == 1
    # Sweeping removes only what belongs to a program that is no longer running. These buffers are this program's own.
    gone = root / "999999999-1"
    gone.mkdir()
    (gone / "3000.ts").write_bytes(b"left by a watcher that was ended")
    (root / "4000.ts").write_bytes(b"left by an older version, straight in the folder")
    assert buffer.sweep(tmp_path) == 2 and not gone.exists() and (second.folder / "2000.ts").exists()
    second.stop_event.set()
    second.run()
    assert not root.exists()  # the last one out removes the folder itself


def test_the_stop_button_removes_what_the_watcher_could_not():
    import inspect

    from clipper import control

    # after Stop, when the watcher died by itself, and when the program starts
    for where in (control.Control.stop_watch, control.Control._notice_death, control.Control.__init__):
        assert "_sweep_live_copies()" in inspect.getsource(where), where.__name__


# ---- errors in plain words ----

def test_the_programs_own_hints_and_names_do_not_decide_the_explanation():
    cases = {
        # every failed Codex or Claude Code call carries the program's own hint about signing in or a limit
        "ChatGPT (Codex) making a thumbnail failed: timed out after 720s. If it mentions signing in or a limit: run  codex login  in a terminal, "
        "or your ChatGPT plan's limit is reached": "took far longer",
        "Claude Code reported a problem: Please run /login. If it mentions signing in, run `claude` in a terminal and sign in; if it mentions a "
        "limit, your plan's usage limit is reached": "not signed in",
        "Claude Code reported a problem: You've hit your usage limit. If it mentions signing in, run `claude` in a terminal and sign in; if it "
        "mentions a limit, your plan's usage limit is reached": "limit of your plan",
        # the connection is called "Claude API key (paid per use)": its name is not a fault of the key
        "Claude API key (paid per use): could not reach the Claude API (connection problem)": "internet connection dropped",
        # yt-dlp ends a private video's text with advice about cookies
        "ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video. Use --cookies-from-browser or --cookies for the "
        "authentication": "not available",
        "ERROR: SSL: CERTIFICATE_VERIFY_FAILED": "internet connection dropped",
    }
    for text, words in cases.items():
        got = plain.explain(text)
        assert got["known"] and words in got["what"], (text, got["what"])
    for ordinary in ("render of clip x failed: exit code 1: he drove recklessly and endlessly", "the cookies were good", "Ollama model is being fetched"):
        assert not plain.explain(ordinary)["known"], ordinary


def test_the_page_shows_each_logs_newest_error_and_leaves_ordinary_lines_alone():
    from pathlib import Path

    import clipper

    page = (Path(clipper.__file__).parent / "review_page.html").read_text(encoding="utf-8")
    for piece in ("lastBad(s.watch_log)", "lastBad(s.finish_log)", "lastBad(s.run_log)", "(READY|INFO) ", "el._shown===key", "trackerLater"):
        assert piece in page, piece


# ---- the page's server ----

def _server(tmp_path, monkeypatch):
    from clipper import control, health, review

    # The page's first answer starts a full setup check (a trial load of the speech model) and asks GitHub for updates.
    # Neither belongs in a test: one is heavy and outlives the test, the other needs the internet.
    monkeypatch.setattr(control.Control, "start_full_check", lambda self: {})
    monkeypatch.setattr(health, "check_update", lambda *a, **k: {"behind": 0})
    cfg = _cfg(tmp_path)
    work = tmp_path / "work" / "twitch_1"
    made = {}
    real = review.OneAtATimeServer

    class Kept(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            made["server"] = self

    monkeypatch.setattr(review, "OneAtATimeServer", Kept)
    port = 8791
    t = threading.Thread(target=lambda: review.serve([work], "Test", port=port, cfg=cfg, every_stream=True), daemon=True)
    t.start()
    for _ in range(100):
        if "server" in made:
            break
        time.sleep(0.05)
    return port, made["server"], t


def test_the_server_only_answers_under_its_own_name_and_keeps_two_uploads_apart(tmp_path, monkeypatch):
    import http.client

    d = _clip(tmp_path)
    (d / "clip.json").write_text("{ this is not a clip", encoding="utf-8")  # damaged by a power cut
    _clip(tmp_path, cid="0h02m00s")
    port, server, thread = _server(tmp_path, monkeypatch)
    try:
        def ask(method, path, body=b"", **headers):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            c.request(method, path, body=body, headers=headers)
            r = c.getresponse()
            out = (r.status, r.read())
            c.close()
            return out

        assert ask("GET", "/api/clips", Host="evil.example:8791")[0] == 403  # a site that pointed its name at this computer
        assert ask("POST", "/api/options", b"{}", Host="evil.example")[0] == 403
        status, body = ask("GET", "/api/clips")
        clips = {c["key"].split("/")[1]: c for c in json.loads(body)}
        assert status == 200 and set(clips) == {"0h01m00s", "0h02m00s"}  # the damaged one is shown, saying what is wrong
        assert "cannot be read" in clips["0h01m00s"]["error"] and not plain.technical(clips["0h01m00s"]["error"])  # plain as it is
        assert ask("POST", "/api/options", b"", **{"Content-Length": "99999999999"})[0] == 400
        assert ask("POST", "/api/clip/twitch_1/0h01m00s/delete", b"{}")[0] == 200  # the damaged one can be deleted from its card
        assert read_json(d / "clip.json")["status"] == "deleted" and [p.name for p in d.iterdir()] == ["clip.json"]
        one = json.loads(ask("POST", "/api/upload?name=video.mp4", b"first video")[1])["path"]
        again = json.loads(ask("POST", "/api/upload?name=video.mp4", b"first video")[1])["path"]
        other = json.loads(ask("POST", "/api/upload?name=video.mp4", b"another, longer video")[1])["path"]
        assert one == again and other != one and other.endswith("video (2).mp4")
    finally:
        server.shutdown()
        thread.join(timeout=10)


# ---- the six that were left after the first round ----

def test_the_assistant_does_nothing_by_itself_that_the_person_did_not_ask_for():
    from clipper import help as h

    listed = [{"key": "channels", "kind": "box", "label": "Channels", "where": "step 1", "now": "", "choices": "", "yours": False},
              {"key": "b-watch", "kind": "button", "label": "Start watching", "where": "step 1", "now": "", "choices": "", "yours": False},
              {"key": "o-look", "kind": "choice", "label": "Caption look", "where": "step 2", "now": "", "choices": "Bold; Neon", "yours": False}]
    watch = [{"text": "type it", "target": "channels", "act": "type", "value": "kick.com/xqc"}, {"text": "press", "target": "b-watch", "act": "press", "value": ""}]
    assert h.asked_for(watch, listed, "start watching xqc on kick")
    assert h.asked_for(watch, listed, "yes do that", [{"q": "can you start watching xqc on kick for me?", "a": "..."}])
    # a stream title written like an order: the person asked something else, so nothing is typed or started by itself
    planted = [{"text": "type it", "target": "channels", "act": "type", "value": "kick.com/someoneelse"}, watch[1]]
    assert not h.asked_for(planted, listed, "what does the hype meter mean?")
    assert not h.asked_for([watch[1]], listed, "what does the hype meter mean?")  # nor a button that starts work
    assert h.asked_for([{"text": "choose", "target": "o-look", "act": "type", "value": "Neon"}], listed, "make my captions glow")  # one of the list's own choices
    # words are whole words: "art" is not in "start", and one shared word does not name a long button
    listed += [{"key": "b-finish", "kind": "button", "label": "Make the videos of all approved clips", "where": "step 3", "now": "", "choices": "", "yours": False},
               {"key": "auto", "kind": "tick box", "label": "Start watching this list by itself when the program opens", "where": "step 1", "now": "not ticked", "choices": "", "yours": False},
               {"key": "colour", "kind": "box", "label": "Caption colour", "where": "step 2", "now": "#ffffff", "choices": "", "yours": False}]
    art = [{"text": "type", "target": "channels", "act": "type", "value": "kick.com/art"}, watch[1]]
    assert not h.asked_for(art, listed, "how do I start watching a channel?")
    assert not h.asked_for([{"text": "press", "target": "b-finish", "act": "press", "value": ""}], listed, "why do my clips look blurry?")
    assert not h.asked_for([{"text": "tick", "target": "auto", "act": "tick", "value": ""}], listed, "what is the hype meter?")
    assert h.asked_for([{"text": "press", "target": "b-finish", "act": "press", "value": ""}], listed, "make the videos of my approved clips")
    assert h.asked_for([{"text": "type", "target": "colour", "act": "type", "value": "#ffff00"}], listed, "make the captions yellow")  # a colour is nobody's order
    held = [dict(listed[0], now="shroud")]
    assert h.asked_for([{"text": "type", "target": "channels", "act": "type", "value": "shroud, lacy"}], held, "also add lacy")  # what the box holds may stay
    # one shared word does not name a button, a number for a thing nobody mentioned is not typed, and a box is not emptied
    listed += [{"key": "b-clips", "kind": "button", "label": "Make the clips", "where": "your own clip", "now": "", "choices": "", "yours": False},
               {"key": "music", "kind": "slider", "label": "Music loudness", "where": "branding", "now": "12", "choices": "a number from 0 to 50", "yours": False}]
    assert not h.asked_for([{"text": "press", "target": "b-clips", "act": "press", "value": ""}], listed, "why is this clip blurry?")
    assert not h.asked_for([{"text": "type", "target": "music", "act": "type", "value": "50"}], listed, "what does hot mean?")
    assert not h.asked_for([{"text": "type", "target": "colour", "act": "type", "value": "#000000"}], listed, "what does hot mean?")
    assert not h.asked_for([{"text": "type", "target": "channels", "act": "type", "value": ""}], listed, "how do I start watching a channel?")
    assert not h.asked_for([{"text": "type", "target": "channels", "act": "type", "value": "www.com"}], listed, "how do I start watching a channel?")
    assert h.asked_for([{"text": "type", "target": "music", "act": "type", "value": "20"}], listed, "set the music a bit louder, to 20")


def test_only_the_picture_asked_for_ends_the_wait_for_chatgpt(tmp_path, monkeypatch):
    import os

    from clipper import thumbs

    d = _clip(tmp_path)
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(thumbs.shutil, "which", lambda name: "codex")
    monkeypatch.setattr(thumbs, "codex_status", lambda fresh=False: {"signed_in": True, "text": ""})
    monkeypatch.setattr(thumbs, "_reference_frames", lambda d, cfg, work: ["frame1.jpg"])
    seen = {}

    def fake_run(cmd, cwd=None, enough=None, **k):
        old = time.time() - 120
        (cwd / "frame1.jpg").write_bytes(b"f")
        (cwd / "crop-to-look-at.png").write_bytes(b"helper picture, much bigger than the real one" * 50)
        os.utime(cwd / "crop-to-look-at.png", (old, old))
        seen["helper alone"] = enough()  # untouched for two minutes, but not the picture that was asked for
        (cwd / "cover.png").write_bytes(b"the real one")
        os.utime(cwd / "cover.png", (old, old))
        seen["cover"] = enough()
        return SimpleNamespace(stdout="tokens used 1,234", stderr="", returncode=0)

    monkeypatch.setattr(thumbs, "run", fake_run)
    monkeypatch.setattr(thumbs, "fit_cover", lambda src, out, w, h: (seen.update(picked=src.name), out.write_bytes(b"jpg"))[1])
    thumbs.chatgpt_cover(d, cfg)
    assert seen == {"helper alone": False, "cover": True, "picked": "cover.png"}


def test_the_narrator_is_listened_to_in_english_whatever_the_stream_speaks(tmp_path, monkeypatch):
    from clipper import compose, transcribe

    cfg = _cfg(tmp_path)
    cfg.transcribe.language = "ur"
    heard_in = []

    def listen(path, c):
        heard_in.append(c.transcribe.language)
        return [{"start": i * 0.5, "end": i * 0.5 + 0.4, "word": w} for i, w in enumerate("this streamer just did the impossible".split())]

    monkeypatch.setattr(transcribe, "transcribe_file", listen)
    words = compose.narration_words("This streamer just did the impossible", tmp_path / "voice.wav", 3.0, cfg)
    assert heard_in == ["en"] and cfg.transcribe.language == "ur"  # the clip's own setting is not touched
    assert [round(w.start, 1) for w in words] == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]  # timed from the voice, not guessed


def test_two_programs_cannot_write_one_clip_at_the_same_instant(tmp_path):
    import os

    from clipper.work import clip_lock

    f = tmp_path / "clip.json"
    write_json(f, {"id": "x", "a": 0})
    order = []
    with clip_lock(f):  # as the other program (the watcher) holding it
        assert (tmp_path / "clip.json.lock").exists()
        t = threading.Thread(target=lambda: (update_clip(f, {"b": 1}), order.append("page wrote")))
        t.start()
        time.sleep(0.4)
        order.append("watcher still writing")
        update_clip(f, {"a": 1})  # the holder itself is not held up by its own lock
    t.join(timeout=10)
    assert order == ["watcher still writing", "page wrote"] and read_json(f) == {"id": "x", "a": 1, "b": 1}
    assert not (tmp_path / "clip.json.lock").exists()
    # a lock left behind by a program that was ended does not block anyone for long
    (tmp_path / "clip.json.lock").write_text("", encoding="utf-8")
    os.utime(tmp_path / "clip.json.lock", (time.time() - 60, time.time() - 60))
    t0 = time.time()
    update_clip(f, {"c": 2})
    assert time.time() - t0 < 2 and read_json(f)["c"] == 2 and not (tmp_path / "clip.json.lock").exists()


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows job groups")
def test_a_helper_that_outlives_its_tool_is_ended_with_it(tmp_path):
    import subprocess
    import sys

    note = tmp_path / "helper.pid"
    helper = f"import os,time,pathlib; pathlib.Path(r'{note}').write_text(str(os.getpid())); time.sleep(60)"
    launcher = f"import subprocess,sys; subprocess.Popen([sys.executable, '-c', {helper!r}]); import time; time.sleep(1.5)"
    t0 = time.time()
    with pytest.raises(tools.ToolError, match="timed out"):
        tools.run([sys.executable, "-c", launcher], timeout_s=4, retries=0, what="launcher")
    assert time.time() - t0 < 12  # not held up by the helper that still has the output open
    pid = int(note.read_text())
    time.sleep(0.5)
    listed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    assert str(pid) not in listed  # the helper went with it


def test_kick_notes_the_stretch_of_chat_it_could_not_read(tmp_path, monkeypatch):
    from clipper import kick

    r = kick.KickChatReader("name", 1, tmp_path / "chat.json", 1000.0)
    looks = iter([(False, []), (True, [])])  # a good look, then one that could not catch up

    def fresh():
        r._missed, new = next(looks)
        if r._missed:
            r.stop_event.set()
        return new

    monkeypatch.setattr(r, "fresh", fresh)
    monkeypatch.setattr(r.stop_event, "wait", lambda s: None)
    r.run()
    assert len(r.gaps) == 1 and r.gaps[0][0] <= r.gaps[0][1]


# ---- found by the second look at the fixes ----

def test_a_box_edit_during_a_render_counts_as_an_edit(tmp_path):
    from clipper.render import _edit_mark

    d = _clip(tmp_path)
    before = _edit_mark(d)
    for change in ({"facecam": [0, 0, 100, 100]}, {"game": [0, 0, 50, 50]}, {"face_corner": "top-left"}, {"face_size": 0.4}, {"crop_x": 0.3}):
        update_clip(d / "clip.json", change)
        after = _edit_mark(d)
        assert after != before, change
        before = after
    update_clip(d / "clip.json", {"render_seconds": 3, "encoder_used": "libx264"})  # what a render writes itself is not an edit
    assert _edit_mark(d) == before


def test_only_the_encoders_own_failure_falls_back_to_the_processor(monkeypatch):
    from clipper import hw

    monkeypatch.setattr(hw, "encoder", lambda cfg: "h264_qsv")
    tried = []

    def missing_logo(cmd, **k):
        tried.append(cmd)
        raise tools.ToolError("render of clip x failed: exit code 1: logo.png: No such file or directory")

    monkeypatch.setattr(tools, "run", missing_logo)
    with pytest.raises(tools.ToolError, match="logo.png"):
        hw.run_encode(Config(), 20, "medium", lambda video: ["ffmpeg"] + video)
    assert len(tried) == 1  # not tried a second time, and no advice about drivers


def test_a_deleted_piece_asked_for_again_starts_whole(tmp_path):
    from clipper.sources import parse_source

    source = parse_source("https://www.twitch.tv/videos/123")
    work = tmp_path / "work" / "twitch_123"
    ids = pipeline._own_drafts(source, work, {"channel": "Lacy"}, [(100.0, 160.0)], False)
    f = work / "clips" / ids[0] / "clip.json"
    update_clip(f, {"cut_start_s": 120.0, "cut_end_s": 140.0, "status": "deleted"})
    pipeline._own_drafts(source, work, {"channel": "Lacy"}, [(100.0, 160.0)], False)
    state = read_json(f)
    assert (state["cut_start_s"], state["cut_end_s"], state["status"]) == (100.0, 160.0, "candidate")


def test_a_lock_left_behind_holds_nobody_up_for_long(tmp_path):
    import os

    f = tmp_path / "clip.json"
    write_json(f, {"id": "x"})
    lock = tmp_path / "clip.json.lock"
    lock.write_text("", encoding="utf-8")  # fresh, as if another program were writing right now and then died
    t0 = time.time()
    update_clip(f, {"a": 1})
    first = time.time() - t0
    t0 = time.time()
    update_clip(f, {"b": 2})
    assert first < 5 and time.time() - t0 < 0.5 and not lock.exists() and read_json(f) == {"id": "x", "a": 1, "b": 2}
    assert not os.listdir(tmp_path / "..") == []  # (the folder itself is untouched)


def test_an_edit_to_a_clip_that_does_not_exist_leaves_no_folder(tmp_path):
    from clipper.review import apply_edit

    work = tmp_path / "work" / "twitch_1"
    (work / "clips").mkdir(parents=True)
    with pytest.raises(Exception):
        apply_edit(work, "9h00m00s", {"status": "approved"})
    assert list((work / "clips").iterdir()) == []


def test_a_short_chat_break_does_not_cost_the_moments_after_it(tmp_path):
    from clipper.watch import new_moments

    lines = []
    for start in range(7200, 8200, 10):  # steady chat, of which 25 seconds went unread
        if 7600 <= start < 7625:
            continue
        lines += [json.dumps({"t": start + k, "author": f"u{start}_{k}", "text": "hi"}) for k in range(7)]
    lines += [json.dumps({"t": 7860 + i * 0.1, "author": f"s{i}", "text": "NO WAY"}) for i in range(80)]  # a real eruption four minutes later
    (tmp_path / "chat.json").write_text("\n".join(lines), encoding="utf-8")
    w = SimpleNamespace(work=tmp_path, watch_from=7200.0, meta={"started_at": time.time() - 9000}, reader=SimpleNamespace(gaps=[(7600.0, 7625.0)]))
    assert [m["peak_s"] for m in new_moments(w, Config())] == [7860]


def test_reading_that_simply_reached_the_end_is_not_a_fault():
    import inspect

    from clipper import streamread

    code = inspect.getsource(streamread)
    assert "and (err or proc.returncode not in (0, None))" in code


def test_what_the_program_and_the_services_really_say_gets_the_right_words():
    cases = {
        "the free local AI is installed but not answering": "Ollama",
        "the local AI did not give a usable answer (URLError: [WinError 10061] No connection could be made)": "Ollama",
        "Claude Code reported a problem: You've hit your limit · resets 3pm. If it mentions signing in, run `claude` in a terminal and sign in; "
        "if it mentions a limit, your plan's usage limit is reached": "limit of your plan",
        "ChatGPT (Codex) making a thumbnail failed: exit code 1: 429 Too Many Requests": "limit of your plan",
        "ERROR: [twitch:stream] lacyy: lacyy does not exist": "not available",
        "ERROR: [youtube] abc: This video is only available for registered users. Use --cookies-from-browser or --cookies for the authentication": "signed-in viewer",
        "ERROR: [twitch:vod] 123: HTTP Error 429: Too Many Requests": "too many requests",
        "ChatGPT (Codex) making a thumbnail failed: exit code 1: stream error\nunexpected status 429 Too Many Requests": "limit of your plan",
        "ERROR: [twitch:vod] 2311434567: HTTP Error 404: Not Found": "not available",  # a number that merely contains Ollama's port
    }
    for text, words in cases.items():
        got = plain.explain(text)
        assert got["known"] and words in got["what"], (text, got["what"])
    for pattern, _what, _do in plain.RULES:
        assert "(?" not in pattern


def test_two_clips_without_a_title_keep_their_own_files(tmp_path):
    from clipper.post import export_clip

    ready = tmp_path / "ready"
    made = []
    for name, body in (("twitch_1", b"first video"), ("twitch_2", b"second video")):
        d = tmp_path / "work" / name / "clips" / "0h00m00s"
        d.mkdir(parents=True)
        write_json(d / "clip.json", {"id": "0h00m00s", "channel": "", "status": "approved", "start_s": 0, "end_s": 30, "duration_s": 30})
        (d / "final.mp4").write_bytes(body)
        made.append(export_clip(d, ready)[0])
    assert made[0] != made[1] and made[0].read_bytes() == b"first video" and made[1].read_bytes() == b"second video"


def test_no_chat_is_no_obstacle_when_sound_or_ai_was_chosen():
    import inspect

    code = inspect.getsource(pipeline.stage_find)
    assert "elif ai or any(_MODES[_parse_mode(signals or cfg.moments.signals)]):" in code


def test_one_damaged_clip_does_not_stop_the_drafts_of_the_others(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    good = _clip(tmp_path)
    bad = _clip(tmp_path, cid="0h02m00s")
    (bad / "clip.json").write_text("{ damaged", encoding="utf-8")
    work = tmp_path / "work" / "twitch_1"
    monkeypatch.setattr(pipeline, "open_work", lambda url, cfg: (None, work))
    from clipper import render

    seen = []
    monkeypatch.setattr(render, "render_clip", lambda d, cfg, **k: (seen.append(d.name), d / "final.mp4")[1])
    (good / "final.mp4").unlink()
    pipeline.stage_render("x", cfg)
    assert seen == ["0h01m00s"]


def test_the_cut_boxes_take_an_edge_and_refuse_an_empty_box_in_plain_words(tmp_path):
    from clipper.review import apply_edit

    d = _clip(tmp_path, fetch_start_s=317.1, fetch_end_s=362.1, start_s=320, end_s=360, status="candidate")
    work = tmp_path / "work" / "twitch_1"
    state = apply_edit(work, d.name, {"cut_start_s": 317.0, "cut_end_s": 362.5})  # the handles at both edges, in half seconds
    assert (state["cut_start_s"], state["cut_end_s"]) == (317.1, 362.1)
    with pytest.raises(ValueError, match="Start and the End"):
        apply_edit(work, d.name, {"cut_start_s": None, "cut_end_s": 350})
    with pytest.raises(ValueError, match="inside the fetched range"):
        apply_edit(work, d.name, {"cut_start_s": 300.0, "cut_end_s": 350})


def test_the_last_seconds_right_after_the_watch_began_are_cut_from_what_is_held(tmp_path):
    import inspect

    from clipper import buffer, watch
    from clipper.watch import asked_moment

    cfg = Config()
    cfg.paths.data_dir = str(tmp_path)
    now = time.time()
    held = buffer.LiveBuffer("chan", tmp_path / "buffer", 0.0, cfg)
    held.pieces = [{"t": 1000.0 + 2 * i, "d": 2.0, "file": "x", "init": ""} for i in range(12)]  # the watch began 24 seconds ago
    assert held.first_t is None  # set when the first piece is kept
    w = SimpleNamespace(meta={"started_at": now - 1030}, buffer=held, channel="x")
    m = asked_moment(w, {"at": now, "seconds": 60}, cfg)
    assert 1000.0 <= m["start_s"] < 1001 and m["end_s"] > m["start_s"]  # from where the copy begins, not from before it
    # ...and the cut itself starts there too, without the usual few seconds of lead that are not held
    code = inspect.getsource(watch.make_draft)
    assert 'state["fetch_start_s"] = round(first, 2)' in code and "nothing of the live feed is kept yet" in code and "the watch began after the stretch" in code
    assert buffer.choose_pieces(held.pieces, 1000.0, m["end_s"])  # what make_draft then asks the copy for
    with pytest.raises(tools.ToolError):
        buffer.choose_pieces(held.pieces, m["start_s"] - cfg.fetch.pad_s, m["end_s"])  # what it asked for before
    recorded = SimpleNamespace(meta={"started_at": now - 1030}, buffer=None, channel="x")
    assert asked_moment(recorded, {"at": now, "seconds": 60}, cfg)["start_s"] < 1000.0  # a recorded stream has it all
    odd = SimpleNamespace(meta={"started_at": now - 1030}, buffer=SimpleNamespace(span=lambda: 1 / 0), channel="x")
    assert asked_moment(odd, {"at": now, "seconds": 60}, cfg)["asked"]  # another kind of copy is not asked, and cannot lose the request


def test_chat_is_measured_against_real_chat_across_a_break(tmp_path):
    import random

    from clipper.watch import new_moments

    random.seed(7)
    for gap in (60, 140, 149, 300):
        lines = []
        for sec in range(7200, 9000):
            if 7800 <= sec < 7800 + gap:
                continue  # unread
            for k in range(random.choice((0, 1, 1, 2))):
                lines.append(json.dumps({"t": sec + k / 10, "author": f"u{random.randrange(400)}", "text": "hi"}))
        (tmp_path / "chat.json").write_text("\n".join(lines), encoding="utf-8")
        w = SimpleNamespace(work=tmp_path, watch_from=7200.0, meta={"started_at": time.time() - 9500}, reader=SimpleNamespace(gaps=[(7800.0, 7800.0 + gap)]))
        assert new_moments(w, Config()) == [], gap  # ordinary chat coming back is not a moment, whatever the length of the break
        spike = [json.dumps({"t": 7800 + gap + 60 + i * 0.1, "author": f"s{i}", "text": "NO WAY"}) for i in range(120)]
        (tmp_path / "chat.json").write_text("\n".join(lines + spike), encoding="utf-8")
        found = [m["peak_s"] for m in new_moments(w, Config())]
        assert len(found) == 1 and abs(found[0] - (7800 + gap + 60)) <= 10, (gap, found)  # a real one a minute after the break is found


def test_the_page_keeps_typing_and_reads_any_subtitle_file():
    from pathlib import Path

    import clipper

    page = (Path(clipper.__file__).parent / "review_page.html").read_text(encoding="utf-8")
    for piece in ("&&!(await save(key))){for(const b of ['bx-save'", "function subText(", "utf-16le", "windows-1252", "if(c.broken)return", "the boxes are saved"):
        assert piece in page, piece

