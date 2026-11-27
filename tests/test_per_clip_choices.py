"""Ahmad, 2026-11-27: A and B versions and the thumbnail's not-wanted list are chosen per clip; a clip can get more
of the stream around it; the whole picture is one click; Look and branding shows a real preview."""

import json
from pathlib import Path

import pytest

import clipper
from clipper import pipeline, review, thumbs
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
    (d / "source.mp4").write_bytes(b"old video")
    (d / "words.json").write_text("[]", encoding="utf-8")
    (d.parent.parent / "meta.json").write_text(json.dumps({"url": state["vod_url"], "duration_s": 7200, "is_live": False}), encoding="utf-8")
    return d


# ---------------- A and B versions ----------------

def test_each_clip_decides_whether_it_gets_a_version_b(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.render.ab_versions = True
    assert pipeline.wants_ab({}, cfg) is True and pipeline.wants_ab({"ab": None}, cfg) is True   # never chose: the general default
    assert pipeline.wants_ab({"ab": False}, cfg) is False
    cfg.render.ab_versions = False
    assert pipeline.wants_ab({}, cfg) is False and pipeline.wants_ab({"ab": True}, cfg) is True
    # the makers of the videos ask the clip, not the general setting
    src = (Path(clipper.__file__).parent / "pipeline.py").read_text(encoding="utf-8")
    assert src.count("cfg.render.ab_versions") == 1 and src.count("wants_ab(") >= 5


def test_the_choice_is_saved_with_the_clip_and_shown_on_its_card(tmp_path):
    cfg = _cfg(tmp_path)
    d = _clip(tmp_path)
    assert review.clip_view(d, cfg=cfg)["ab"] is True
    state = review.apply_edit(d.parent.parent, d.name, {"ab": False, "cover_avoid": "  no   blood,\n no red arrows "})
    assert state["ab"] is False and state["cover_avoid"] == "no blood, no red arrows"
    seen = review.clip_view(d, cfg=cfg)
    assert seen["ab"] is False and seen["cover_avoid"] == "no blood, no red arrows"
    for piece in ('name="ab"', "Make a version B too", "ab:g('ab').checked", "ab:c.ab!==false", "A and B versions are chosen on each clip now"):
        assert piece in PAGE, piece
    assert 'id="o-ab"' not in PAGE and "ab_versions:$(" not in PAGE     # no longer one switch for every clip


# ---------------- the thumbnail's not-wanted list ----------------

def test_a_clip_adds_its_own_not_wanted_things_to_the_general_list(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.render.thumbnail_avoid = "no guns"
    assert thumbs.avoid_text(cfg).endswith("no guns")
    both = thumbs.avoid_text(cfg, {"cover_avoid": "no red arrows"})
    assert both.endswith("no guns; no red arrows")
    cfg.render.thumbnail_avoid = ""
    assert thumbs.avoid_text(cfg, {"cover_avoid": "no red arrows"}).endswith(": no red arrows") and thumbs.avoid_text(cfg, {}) == ""
    state = {"channel": "x", "title": "t", "hook": "h", "transcript": "words", "cover_avoid": "do not show the other player's name"}
    assert "do not show the other player's name" in thumbs.auto_prompt(state, cfg)
    assert "do not show the other player's name" in thumbs.own_prompt("a red car", cfg, state)
    assert "other player" not in thumbs.own_prompt("a red car", cfg)
    for piece in ('name="t_cover_avoid"', "Not wanted in this clip", "avoid:el.querySelector('[name=t_cover_avoid]').value", "cover_avoid:g('t_cover_avoid').value.trim()"):
        assert piece in PAGE, piece
    assert 'id="o-avoid"' in PAGE                                        # the general list stays where it was


# ---------------- more of the stream around a clip ----------------

def test_captions_already_there_are_kept_when_the_video_gets_longer():
    old = [{"start": 1.0, "end": 1.4, "word": "FIXED"}, {"start": 9.0, "end": 9.5, "word": "end"}]
    new = [{"start": 2.0, "end": 2.5, "word": "before"}, {"start": 16.0, "end": 16.4, "word": "heard-wrong"},
           {"start": 24.6, "end": 25.0, "word": "end"}, {"start": 27.0, "end": 27.5, "word": "after"}]
    got = pipeline.merge_words(old, new, shift=15.0, old_len=10.0)
    assert [w["word"] for w in got] == ["before", "FIXED", "end", "after"]          # the person's correction survives, new words only outside
    assert got[1]["start"] == 16.0 and got[2]["end"] == 24.5
    assert pipeline.merge_words([], new, 15.0, 10.0) == new


def test_more_seconds_are_fetched_and_only_then_put_in_place(tmp_path, monkeypatch):
    from clipper import transcribe

    cfg = _cfg(tmp_path)
    d = _clip(tmp_path)
    (d / "words.json").write_text(json.dumps([{"start": 12.0, "end": 12.5, "word": "kept"}]), encoding="utf-8")
    (d / "wave.json").write_text("{}", encoding="utf-8")
    asked = []

    def fake_fetch(source, a, b, out, cfg_):
        asked.append((source.platform, a, b))
        Path(out).write_bytes(b"new, longer video")
        return out

    monkeypatch.setattr(pipeline, "fetch_range", fake_fetch)
    monkeypatch.setattr(pipeline, "probe", lambda f, c: {"duration_s": 80.0, "width": 1920, "height": 1080, "fps": 30})
    monkeypatch.setattr(pipeline, "free_gb", lambda p: 50.0)
    monkeypatch.setattr(transcribe, "transcribe_file", lambda f, c: [{"start": 3.0, "end": 3.4, "word": "earlier"}, {"start": 70.0, "end": 70.4, "word": "later"}])
    monkeypatch.setattr("clipper.fetch.ensure_sound", lambda f, c: True)

    state = pipeline.widen_clip(d, cfg, before=15, after=15)
    assert asked == [("twitch", 575.0, 655.0)]
    assert state["fetch_start_s"] == 575.0 and state["fetch_end_s"] == 655.0 and state["source_duration_s"] == 80.0
    assert state["start_s"] == 600.0 and "cut_start_s" not in state                  # the clip itself did not move
    assert (d / "source.mp4").read_bytes() == b"new, longer video" and not (d / "source.wide.mp4").exists() and not (d / "wave.json").exists()
    assert [w["word"] for w in json.loads((d / "words.json").read_text(encoding="utf-8"))] == ["earlier", "kept", "later"]
    assert json.loads((d / "words.json").read_text(encoding="utf-8"))[1]["start"] == 27.0   # 12 s into the old video is 27 s into the new


def test_a_failed_fetch_leaves_the_clip_as_it_was(tmp_path, monkeypatch):
    from clipper.tools import ToolError

    cfg = _cfg(tmp_path)
    d = _clip(tmp_path)
    monkeypatch.setattr(pipeline, "free_gb", lambda p: 50.0)

    def broken(source, a, b, out, cfg_):
        Path(out).write_bytes(b"half")
        raise ToolError("the internet dropped")

    monkeypatch.setattr(pipeline, "fetch_range", broken)
    with pytest.raises(ToolError, match="the internet dropped"):
        pipeline.widen_clip(d, cfg, before=15)
    assert (d / "source.mp4").read_bytes() == b"old video" and not (d / "source.wide.mp4").exists()
    assert json.loads((d / "clip.json").read_text(encoding="utf-8"))["fetch_start_s"] == 590.0


def test_adding_seconds_says_plainly_when_it_cannot(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    d = _clip(tmp_path)
    monkeypatch.setattr(pipeline, "free_gb", lambda p: 50.0)
    with pytest.raises(ValueError, match="how many seconds to add"):
        pipeline.widen_clip(d, cfg)
    with pytest.raises(ValueError, match="at most 120 seconds"):
        pipeline.widen_clip(d, cfg, before=500)
    meta = d.parent.parent / "meta.json"
    meta.write_text(json.dumps({"no_vod": True, "is_live": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="not being recorded"):
        pipeline.widen_clip(d, cfg, after=15)
    meta.write_text(json.dumps({"duration_s": 640.0, "is_live": False}), encoding="utf-8")
    with pytest.raises(ValueError, match="nothing more to add"):          # the clip's video already reaches the end of the video
        pipeline.widen_clip(d, cfg, after=15)
    state = json.loads((d / "clip.json").read_text(encoding="utf-8"))
    state.update(fetch_start_s=0.0, source_duration_s=590.0, fetch_end_s=590.0)
    (d / "clip.json").write_text(json.dumps(state), encoding="utf-8")
    meta.write_text(json.dumps({"duration_s": 7200.0}), encoding="utf-8")
    with pytest.raises(ValueError, match="at most 10 minutes"):
        pipeline.widen_clip(d, cfg, after=60)
    for piece in ("async function widen(key,before,after,btn){", "/widen',{before:before,after:after}", "Add 15 s before", "Add 30 s on both sides",
                  "v.removeAttribute('src');v.load()}"):
        assert piece in PAGE, piece
    assert '/widen", self.path)' in (Path(clipper.__file__).parent / "review.py").read_text(encoding="utf-8")


# ---------------- the whole picture, in one click ----------------

def test_the_whole_picture_is_one_click_under_the_player():
    for piece in ("async function quickFrame(key,mode,btn){", "{layout:'blur',game:[0,0,1,1]}:{auto:true,keep:true}", ">Whole picture</button>", ">Automatic</button>",
                  ">Choose boxes…</button>", ".clip.open .frow{display:flex"):
        assert piece in PAGE, piece
    # the two boxes keep the shape the layout shows them in: resizing changes the width and the height follows
    assert "b[2]=w;b[3]=bxH(w,asp)" in PAGE


def test_setting_the_whole_picture_is_accepted_by_the_program(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    d = _clip(tmp_path)
    made = []
    monkeypatch.setattr("clipper.render.render_clip", lambda dd, c, **k: made.append(dd.name))
    out = review.set_framing(d, {"layout": "blur", "game": [0, 0, 1, 1]}, cfg)
    state = json.loads((d / "clip.json").read_text(encoding="utf-8"))
    assert state["layout"] == "blur" and state["layout_auto"] is False and made == [d.name] and out["others"] == []


# ---------------- the preview on Look and branding ----------------

def test_the_look_page_has_a_preview_that_follows_every_change(tmp_path):
    for piece in ('id="lk-phone"', 'id="lk-img"', 'id="lk-tabs"', 'id="lk-note"', 'id="lk-other"', "async function lookDraw(){", "fetch('/api/look/preview?n='+LK.n)",
                  "if(curPage==='look')lookSoon();return true}", "if(p==='look')lookSoon();", "function lookShow(w){", ".lookprev{position:sticky"):
        assert piece in PAGE, piece
    src = (Path(clipper.__file__).parent / "render.py").read_text(encoding="utf-8")
    still = src[src.index("    if still:\n        # The picture is complete here"):src.index("    from .fetch import ensure_sound\n\n    ensure_sound(clip_dir")]
    assert "select=" in still and "os.replace(f, target)" in still and "update_clip" not in still   # frames only; the clip's record is not touched
    with pytest.raises(ValueError, match="There is no clip to show yet"):
        review.look_preview([tmp_path / "work" / "nothing"], _cfg(tmp_path))


def test_the_preview_is_made_once_per_look(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    d = _clip(tmp_path, title="A title")
    (d / "words.json").write_text("[]", encoding="utf-8")
    calls = []

    def fake(clip_dir, c, out, wait=True):
        calls.append(clip_dir.name)
        out.mkdir(parents=True, exist_ok=True)
        for n in ("open", "mid", "end"):
            (out / f"{n}.jpg").write_bytes(b"jpg")
        return {}

    monkeypatch.setattr("clipper.render.preview_stills", fake)
    first = review.look_preview([d.parent.parent], cfg)
    again = review.look_preview([d.parent.parent], cfg)
    assert calls == [d.name] and first["v"] == again["v"] and first["title"] == "A title" and first["of"] == 1
    cfg.brand.outro_text = "Follow for part 2"                         # something that can be seen changed
    assert review.look_preview([d.parent.parent], cfg)["v"] != first["v"] and len(calls) == 2
