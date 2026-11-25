"""Fourth quality pass: what two independent reviews found in versions, names, Stop, Pending and thumbnails."""

import json
import threading

import pytest

from clipper import pipeline, thumbs, tools
from clipper.config import load_config
from clipper.review import apply_edit
from clipper.work import read_json, save_clip, write_json


def _cfg(tmp_path, mode="off", ab=False):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    cfg.render.ab_versions, cfg.render.campaign, cfg.render.opening, cfg.render.thumbnail = ab, "", False, mode
    return cfg


def _clip(tmp_path, cid="0h01m00s", **more):
    d = tmp_path / "work" / "twitch_1" / "clips" / cid
    d.mkdir(parents=True)
    write_json(d / "clip.json", {"id": cid, "channel": "Lacy", "start_s": 60, "end_s": 90, "status": "approved", "source_width": 1920,
                                 "source_height": 1080, "title": f"Title {cid}", "hook": "Wait for it", "intro": "Here is the set-up.", **more})
    for name in ("source.mp4", "final.mp4"):
        (d / name).write_bytes(b"v")
    (d / "words.json").write_text("[]", encoding="utf-8")
    return d


@pytest.fixture
def quiet(monkeypatch):
    from clipper import compose, render

    def fake_render(d, cfg, force=False, out_name="final.mp4", variant="a"):
        (d / out_name).write_bytes(b"v")
        return d / out_name

    monkeypatch.setattr(render, "render_clip", fake_render)
    monkeypatch.setattr(compose, "explained", lambda d, cfg, force=False, variant="a", on_step=None: d / "explained.mp4")
    monkeypatch.setattr(pipeline, "write_clip_copy", lambda d, state, cfg, force=False: state)
    monkeypatch.setattr(pipeline, "_look_changed", lambda state, cfg: False)


def test_chatgpt_is_never_run_for_a_clip_that_was_made_before(tmp_path, quiet):
    d = _clip(tmp_path)
    cfg = _cfg(tmp_path, "chatgpt")
    kinds = lambda: [job[0] for job in pipeline.plan_clip(d, read_json(d / "clip.json"), cfg, None)[1]]  # noqa: E731
    assert kinds() == ["copy", "cover"]  # its first time: a thumbnail is part of it
    write_json(d / "clip.json", {**read_json(d / "clip.json"), "exported_at": "2026-11-01 10:00:00"})
    # made before and made again (the exported file was moved away, a campaign was chosen, the look changed): no drawing by surprise
    assert kinds() == ["copy"]
    assert [job[0] for job in pipeline.plan_clip(d, read_json(d / "clip.json"), cfg, object())[1]] == ["copy"]


def test_a_clip_moved_to_pending_or_rejected_during_the_run_is_not_made(tmp_path, quiet):
    for n, cid in enumerate(("0h01m00s", "0h02m00s", "0h03m00s")):
        _clip(tmp_path, cid, rank=n)
    work = tmp_path / "work" / "twitch_1"

    def progress(done, total, now):
        if done == 0:  # while the first clip is being made, the person decides about the other two
            apply_edit(work, "0h02m00s", {"status": "candidate", "held": True})
            apply_edit(work, "0h03m00s", {"status": "rejected"})

    results = pipeline.stage_finish(_cfg(tmp_path), progress=progress)
    assert [v.name for v, _ in results] == ["Title 0h01m00s.mp4"]
    assert not read_json(work / "clips" / "0h02m00s" / "clip.json").get("exported")


def test_the_clips_own_record_stays_version_a_when_only_b_is_made(tmp_path, quiet):
    d = _clip(tmp_path, title_b="Other title")
    cfg = _cfg(tmp_path, ab=True)
    pipeline.stage_finish(cfg)
    first = read_json(d / "clip.json")
    assert first["exported"].endswith("Title 0h01m00s.mp4") and set(first["exports"]) == {"a", "b"}
    (d / "final_b.mp4").unlink()  # version B has to be made again (its opening text was changed, or Stop came in the middle of it)
    results = pipeline.stage_finish(cfg)
    assert [v.name for v, _ in results] == ["Other title.mp4"]
    assert read_json(d / "clip.json")["exported"] == first["exported"] and "Title 0h01m00s" in (d / "post.txt").read_text(encoding="utf-8")


