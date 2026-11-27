"""Choosing which versions are made, what the finished files are called, stopping, and the Pending list."""

import threading
import time

import pytest

from clipper import pipeline, tools
from clipper.config import load_config
from clipper.post import export_clip, export_stem, safe_name
from clipper.review import apply_edit, clip_view, videos_made
from clipper.work import read_json, write_json


def _clip(tmp_path, cid="0h01m00s", work="twitch_1", **more):
    d = tmp_path / "work" / work / "clips" / cid
    d.mkdir(parents=True)
    write_json(d / "clip.json", {"id": cid, "channel": "Lacy", "start_s": 60, "end_s": 90, "status": "approved", "duration_s": 30,
                                 "width": 1080, "height": 1920, **more})
    return d


# ---- names ----

def test_a_title_becomes_a_file_name_windows_accepts():
    assert safe_name('He said "WHAT?" / 10:30 | <live>') == "He said WHAT 10 30 live"
    assert safe_name("  dots and spaces...  ") == "dots and spaces"
    assert safe_name("x" * 300) == "x" * 90
    assert safe_name("CON") == "" and safe_name("???") == "" and safe_name(None) == ""
    assert safe_name("یہ کیا ہوا #shorts") == "یہ کیا ہوا #shorts"  # other alphabets and hashtags are fine in a file name


def test_each_version_is_named_by_its_own_title():
    state = {"channel": "Lacy", "title": "Cop chase gone wrong", "title_b": "He did NOT see that coming"}
    assert export_stem(state, "c1", "_A") == ("Cop chase gone wrong", True)
    assert export_stem(state, "c1", "_B") == ("He did NOT see that coming", True)
    assert export_stem(state, "c1", "_explained_A") == ("Cop chase gone wrong (explained)", True)
    assert export_stem(state, "c1", "_explained_B") == ("He did NOT see that coming (explained)", True)
    same = {"channel": "Lacy", "title": "One title", "title_b": "one title"}
    assert export_stem(same, "c1", "_B")[0] == "One title (B)" and export_stem({**same, "title_b": ""}, "c1", "_B")[0] == "One title (B)"


def test_without_a_real_title_the_old_name_is_kept():
    assert export_stem({"channel": "Lacy"}, "0h01m00s", "") == ("Lacy_0h01m00s", False)
    assert export_stem({"channel": "Lacy", "title": "  "}, "0h01m00s", "_B") == ("Lacy_0h01m00s_B", False)
    # the title written without AI is only a placeholder ("Lacy stream moment"): not a name to find a video by
    plain = {"channel": "Lacy", "title": "Lacy stream moment", "copy_source": "plain", "copy_auto": ["title", "hook"]}
    assert export_stem(plain, "0h01m00s", "_explained") == ("Lacy_0h01m00s_explained", False)
    # ... but a title the person typed over it is theirs
    assert export_stem({**plain, "title": "My own title", "copy_auto": ["hook"]}, "0h01m00s", "")[0] == "My own title"


def test_two_clips_with_one_title_both_stay_and_a_new_title_replaces_the_old_file(tmp_path):
    ready = tmp_path / "ready"
    a, b = _clip(tmp_path, "0h01m00s", title="Same title"), _clip(tmp_path, "0h02m00s", title="Same title")
    for d, data in ((a, b"first"), (b, b"second")):
        (d / "final.mp4").write_bytes(data)
    va, _ = export_clip(a, ready)
    vb, _ = export_clip(b, ready)
    assert va.name == "Same title.mp4" and vb.name == "Same title (2).mp4" and va.read_bytes() == b"first"
    again, _ = export_clip(a, ready)
    assert again == va and len(list(va.parent.glob("*.mp4"))) == 2  # made again: its own file, not a third
    apply_edit(tmp_path / "work" / "twitch_1", "0h01m00s", {"title": "A better title"})
    (a / "final.mp4").write_bytes(b"first")
    new, _ = export_clip(a, ready)
    assert new.name == "A better title.mp4" and not va.exists() and not va.with_suffix(".txt").exists() and vb.exists()
    assert read_json(a / "clip.json")["exports"] == {"a": str(new)} and new.with_suffix(".txt").exists()


# ---- which versions ----

