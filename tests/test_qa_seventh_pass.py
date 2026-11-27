"""Seventh quality pass (2026-11-27): faults found by review and by running the new things of that day."""

import json
from pathlib import Path

import clipper
from clipper import plain, review
from clipper.config import load_config

PAGE = clipper.page_source()


def _cfg(tmp_path):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    return cfg


def _clip(tmp_path, **extra):
    d = tmp_path / "work" / "twitch_1" / "clips" / "0h10m00s"
    d.mkdir(parents=True)
    state = {"id": "0h10m00s", "start_s": 600.0, "end_s": 630.0, "status": "candidate", "fetch_start_s": 590.0, "fetch_end_s": 640.0,
             "source_duration_s": 50.0, "source_width": 1920, "source_height": 1080, "vod_url": "https://www.twitch.tv/videos/123456789",
             "platform": "twitch", "channel": "somebody", **extra}
    (d / "clip.json").write_text(json.dumps(state), encoding="utf-8")
    (d / "source.mp4").write_bytes(b"video")
    (d / "words.json").write_text("[]", encoding="utf-8")
    (d.parent.parent / "meta.json").write_text(json.dumps({"url": state["vod_url"], "duration_s": 7200, "is_live": False}), encoding="utf-8")
    return d


def test_a_long_request_on_a_clip_is_drawn_by_the_card_and_cannot_be_started_twice():
    """The "working" note used to sit on a button that the card's redraw replaced: after a second or two the button
    looked ready again, and a second press started a second download into the same file."""
    for piece in ("const WORK=new Map();", "function workStart(key,text){", "async function workEnd(key){",
                  "if(WORK.has(key)){toast('Wait a moment: '+WORK.get(key),true);return}",
                  "const wk=WORK.get(k), dis=wk?' disabled':'';", "(wk?esc(wk):'Picture:')", "(wk?esc(wk):'Need more of the stream?')",
                  'class="btns wrow"'):
        assert piece in PAGE, piece
    assert PAGE.count("'+dis+' onclick=\"widen(") == 3 and PAGE.count("'+dis+' onclick=\"quickFrame(") == 2
    # what was typed during the wait is saved, not wiped by the redraw
    end = PAGE[PAGE.index("async function workEnd(key){"):PAGE.index("/* The picture of one clip, in one click")]
    assert "el.dataset.dirty==='1'&&await save(key)" in end and "dataset.dirty=''" not in end
    # "the seconds were added, but ..." is not announced as "No more of the stream was added"
    assert "/^the seconds were added/i.test(r.error)?'':'No more of the stream was added: '" in PAGE


def test_two_requests_for_more_of_the_same_clip_do_not_run_together(tmp_path, monkeypatch):
    import threading

    import pytest

    from clipper import pipeline, transcribe

    cfg = _cfg(tmp_path)
    d = _clip(tmp_path)
    monkeypatch.setattr(pipeline, "free_gb", lambda p: 50.0)
    inside, go_on, second = threading.Event(), threading.Event(), {}

    def slow_fetch(source, a, b, out, cfg_):
        inside.set()
        go_on.wait(10)
        Path(out).write_bytes(b"longer video")
        return out

    monkeypatch.setattr(pipeline, "fetch_range", slow_fetch)
    monkeypatch.setattr(pipeline, "probe", lambda f, c: {"duration_s": 65.0, "width": 1920, "height": 1080, "fps": 30})
    monkeypatch.setattr(transcribe, "transcribe_file", lambda f, c: [])
    monkeypatch.setattr("clipper.fetch.ensure_sound", lambda f, c: True)
    first = threading.Thread(target=lambda: second.update(first=pipeline.widen_clip(d, cfg, before=15)))
    first.start()
    assert inside.wait(10)
    with pytest.raises(ValueError, match="already being added"):
        pipeline.widen_clip(d, cfg, after=15)
    go_on.set()
    first.join(20)
    assert second["first"]["fetch_start_s"] == 575.0
    assert pipeline._widening == set()                                       # and afterwards the clip is free again


def test_a_new_thumbnail_shows_after_typing_in_the_not_wanted_box():
    """The box marked the card as "being edited", so the card was not redrawn and still said "No thumbnail yet"."""
    assert 'name="t_cover_avoid"' in PAGE and 'name="cover_avoid"' not in PAGE          # t_ names do not hold a redraw back
    for piece in ("const coverAvoid=new Map();", "coverAvoid.has(k)?coverAvoid.get(k):(c.cover_avoid||'')", "cover_avoid:g('t_cover_avoid').value.trim(),",
                  "avoid:el.querySelector('[name=t_cover_avoid]').value"):
        assert piece in PAGE, piece