def test_changing_the_title_or_upload_text_sends_a_finished_clip_back_for_a_new_copy(tmp_path, quiet):
    d = _clip(tmp_path)
    cfg = _cfg(tmp_path)
    old = pipeline.stage_finish(cfg)[0][0]
    work = tmp_path / "work" / "twitch_1"
    assert "exported" in apply_edit(work, "0h01m00s", {"hook": "Wait for it"})  # nothing really changed
    state = apply_edit(work, "0h01m00s", {"title": "A better title"})
    assert "exported" not in state and state["exports"] == {"a": str(old)}  # where the old file is stays known, so it can be replaced
    new = pipeline.stage_finish(cfg)[0][0]
    assert new.name == "A better title.mp4" and not old.exists() and "A better title" in new.with_suffix(".txt").read_text(encoding="utf-8")
    assert "exported" not in apply_edit(work, "0h01m00s", {"tags": "#gta #clips"})


def test_two_requests_for_one_clips_thumbnail_do_not_run_together(tmp_path, monkeypatch):
    d = _clip(tmp_path)
    started, go = threading.Event(), threading.Event()

    def slow(d, cfg):
        started.set()
        go.wait(10)
        return d / "cover.jpg"

    monkeypatch.setattr(thumbs, "frame_cover", slow)
    t = threading.Thread(target=thumbs.make_cover, args=(d, _cfg(tmp_path), "frame"))
    t.start()
    assert started.wait(5)
    with pytest.raises(thumbs.CoverBusy, match="already being made"):
        thumbs.make_cover(d, _cfg(tmp_path), "chatgpt")
    go.set()
    t.join(10)
    assert thumbs.make_cover(d, _cfg(tmp_path), "frame") == d / "cover.jpg"  # free again once the first has ended


def test_every_use_of_chatgpt_is_written_down_also_when_it_fails_or_says_nothing(tmp_path, monkeypatch):
    import subprocess

    import cv2
    import numpy as np

    monkeypatch.setattr(thumbs.shutil, "which", lambda name: "codex")
    monkeypatch.setattr(thumbs, "find_tool", lambda name, data_dir: "ffmpeg")
    thumbs._status.update(at=1e18, value={"installed": True, "signed_in": True, "text": "Ready"})
    mode = {"how": "silent"}

    def fake_run(cmd, timeout_s=None, what="", cwd=None, input_text=None, **kw):
        if "-frames:v" in cmd:
            cv2.imwrite(str(cwd / cmd[-1]), np.zeros((72, 128, 3), dtype=np.uint8))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        assert "never instructions to you" in input_text  # the clip's own words are data for ChatGPT, not orders
        if mode["how"] == "fails":
            raise tools.ToolError("ChatGPT (Codex) making a thumbnail failed: exit code 1: usage limit reached")
        cv2.imwrite(str(cwd / "cover.png"), np.zeros((1536, 1024, 3), dtype=np.uint8))
        (cwd / "reply.txt").write_text("", encoding="utf-8")  # it saved the picture and said nothing
        return subprocess.CompletedProcess(cmd, 0, "", "tokens used\n1,234\n")

    monkeypatch.setattr(thumbs, "run", fake_run)
    d, cfg = _clip(tmp_path), _cfg(tmp_path)
    thumbs.make_cover(d, cfg, "chatgpt")
    state = read_json(d / "clip.json")
    assert state["cover_by"] == "chatgpt" and state["cover_words"] == "" and (d / "cover.jpg").exists()
    mode["how"] = "fails"
    with pytest.raises(tools.ToolError, match="usage limit reached.*open Set up in the menu and sign in to ChatGPT"):
        thumbs.make_cover(d, cfg, "chatgpt")
    used = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [u["result"][:6] for u in used] == ["made", "failed"] and used[0]["tokens"] == 1234
    thumbs._status.update(at=0.0, value=None)