def _finish(tmp_path, monkeypatch, ab=False, **kw):
    from clipper import compose, render

    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    cfg.render.ab_versions, cfg.render.campaign, cfg.render.opening = ab, "", False
    made = []

    def fake_render(d, cfg, force=False, out_name="final.mp4", variant="a"):
        (d / out_name).write_bytes(b"v")
        made.append(out_name)
        return d / out_name

    def fake_explained(d, cfg, force=False, variant="a", on_step=None):
        name = "explained_b.mp4" if variant == "b" else "explained.mp4"
        if on_step:
            on_step()
        (d / name).write_bytes(b"e")
        made.append(name)
        return d / name

    monkeypatch.setattr(render, "render_clip", fake_render)
    monkeypatch.setattr(compose, "explained", fake_explained)
    monkeypatch.setattr(pipeline, "write_clip_copy", lambda d, state, cfg, force=False: state)
    monkeypatch.setattr(pipeline, "_look_changed", lambda state, cfg: False)
    problems: list[str] = []
    results = pipeline.stage_finish(cfg, problems=problems, **kw)
    return sorted(v.name for v, _ in results), problems


def _ready_clip(tmp_path, cid="0h01m00s", **more):
    d = _clip(tmp_path, cid, source_width=1920, title=f"Title {cid}", intro="Here is the set-up.", **more)
    (d / "source.mp4").write_bytes(b"s")
    (d / "words.json").write_text("[]", encoding="utf-8")
    return d


def test_the_normal_video_can_be_left_out_and_only_the_explainer_made(tmp_path, monkeypatch):
    d = _ready_clip(tmp_path, simple=False, explainer=True)
    names, problems = _finish(tmp_path, monkeypatch)
    assert names == ["Title 0h01m00s (explained).mp4"] and not problems
    state = read_json(d / "clip.json")
    assert set(state["exports"]) == {"xa"} and videos_made(d, state)  # nothing is missing: it is Done
    assert _finish(tmp_path, monkeypatch) == ([], [])  # and it is not made a second time


def test_both_versions_by_default_and_all_four_with_a_and_b(tmp_path, monkeypatch):
    _ready_clip(tmp_path, explainer=True, title_b="Other title")
    names, _ = _finish(tmp_path, monkeypatch, ab=True)
    assert names == ["Other title (explained).mp4", "Other title.mp4", "Title 0h01m00s (explained).mp4", "Title 0h01m00s.mp4"]


def test_ticking_the_normal_video_later_makes_only_that_one(tmp_path, monkeypatch):
    d = _ready_clip(tmp_path, simple=False, explainer=True)
    _finish(tmp_path, monkeypatch)
    apply_edit(tmp_path / "work" / "twitch_1", "0h01m00s", {"simple": True})
    assert not videos_made(d, read_json(d / "clip.json"))  # back in Approved until the missing version is made
    assert _finish(tmp_path, monkeypatch)[0] == ["Title 0h01m00s.mp4"]


def test_a_clip_must_keep_at_least_one_version(tmp_path):
    _clip(tmp_path)
    with pytest.raises(ValueError, match="at least one version"):
        apply_edit(tmp_path / "work" / "twitch_1", "0h01m00s", {"simple": False})
    state = apply_edit(tmp_path / "work" / "twitch_1", "0h01m00s", {"simple": False, "explainer": True})
    assert state["simple"] is False and clip_view(tmp_path / "work" / "twitch_1" / "clips" / "0h01m00s")["simple"] is False


def test_a_clip_exported_by_an_older_version_is_not_made_again(tmp_path, monkeypatch):
    d = _ready_clip(tmp_path)
    (d / "final.mp4").write_bytes(b"v")
    old = tmp_path / "ready" / "2026-01-01" / "Lacy_0h01m00s.mp4"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"v")
    write_json(d / "clip.json", {**read_json(d / "clip.json"), "exported": str(old)})
    assert _finish(tmp_path, monkeypatch) == ([], []) and read_json(d / "clip.json")["exports"] == {"a": str(old)}
    assert pipeline.known_exports({"exported": str(old)}, ab=True) == {}  # A and B switched on since: everything is exported again