def test_version_b_ticked_on_a_finished_clip_is_still_to_be_made(tmp_path):
    d = _clip(tmp_path, status="approved", exported=str(tmp_path / "a.mp4"), exports={"a": str(tmp_path / "a.mp4")})
    (d / "final.mp4").write_bytes(b"v")
    state = json.loads((d / "clip.json").read_text(encoding="utf-8"))
    assert review.videos_made(d, state) is True                      # made as version A only, and nothing else was asked
    state["ab"] = True
    assert review.videos_made(d, state) is False                     # B was ticked afterwards: Make videos has work to do
    state["exports"]["b"] = str(tmp_path / "b.mp4")
    (d / "final_b.mp4").write_bytes(b"v")
    assert review.videos_made(d, state) is True
    state["ab"] = False
    state["exports"].pop("b")
    assert review.videos_made(d, state) is True                      # unticked: one video is all there is to make


def test_automatic_under_the_player_keeps_the_boxes_remembered_for_the_streamer(tmp_path, monkeypatch):
    from clipper import layout

    cfg = _cfg(tmp_path)
    d = _clip(tmp_path, layout="blur", game=[0, 0, 1, 1])
    forgot = []
    monkeypatch.setattr(layout, "save_framing", lambda channel, state, c: forgot.append((channel, state)))
    monkeypatch.setattr(layout, "auto_layout", lambda dd, state, c: {**state, "layout": "crop", "layout_reason": "no steady face found"})
    monkeypatch.setattr("clipper.render.render_clip", lambda dd, c, **k: None)
    review.set_framing(d, {"auto": True, "keep": True}, cfg)         # the one-click button: this clip only
    assert forgot == []
    review.set_framing(d, {"auto": True}, cfg)                       # "Back to automatic" in the box editor: as before
    assert forgot == [("somebody", None)]
    assert "{auto:true,keep:true}" in PAGE


def test_the_preview_says_its_error_in_words_and_follows_a_new_logo():
    assert "if(plainOf(r.error,true))$('lk-empty').innerHTML=plainHtml(r.error);else $('lk-empty').textContent=r.error;" in PAGE
    assert "show(await r.json());if(curPage==='look')lookSoon();toast(kind==='logo'" in PAGE
    assert "if(LK.v&&!LK.busy){$('lk-img').src=" in PAGE              # no picture is fetched while it is being made again


def test_names_the_wording_pass_missed():
    assert "Trim and framing" not in PAGE and ">Branding <small>" not in PAGE
    src = (Path(clipper.__file__).parent / "plain.py").read_text(encoding="utf-8")
    assert "Trim and framing" not in src and "Start, end and picture" in src
    got = plain.explain("the free AI is installed but not answering")
    assert got["known"] is True, got                                  # the reworded message is still explained in plain words
    # the help assistant opens the editor for the Picture row and the version buttons too
    assert PAGE.count("el.closest('.info,.edhead,.frow,.vtabs')") == 2


def test_the_picture_row_fits_under_the_player():
    """The player column stays in place while the rest scrolls, so everything under the video must fit in the window."""
    assert ".clip.open .vid video{border-radius:var(--r2);max-height:max(220px,calc(100vh - 244px));cursor:default}" in PAGE


def _model(cfg, folder):
    snap = Path(cfg.data_dir) / "models" / folder / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x")


def test_the_speech_model_is_found_by_its_real_folder_not_by_how_a_name_ends(tmp_path):
    from clipper import health

    cfg = _cfg(tmp_path)
    _model(cfg, "models--Systran--faster-distil-whisper-large-v3")
    assert health.speech_model_there(cfg, "distil-large-v3") is True       # was called missing: the folder does not end in that name
    assert health.speech_model_there(cfg, "large-v3") is False             # was called there, and 3 GB then came down unasked
    _model(cfg, "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo")
    assert health.speech_model_there(cfg, "large-v3-turbo") is True and health.speech_model_there(cfg, "turbo") is True
    assert health.speech_model_there(cfg, "large-v3") is False and health.speech_model_there(cfg, "small") is False
    _model(cfg, "models--Systran--faster-whisper-large-v3")
    assert health.speech_model_there(cfg, "large") is True and health.speech_model_there(cfg, "Systran/faster-whisper-large-v3") is True
    assert health.model_repos("distil-large-v3.5") == {"faster-distil-whisper-large-v3.5", "distil-large-v3.5-ct2"}


def test_reading_a_whole_stream_from_the_page_uses_a_model_that_is_there(tmp_path, monkeypatch):
    """On a processor the whole stream is read with the model "small". From the page that one is never downloaded
    (the Set up button fetches the usual model), so the run failed late with advice that could not help."""
    from clipper import health, hw, streamread

    cfg = _cfg(tmp_path)
    cfg.transcribe.backend, cfg.ai.speech_model = "local", "auto"
    monkeypatch.setattr(hw, "whisper_device", lambda c: ("cpu", "int8"))
    _model(cfg, "models--someone--faster-whisper-" + str(cfg.transcribe.model).split("/")[-1])
    monkeypatch.setenv(health.ASK_FIRST, "1")
    assert streamread.stream_model_name(cfg) == cfg.transcribe.model       # the usual one reads the stream, slower
    _model(cfg, "models--Systran--faster-whisper-small")
    assert streamread.stream_model_name(cfg) == "small"
    monkeypatch.delenv(health.ASK_FIRST)
    cfg2 = _cfg(tmp_path / "terminal")
    assert streamread.stream_model_name(cfg2) == "small"                    # from a terminal it is fetched, as before


