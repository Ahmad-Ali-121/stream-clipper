"""Thumbnails: the free one from the clip, the one ChatGPT draws, and how they join "Make videos"."""

import json
import sys

import pytest

from clipper import pipeline, thumbs, tools
from clipper.config import load_config
from clipper.work import read_json, write_json


def _cfg(tmp_path, mode="off"):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    cfg.render.ab_versions, cfg.render.campaign, cfg.render.opening, cfg.render.thumbnail = False, "", False, mode
    return cfg


def _clip(tmp_path, cid="0h01m00s", **more):
    d = tmp_path / "work" / "twitch_1" / "clips" / cid
    d.mkdir(parents=True)
    write_json(d / "clip.json", {"id": cid, "channel": "Lacy", "start_s": 60, "end_s": 90, "status": "approved", "source_width": 1920,
                                 "source_height": 1080, "title": "Cop chase gone wrong", "hook": "He did not see that coming",
                                 "transcript": "oh my god he is right behind me", **more})
    (d / "source.mp4").write_bytes(b"s")
    (d / "words.json").write_text("[]", encoding="utf-8")
    return d


def _picture(path, w, h):
    import cv2
    import numpy as np

    cv2.imwrite(str(path), np.full((h, w, 3), 127, dtype=np.uint8))


def _size(path):
    import cv2

    return cv2.imread(str(path)).shape[:2][::-1]


def test_the_words_on_the_free_thumbnail_are_big_safe_and_end_in_the_accent_colour():
    ass = thumbs.cover_ass("He did {not} see\\that\ncoming", "Arial Black")
    line = ass.strip().splitlines()[-1]
    assert line.endswith("HE DID NOT SEE THAT {\\c&H0000D4FF&}COMING") and "PlayResY: 1920" in ass and "Arial Black,136," in ass
    assert "Arial Black,168," in thumbs.cover_ass("No way", "Arial Black") and ",560,1" in thumbs.cover_ass("No way", "Arial Black", top=560)
    assert thumbs.cover_ass("WOW", "Impact").strip().endswith("{\\c&H0000D4FF&}WOW")


def test_any_picture_becomes_a_thumbnail_of_the_right_size(tmp_path):
    for n, (w, h) in enumerate(((1024, 1536), (1920, 1080), (1080, 1920), (300, 900))):
        _picture(tmp_path / f"in{n}.png", w, h)
        thumbs.fit_cover(tmp_path / f"in{n}.png", tmp_path / f"out{n}.jpg")
        assert _size(tmp_path / f"out{n}.jpg") == (1080, 1920)
    (tmp_path / "bad.png").write_bytes(b"not a picture")
    with pytest.raises(tools.ToolError, match="could not be opened"):
        thumbs.fit_cover(tmp_path / "bad.png", tmp_path / "x.jpg")


def test_chatgpt_is_told_the_clips_facts_and_the_fixed_rules_without_anyone_writing_a_prompt():
    p = thumbs.auto_prompt({"channel": "Lacy", "title": "Cop chase gone wrong", "hook": "Wait for it", "transcript": "word " * 500, "ai_pick": "chat exploded"})
    assert p.startswith("$imagegen") and "Cop chase gone wrong" in p and "chat exploded" in p and "cover.png" in p and "1080x1920" in p
    assert "You decide what the picture shows" in p and len(p) < 3500  # the speech is cut, not sent whole
    mine = thumbs.own_prompt("  a red car flying over a lake  ")
    assert "a red car flying over a lake" in mine and "cover.png" in mine and "frames from the real video" in mine


def test_a_request_must_say_how_and_your_own_one_needs_words(tmp_path):
    d = _clip(tmp_path)
    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError, match="describe the picture"):
        thumbs.make_cover(d, cfg, "prompt", "   ")
    with pytest.raises(ValueError, match="very long"):
        thumbs.make_cover(d, cfg, "prompt", "x" * 2001)
    with pytest.raises(ValueError, match="from the clip, by ChatGPT"):
        thumbs.make_cover(d, cfg, "magic")