def test_one_clip_can_be_made_without_the_others(tmp_path, monkeypatch):
    _ready_clip(tmp_path, "0h01m00s")
    other = _ready_clip(tmp_path, "0h02m00s")
    names, _ = _finish(tmp_path, monkeypatch, only=("twitch_1", "0h02m00s"))
    assert names == ["Title 0h02m00s.mp4"] and videos_made(other, read_json(other / "clip.json"))
    assert not read_json(tmp_path / "work" / "twitch_1" / "clips" / "0h01m00s" / "clip.json").get("exported")


# ---- stopping ----

def test_stop_after_this_video_leaves_the_rest_approved(tmp_path, monkeypatch):
    for cid in ("0h01m00s", "0h02m00s", "0h03m00s"):
        _ready_clip(tmp_path, cid, rank=int(cid[3]))
    seen, left = [], []
    names, problems = _finish(tmp_path, monkeypatch, progress=lambda done, total, now: seen.append((done, total, now)),
                              stop=lambda: len(seen) >= 1, left=left)
    assert names == ["Title 0h01m00s.mp4"] and not problems
    assert left == ["Title 0h02m00s", "Title 0h03m00s"] and seen == [(0, 3, "Title 0h01m00s")]
    for cid in ("0h02m00s", "0h03m00s"):
        state = read_json(tmp_path / "work" / "twitch_1" / "clips" / cid / "clip.json")
        assert state["status"] == "approved" and "error" not in state and not state.get("exported")


def test_stop_now_ends_the_clip_in_the_middle_without_calling_it_a_failure(tmp_path, monkeypatch):
    from clipper import render

    for cid in ("0h01m00s", "0h02m00s"):
        _ready_clip(tmp_path, cid, rank=int(cid[3]))
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    cfg.render.ab_versions = False

    def stopped_render(d, cfg, force=False, out_name="final.mp4", variant="a"):
        raise tools.Stopped()

    monkeypatch.setattr(render, "render_clip", stopped_render)
    monkeypatch.setattr(pipeline, "write_clip_copy", lambda d, state, cfg, force=False: state)
    problems, left = [], []
    assert pipeline.stage_finish(cfg, problems=problems, left=left) == [] and not problems
    assert left == ["Title 0h01m00s", "Title 0h02m00s"]
    assert "error" not in read_json(tmp_path / "work" / "twitch_1" / "clips" / "0h01m00s" / "clip.json")


def test_a_running_tool_is_ended_when_stop_is_pressed():
    import sys

    event = threading.Event()
    out: list = []

    def work() -> None:
        tools.stop_on(event)
        t0 = time.monotonic()
        for watched in (None, lambda line: None):
            try:
                tools.run([sys.executable, "-c", "import time; time.sleep(60)"], timeout_s=120, retries=3, on_line=watched)
                out.append("ended by itself")
            except tools.Stopped:
                out.append(round(time.monotonic() - t0))

    t = threading.Thread(target=work)
    t.start()
    time.sleep(1.5)
    event.set()
    t.join(timeout=30)
    assert not t.is_alive() and len(out) == 2 and all(isinstance(s, int) and s < 20 for s in out)  # not retried, not waited out
    # a thread that did not ask for it is never stopped by someone else's Stop
    assert tools.run([sys.executable, "-c", "print('fine')"], timeout_s=60).stdout.strip() == "fine"


def test_the_stop_buttons_of_the_page_reach_the_job(tmp_path):
    from clipper.control import Control

    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    tmp_path.joinpath("work").mkdir()
    c = Control(cfg)
    with pytest.raises(ValueError, match="no videos are being made"):
        c.stop_finish({"how": "now"})
    c.finishing = True
    with pytest.raises(ValueError, match="say how to stop"):
        c.stop_finish({"how": "maybe"})
    assert c.stop_finish({"how": "after"})["finish_stopping"] == "after" and not c.finish_stop.is_set()
    assert c.stop_finish({"how": "now"})["finish_stopping"] == "now" and c.finish_stop.is_set()
    assert c.stop_finish({"how": "after"})["finish_stopping"] == "now"  # "now" is not softened afterwards
    c.finishing = False
    with pytest.raises(ValueError, match="approve this clip first"):
        c.start_finish({"only": "twitch_1/0h01m00s"})
    with pytest.raises(ValueError, match="not a clip"):
        c.start_finish({"only": "../../x"})


# ---- Pending ----

