"""Faults found in the quality pass of 2026-11-20, each pinned by a test."""

import json
from pathlib import Path

import pytest

from clipper.config import load_config


def _cfg(tmp_path):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    return cfg


def _clip(tmp_path, **state):
    d = tmp_path / "clips" / "0h00m10s"
    d.mkdir(parents=True)
    (d / "clip.json").write_text(json.dumps({"id": "0h00m10s", "start_s": 10, "end_s": 40, **state}), encoding="utf-8")
    return d


def test_an_own_clip_asked_for_again_comes_back_for_review(tmp_path):
    from clipper.pipeline import _own_drafts
    from clipper.sources import Source

    src = Source("youtube", "x", "https://example.invalid/v")
    (cid,) = _own_drafts(src, tmp_path, {}, [(0.0, 30.0)], True)
    path = tmp_path / "clips" / cid / "clip.json"

    def set_status(value):
        s = json.loads(path.read_text(encoding="utf-8"))
        s["status"] = value
        path.write_text(json.dumps(s), encoding="utf-8")

    set_status("deleted")  # deleted on the page, then made again: it must not be skipped without a word
    _own_drafts(src, tmp_path, {}, [(0.0, 30.0)], True)
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "candidate"
    set_status("approved")  # the same range again keeps its approval
    _own_drafts(src, tmp_path, {}, [(0.0, 30.0)], True)
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "approved"
    _own_drafts(src, tmp_path, {}, [(0.0, 45.0)], True)  # another range in the same folder was never reviewed
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "candidate"


def test_a_damaged_file_gives_the_default_instead_of_breaking_the_page(tmp_path):
    from clipper.review import list_clips
    from clipper.work import read_json

    for name, text in (("empty.json", ""), ("null.json", "null"), ("half.json", '{"a": 1'), ("list.json", "[1]")):
        (tmp_path / name).write_text(text, encoding="utf-8")
        assert read_json(tmp_path / name, {}) == {}
    with pytest.raises(ValueError):
        read_json(tmp_path / "half.json")  # asked for without a default: the fault is still raised
    d = _clip(tmp_path)
    bad = tmp_path / "clips" / "0h00m50s"
    bad.mkdir()
    (bad / "clip.json").write_text("", encoding="utf-8")
    assert d in list_clips([tmp_path])  # one unreadable clip does not blank the list


def test_a_slow_step_writes_only_what_it_made(tmp_path):
    from clipper.work import read_json, update_clip, write_json

    d = _clip(tmp_path, hook="old", exported="x.mp4")
    stale = read_json(d / "clip.json")  # what a render read when it started
    write_json(d / "clip.json", {**stale, "hook": "typed while it rendered"})
    state = update_clip(d / "clip.json", {"render_seconds": 9}, drop=("exported",))
    assert state["hook"] == "typed while it rendered" and state["render_seconds"] == 9 and "exported" not in state


def test_a_file_in_use_is_named_and_an_edit_is_not_half_saved(tmp_path, monkeypatch):
    from clipper import work
    from clipper.review import apply_edit

    d = _clip(tmp_path, hook="old")
    (d / "final.mp4").write_bytes(b"x")
    real = Path.unlink

    def locked(self, *a, **k):
        if self.name == "final.mp4":
            raise PermissionError(32, "in use")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", locked)
    with pytest.raises(work.FileInUse, match="in use right now"):
        work.remove(d / "final.mp4", wait_s=0.2)
    monkeypatch.setattr(work, "remove", lambda p, wait_s=0.2: (_ for _ in ()).throw(work.FileInUse("busy")) if p.name == "final.mp4" else real(p, missing_ok=True))
    import clipper.review as review

    monkeypatch.setattr(review, "remove", work.remove)
    with pytest.raises(ValueError):
        apply_edit(tmp_path, "0h00m10s", {"hook": "new"})
    assert json.loads((d / "clip.json").read_text(encoding="utf-8"))["hook"] == "old"  # refused whole, with the reason


def test_a_piece_of_exactly_eight_seconds_is_long_enough():
    from clipper.pipeline import check_segments

    assert 64.1 - 56.1 < 8  # the arithmetic that used to refuse it
    assert check_segments([[56.1, 64.1]], 100) == [(56.1, 64.1)]
    with pytest.raises(ValueError, match="at least 8"):
        check_segments([[56.1, 63.9]], 100)


def test_no_internet_does_not_hide_a_known_newer_version(tmp_path, monkeypatch):
    from clipper import health

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(health, "update_state", lambda fetch=False: {"can": True, "behind": 3, "why": ""})
    assert health.check_update(cfg)["can"]
    monkeypatch.setattr(health, "update_state", lambda fetch=False: {"can": False, "behind": 0, "unreachable": True, "why": "GitHub could not be reached"})
    assert health.check_update(cfg)["behind"] == 3
    assert json.loads((tmp_path / "health.json").read_text(encoding="utf-8"))["update"]["can"] is True


def test_update_waits_for_running_work(tmp_path, monkeypatch):
    from clipper.control import Control

    c = Control(_cfg(tmp_path))
    monkeypatch.setattr(c, "running", lambda: True)
    with pytest.raises(ValueError, match="still running"):
        c.start_update()


def test_saved_boxes_without_a_picture_size_do_not_break_new_clips(tmp_path):
    from clipper.layout import auto_layout, save_framing

    cfg = _cfg(tmp_path)
    save_framing("Lacy", {"layout": "crop", "game": [0.1, 0.0, 0.3, 1.0], "source": [None, None]}, cfg)
    state = auto_layout(tmp_path, {"channel": "lacy", "source_width": 1920, "source_height": 1080}, cfg)
    assert state["layout"] == "crop" and state["game"] == [0.1, 0.0, 0.3, 1.0]


def test_a_stopped_tool_takes_its_helpers_with_it(tmp_path):
    import sys
    import time

    from clipper.tools import ToolError, run

    mark = tmp_path / "child-alive.txt"
    child = f"import time,pathlib\nfor i in range(40):\n    pathlib.Path(r'{mark}').write_text(str(i)); time.sleep(0.25)\n"
    parent = f"import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c',{child!r}])\ntime.sleep(30)\n"
    with pytest.raises(ToolError, match="timed out"):
        run([sys.executable, "-c", parent], timeout_s=2)
    time.sleep(0.6)
    seen = mark.read_text(encoding="utf-8") if mark.exists() else ""
    time.sleep(1.0)
    assert (mark.read_text(encoding="utf-8") if mark.exists() else "") == seen  # the helper stopped writing: it is gone too