def test_without_codex_the_reason_and_the_way_to_set_it_up_are_given(tmp_path, monkeypatch):
    monkeypatch.setattr(thumbs.shutil, "which", lambda name: None)
    thumbs._status.update(at=0.0, value=None)
    assert thumbs.codex_status(fresh=True)["installed"] is False
    with pytest.raises(tools.ToolError, match="not installed.*codex login"):
        thumbs.chatgpt_cover(_clip(tmp_path), _cfg(tmp_path))
    thumbs._status.update(at=0.0, value=None)


def _fake_codex(monkeypatch, tmp_path, picture=True, seen=None):
    """Codex stands in as a function: it reads the request, and (when asked to) saves cover.png where it was told to work."""
    monkeypatch.setattr(thumbs.shutil, "which", lambda name: "codex")
    thumbs._status.update(at=1e18, value={"installed": True, "signed_in": True, "text": "Ready"})

    def fake_run(cmd, timeout_s=None, what="", cwd=None, input_text=None, **kw):
        import subprocess

        if "ffmpeg" in str(cmd[0]).lower() or "-frames:v" in cmd:
            _picture(cwd / cmd[-1], 1280, 720)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if seen is not None:
            seen.append((cmd, input_text, cwd))
        if picture:
            _picture(cwd / "cover.png", 1024, 1536)
        (cwd / "reply.txt").write_text("thinking...\nNO WAY\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "cover.png", "tokens used\n23,829\n")

    monkeypatch.setattr(thumbs, "run", fake_run)
    monkeypatch.setattr(thumbs, "find_tool", lambda name, data_dir: "ffmpeg")


def test_chatgpt_draws_it_and_the_use_is_written_down(tmp_path, monkeypatch):
    seen: list = []
    _fake_codex(monkeypatch, tmp_path, seen=seen)
    d, cfg = _clip(tmp_path), _cfg(tmp_path)
    out = thumbs.make_cover(d, cfg, "chatgpt")
    assert out == d / "cover.jpg" and _size(out) == (1080, 1920) and not (d / "cover_work").exists()
    cmd, text, cwd = seen[0]
    assert cmd[:2] == ["codex", "exec"] and cmd[-1] == "-" and cmd.count("-i") == 3 and "workspace-write" in cmd  # the request goes in on the input
    assert cwd == d / "cover_work" and text.startswith("$imagegen") and "Cop chase gone wrong" in text
    state = read_json(d / "clip.json")
    assert state["cover_by"] == "chatgpt" and state["cover_words"] == "NO WAY" and thumbs.cover_info(d, state)["made"]
    used = json.loads((tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert used["what"] == "thumbnail" and used["tokens"] == 23829 and "ChatGPT plan" in used["service"]
    thumbs.make_cover(d, cfg, "prompt", "a red car over a lake")
    assert "a red car over a lake" in seen[1][1] and read_json(d / "clip.json")["cover_by"] == "prompt"
    assert read_json(d / "clip.json")["cover_prompt"] == "a red car over a lake"


def test_an_answer_without_a_picture_is_an_error_with_what_chatgpt_said(tmp_path, monkeypatch):
    _fake_codex(monkeypatch, tmp_path, picture=False)
    d = _clip(tmp_path)
    with pytest.raises(tools.ToolError, match="saved no picture.*NO WAY"):
        thumbs.make_cover(d, _cfg(tmp_path), "chatgpt")
    assert not (d / "cover.jpg").exists() and not (d / "cover_work").exists()


def test_thumbnails_are_planned_only_when_switched_on_and_never_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_look_changed", lambda state, cfg: False)
    d = _clip(tmp_path)
    (d / "final.mp4").write_bytes(b"v")
    kinds = lambda mode: [job[0] for job in pipeline.plan_clip(d, read_json(d / "clip.json"), _cfg(tmp_path, mode), None)[1]]  # noqa: E731
    assert kinds("off") == ["copy"] and kinds("frame") == ["copy", "cover"] and kinds("chatgpt") == ["copy", "cover"]
    write_json(d / "clip.json", {**read_json(d / "clip.json"), "cover_error": "limit reached", "cover_error_mode": "chatgpt"})
    assert kinds("chatgpt") == ["copy"] and kinds("frame") == ["copy", "cover"]  # not tried again by itself the same way
    _picture(d / "cover.jpg", 1080, 1920)
    assert kinds("frame") == ["copy"]  # one made by hand is kept


def test_a_thumbnail_that_fails_never_costs_the_clip_its_video(tmp_path, monkeypatch):
    from clipper import render

    d = _clip(tmp_path)
    (d / "final.mp4").write_bytes(b"v")
    cfg = _cfg(tmp_path, "chatgpt")
    monkeypatch.setattr(pipeline, "_look_changed", lambda state, cfg: False)
    monkeypatch.setattr(pipeline, "write_clip_copy", lambda d, state, cfg, force=False: state)
    monkeypatch.setattr(render, "render_clip", lambda *a, **k: None)
    monkeypatch.setattr(thumbs, "make_cover", lambda d, cfg, how, prompt="": (_ for _ in ()).throw(tools.ToolError("your plan's limit is reached")))
    problems: list[str] = []
    results = pipeline.stage_finish(cfg, problems=problems)
    assert [v.name for v, _ in results] == ["Cop chase gone wrong.mp4"] and problems == ["the thumbnail of Cop chase gone wrong: your plan's limit is reached"]
    state = read_json(d / "clip.json")
    assert state["cover_error"] == "your plan's limit is reached" and "error" not in state and state["exported"]
    assert pipeline.stage_finish(cfg, problems=problems) == []  # and the next run does not hammer ChatGPT again

    # Clips finished earlier are left alone when thumbnails are on: no surprise drawing for every old clip.
    write_json(d / "clip.json", {k: v for k, v in read_json(d / "clip.json").items() if not k.startswith("cover_")})
    assert pipeline.plan_clip(d, read_json(d / "clip.json"), cfg, None)[1] == []


def test_a_tool_can_be_given_its_request_on_the_input():
    text = "héllo \"quoted\" & 100% | <odd>"
    out = tools.run([sys.executable, "-c", "import sys; print(ascii(sys.stdin.buffer.read().decode('utf-8').upper()))"], timeout_s=60, input_text=text)
    assert out.stdout.strip() == ascii(text.upper())


def test_the_option_and_the_page_controls_are_there():
    from pathlib import Path

    import clipper
    from clipper.control import clean_options

    assert clean_options({"thumbnail": "chatgpt"}) == {"render": {"thumbnail": "chatgpt"}}
    with pytest.raises(ValueError, match="thumbnails must be one of"):
        clean_options({"thumbnail": "midjourney"})
    assert load_config().render.thumbnail == "off"  # an extra: nothing is made unless it is switched on
    page = (Path(clipper.__file__).parent / "review_page.html").read_text(encoding="utf-8")
    for piece in ('id="o-thumb"', "makeCover(", "Let ChatGPT make it", "Make it from the clip (free)", "Make it from my description", 'name="t_cover"', "/cover.jpg"):
        assert piece in page, piece


def test_the_not_wanted_list_and_the_face_style_reach_chatgpt(tmp_path):
    from clipper import control
    from clipper.config import load_config

    cfg = load_config()
    cfg.render.thumbnail_face, cfg.render.thumbnail_avoid = True, "no guns,   no blood"
    state = {"channel": "Lacy", "title": "Cop chase gone wrong"}
    auto = thumbs.auto_prompt(state, cfg)
    assert "excited or shocked" in auto and "do not invent one" in auto and "does NOT want" in auto and "no guns, no blood" in auto
    assert auto.index("no guns") < auto.index("ONE image")  # the fixed rules still close the request
    mine = thumbs.own_prompt("a red car", cfg)
    assert "no guns, no blood" in mine and "excited or shocked" not in mine  # the person's own description decides the subject
    cfg.render.thumbnail_face, cfg.render.thumbnail_avoid = False, ""
    plain = thumbs.auto_prompt(state, cfg)
    assert "excited or shocked" not in plain and "does NOT want" not in plain
    assert control.clean_options({"thumbnail_avoid": "  no   guns ", "thumbnail_face": False}) == {"render": {"thumbnail_avoid": "no guns", "thumbnail_face": False}}
    for bad in ({"thumbnail_avoid": "x" * 501}, {"thumbnail_face": "yes"}):
        try:
            control.clean_options(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