def test_pending_keeps_a_clip_apart_from_to_review_until_it_is_decided_again(tmp_path):
    d = _clip(tmp_path)
    work = tmp_path / "work" / "twitch_1"
    with pytest.raises(ValueError, match="back in review"):
        apply_edit(work, "0h01m00s", {"held": True})  # still approved
    state = apply_edit(work, "0h01m00s", {"status": "candidate", "held": True})
    assert state["status"] == "candidate" and state["held"] is True and clip_view(d)["held"] is True
    assert pipeline.select_clips(work, status="approved") == []  # "Make videos" leaves it alone
    assert apply_edit(work, "0h01m00s", {"hook": "typed while pending"})["held"] is True
    state = apply_edit(work, "0h01m00s", {"status": "approved"})
    assert "held" not in state and clip_view(d)["held"] is False
    apply_edit(work, "0h01m00s", {"status": "candidate", "held": True})
    assert "held" not in apply_edit(work, "0h01m00s", {"status": "candidate", "held": False}) or not read_json(d / "clip.json")["held"]


def test_the_page_has_the_new_controls():
    from pathlib import Path

    import clipper

    page = clipper.page_source()
    for piece in ('name="simple"', 'id="b-fstop"', 'id="stop-cancel"', "finishStop('after')", "finishStop('now')", "['pending','Pending']",
                  "Move to Pending", "/api/clips/hold-approved", "makeOne(", 'id="b-ready"', "!$('stopbox').hidden||"):
        assert piece in page, piece


# ---- how far it is ----

def test_the_work_is_counted_before_it_starts_so_the_bar_moves_by_real_steps(tmp_path, monkeypatch):
    from clipper import progress

    _ready_clip(tmp_path, "0h01m00s", rank=1, explainer=True)          # render A, render B, two explainers of two halves each
    done_one = _ready_clip(tmp_path, "0h02m00s", rank=2)
    _ready_clip(tmp_path, "0h03m00s", rank=3, simple=False, explainer=True)
    _finish(tmp_path, monkeypatch, ab=True, only=("twitch_1", "0h02m00s"))  # this one is made already: not counted again
    assert videos_made(done_one, read_json(done_one / "clip.json"))
    seen: list[tuple] = []
    monkeypatch.setattr(progress, "item", lambda index, count, text="": seen.append((index, count, text)))
    clips: list[tuple] = []
    _finish(tmp_path, monkeypatch, ab=True, progress=lambda done, total, now: clips.append((done, total)))
    assert clips == [(0, 2), (1, 2), (2, 2)]  # two clips have work; the finished one is not mentioned
    assert [(i, n) for i, n, _ in seen] == [(i, 12) for i in range(12)]  # every step is one of eleven, in order, never past the end
    texts = [t for _, _, t in seen]
    assert texts[0] == "clip 1 of 2: Title 0h01m00s · rendering the video" and texts[1].endswith("rendering version B")
    assert texts[2].endswith("the explainer: the narrator's card") and texts[3].endswith("the explainer: joining it to the clip")
    assert texts[6].startswith("clip 2 of 2: Title 0h03m00s") and texts[-1].endswith("explainer B: joining it to the clip")


def test_the_plan_of_a_clip_is_only_what_is_missing(tmp_path, monkeypatch):
    d = _ready_clip(tmp_path)
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    cfg.render.ab_versions, cfg.render.opening = False, False
    monkeypatch.setattr(pipeline, "_look_changed", lambda state, cfg: False)
    fresh, jobs = pipeline.plan_clip(d, read_json(d / "clip.json"), cfg, None)
    assert fresh and [(k, v) for k, v, *_ in jobs] == [("render", "a"), ("copy", "a")]
    (d / "final.mp4").write_bytes(b"v")
    fresh, jobs = pipeline.plan_clip(d, read_json(d / "clip.json"), cfg, None)
    assert not fresh and [(k, v) for k, v, *_ in jobs] == [("copy", "a")]  # the draft is good: it only goes to the ready folder
    _finish(tmp_path, monkeypatch)
    assert pipeline.plan_clip(d, read_json(d / "clip.json"), cfg, None) == (False, [])
    (d / "source.mp4").unlink()
    with pytest.raises(tools.ToolError, match="never downloaded"):
        pipeline.plan_clip(d, read_json(d / "clip.json"), cfg, None)