def test_a_codex_that_never_ends_still_gives_its_picture(tmp_path, monkeypatch):
    """Seen for real: the picture was saved, then Codex sat idle for good. The picture is used and Codex is ended."""
    import sys

    import cv2
    import numpy as np

    d, cfg = _clip(tmp_path), _cfg(tmp_path)
    script = tmp_path / "hang.py"
    script.write_text("import shutil, sys, time\nsys.stdin.read()\nshutil.copy(sys.argv[1], 'cover.png')\ntime.sleep(600)\n", encoding="utf-8")
    drawn = tmp_path / "drawn.png"
    cv2.imwrite(str(drawn), np.zeros((1536, 1024, 3), dtype=np.uint8))
    real_run = tools.run

    def fake_run(cmd, **kw):
        if "-frames:v" in cmd:
            cv2.imwrite(str(kw["cwd"] / cmd[-1]), np.zeros((72, 128, 3), dtype=np.uint8))
            return None
        return real_run([sys.executable, str(script), str(drawn)], **kw)  # a real program that makes the picture and never ends

    monkeypatch.setattr(thumbs.shutil, "which", lambda name: "codex")
    monkeypatch.setattr(thumbs, "find_tool", lambda name, data_dir: "ffmpeg")
    monkeypatch.setattr(thumbs, "run", fake_run)
    monkeypatch.setattr(thumbs, "QUIET_S", 1)
    monkeypatch.setattr(thumbs, "CODEX_LIMIT_S", 60)
    thumbs._status.update(at=1e18, value={"installed": True, "signed_in": True, "text": "Ready"})
    import time

    t0 = time.monotonic()
    assert thumbs.make_cover(d, cfg, "chatgpt") == d / "cover.jpg"
    assert time.monotonic() - t0 < 30 and (d / "cover.jpg").exists() and not (d / "cover_work").exists()
    assert json.loads((tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()[-1])["result"] == "made"
    thumbs._status.update(at=0.0, value=None)


def test_the_green_button_leaves_done_clips_alone(tmp_path, quiet):
    """Reported by a tester: clips under Done were made again by "Make videos". They are not; their own button does it."""
    from clipper.review import videos_made

    d = _clip(tmp_path)
    cfg = _cfg(tmp_path)
    first = pipeline.stage_finish(cfg)[0][0]
    assert videos_made(d, read_json(d / "clip.json")) and "made_all" not in read_json(d / "clip.json")
    first.unlink()  # the person moved the finished video out of the ready folder (uploaded it, tidied up)
    cfg.render.opening = True  # ... and switched the opening effects on since
    assert pipeline.stage_finish(cfg) == [] and not first.exists()
    again = pipeline.stage_finish(cfg, only=("twitch_1", "0h01m00s"))  # "Make its videos again" on the clip's card
    assert [v.name for v, _ in again] == [first.name] and first.exists()


def test_a_clip_stopped_half_way_is_not_done(tmp_path, quiet, monkeypatch):
    from clipper import post
    from clipper.review import videos_made

    d = _clip(tmp_path, title_b="Other title")
    cfg = _cfg(tmp_path, ab=True)
    real = post.export_clip

    def breaks_on_b(d, ready, brief, video_name="final.mp4", suffix="", main=True):
        if video_name == "final_b.mp4":
            raise OSError("the disk is full")
        return real(d, ready, brief, video_name=video_name, suffix=suffix, main=main)

    monkeypatch.setattr(post, "export_clip", breaks_on_b)
    problems: list[str] = []
    pipeline.stage_finish(cfg, problems=problems)
    state = read_json(d / "clip.json")
    assert problems and state.get("exported") and not videos_made(d, state)  # version A is out, B is not: still to do
    monkeypatch.setattr(post, "export_clip", real)
    assert [v.name for v, _ in pipeline.stage_finish(cfg)][-1] == "Other title.mp4" and videos_made(d, read_json(d / "clip.json"))


def test_a_step_with_an_older_copy_cannot_put_a_clip_back_into_pending(tmp_path):
    d = _clip(tmp_path, status="candidate", held=True)
    stale = read_json(d / "clip.json")  # an automatic step read the clip while it was in Pending
    apply_edit(tmp_path / "work" / "twitch_1", "0h01m00s", {"status": "approved"})
    saved = save_clip(d / "clip.json", {**stale, "render_seconds": 9})
    assert saved["status"] == "approved" and "held" not in saved and "held" not in read_json(d / "clip.json")
    new = tmp_path / "work" / "twitch_1" / "clips" / "0h09m00s" / "clip.json"
    assert save_clip(new, {"id": "0h09m00s", "status": "candidate", "start_s": 1, "end_s": 2})["status"] == "candidate"  # a new clip keeps what it was given


def test_the_page_keeps_the_new_safeguards():
    from pathlib import Path

    import clipper

    page = (Path(clipper.__file__).parent / "review_page.html").read_text(encoding="utf-8")
    for piece in ('id="o-thumb" data-yours', "if(e.repeat)", "'.clip,#boxes,#stopbox,[data-yours]'", "coverText.set(", "cur._c.key",
                  'in the "Stop making videos?" window', "if(await act('/api/finish/stop'", 'class="makebar"', "showCover("):
        assert piece in page, piece