def test_a_word_cut_in_half_at_the_old_edge_is_whole_after_adding_seconds():
    from clipper import pipeline

    old = [{"start": 0.0, "end": 0.2, "word": "lo"}, {"start": 0.6, "end": 1.0, "word": "world"}, {"start": 9.7, "end": 10.0, "word": "by"}]
    new = [{"start": 14.2, "end": 15.2, "word": "hello"}, {"start": 15.6, "end": 16.0, "word": "wrld"}, {"start": 24.7, "end": 25.4, "word": "bye"},
           {"start": 26.0, "end": 26.5, "word": "now"}]
    got = [w["word"] for w in pipeline.merge_words(old, new, shift=15.0, old_len=10.0)]
    assert got == ["hello", "world", "bye", "now"]            # whole words at both seams, the person's "world" kept, nothing twice


def test_adding_seconds_keeps_what_the_clip_text_is_written_from_and_the_finished_videos(tmp_path, monkeypatch):
    from clipper import pipeline, transcribe

    cfg = _cfg(tmp_path)
    d = _clip(tmp_path, transcript="the clip itself")
    (d / "words.json").write_text(json.dumps([{"start": 12.0, "end": 12.5, "word": "inside"}]), encoding="utf-8")
    monkeypatch.setattr(pipeline, "free_gb", lambda p: 50.0)
    monkeypatch.setattr(pipeline, "fetch_range", lambda source, a, b, out, c: Path(out).write_bytes(b"longer"))
    monkeypatch.setattr(pipeline, "probe", lambda f, c: {"duration_s": 110.0, "width": 1920, "height": 1080, "fps": 30})
    monkeypatch.setattr(transcribe, "transcribe_file", lambda f, c: [{"start": 2.0, "end": 2.4, "word": "long-before"}, {"start": 100.0, "end": 100.4, "word": "long-after"}])
    monkeypatch.setattr("clipper.fetch.ensure_sound", lambda f, c: True)
    state = pipeline.widen_clip(d, cfg, before=30, after=30)
    assert state["transcript"] == "inside"                                   # not "long-before inside long-after"
    assert state["fetch_end_s"] == 670.0 and isinstance(state["fetch_end_s"], float) and len(str(state["fetch_end_s"])) <= 7
    # the request from the page no longer throws the draft and finished videos away: the clip's start and end did not move
    src = (Path(clipper.__file__).parent / "review.py").read_text(encoding="utf-8")
    handler = src[src.index('/widen", self.path)'):src.index('/framing", self.path)')]
    assert "RENDERS" not in handler and "render_clip(d, use)" not in handler
    # a clip whose draft is still being made is not touched
    (d / "words.json").unlink()
    import pytest

    with pytest.raises(ValueError, match="still being made"):
        pipeline.widen_clip(d, cfg, before=15)


def test_the_preview_takes_a_clip_that_is_free_and_keeps_each_look_apart(tmp_path, monkeypatch):
    import os
    import time

    from clipper import render

    cfg = _cfg(tmp_path)
    d1 = _clip(tmp_path, title="Being made")
    d2 = d1.parent / "0h20m00s"
    d2.mkdir()
    (d2 / "clip.json").write_text(json.dumps({"id": "0h20m00s", "start_s": 1200.0, "end_s": 1230.0, "status": "candidate", "title": "Free"}), encoding="utf-8")
    (d2 / "source.mp4").write_bytes(b"v")
    (d2 / "words.json").write_text("[]", encoding="utf-8")
    os.utime(d2 / "clip.json", (time.time() - 100, time.time() - 100))       # d1 is the newest, so it is tried first
    asked = []

    def fake(clip_dir, c, out, wait=True):
        asked.append((clip_dir.name, wait))
        if clip_dir == d1:
            raise render.ClipBusy("its video is being made")
        out.mkdir(parents=True, exist_ok=True)
        for n in ("open", "mid", "end"):
            (out / f"{n}.jpg").write_bytes(b"jpg")
        return {}

    monkeypatch.setattr("clipper.render.preview_stills", fake)
    got = review.look_preview([d1.parent.parent], cfg)
    assert got["title"] == "Free" and asked == [("0h10m00s", False), ("0h20m00s", False)]   # never waits for a render
    folder = Path(cfg.data_dir) / "tmp" / "look-preview" / got["v"]
    assert (folder / "open.jpg").exists()                                    # each look has its own pictures
    src = (Path(clipper.__file__).parent / "review.py").read_text(encoding="utf-8")
    assert 'v = re.search(r"[?&]v=([0-9a-f]{16})(&|$)", self.path)' in src
    cfg.brand.outro_seconds = 10
    assert render.preview_seconds(cfg) == 16.0 and render.preview_seconds(_cfg(tmp_path / "x")) >= 14.0   # long enough for the closing text
