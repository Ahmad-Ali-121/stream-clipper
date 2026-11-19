import pytest

from clipper.pipeline import parse_clock, split_range


def test_times_can_be_written_three_ways():
    assert parse_clock("1:02:30") == 3750 and parse_clock("2:30") == 150 and parse_clock("45") == 45 and parse_clock(12.5) == 12.5
    for bad in ("", "1:2:3:4", "abc", "1::5", "-5"):
        with pytest.raises(ValueError):
            parse_clock(bad)


def test_a_range_splits_into_equal_parts():
    assert split_range(30, 180, parts=3) == [(30.0, 80.0), (80.0, 130.0), (130.0, 180.0)]
    assert split_range(0, 45) == [(0.0, 45.0)]


def test_a_range_splits_into_parts_of_a_set_length():
    # 150 s in parts of 50: exactly three.
    assert split_range(0, 150, part_s=50) == [(0.0, 50.0), (50.0, 100.0), (100.0, 150.0)]
    # 170 s: the 20 s left over is long enough to be a part of its own.
    assert split_range(0, 170, part_s=50)[-1] == (150.0, 170.0)
    # 155 s: 5 s left over is too short for a part, so the last part takes it.
    assert split_range(0, 155, part_s=50) == [(0.0, 50.0), (50.0, 100.0), (100.0, 155.0)]


def test_impossible_splits_say_why():
    with pytest.raises(ValueError, match="after the start"):
        split_range(50, 50)
    with pytest.raises(ValueError, match="more parts"):
        split_range(0, 900)                # one clip of 15 minutes
    with pytest.raises(ValueError, match="fewer parts"):
        split_range(0, 30, parts=10)       # parts of 3 seconds
    with pytest.raises(ValueError, match="at least"):
        split_range(0, 100, part_s=3)


def test_a_video_file_on_this_computer_is_a_source(tmp_path):
    from clipper.sources import SourceError, parse_source

    f = tmp_path / "My Screen Recording (2).mp4"
    f.write_bytes(b"x")
    s = parse_source(f'"{f}"')  # pasted with the quotes Windows adds
    assert s.platform == "local" and s.url == str(f.resolve()) and s.key.startswith("local_My-Screen-Recording-2-")
    assert parse_source(str(f)).key == s.key  # the same file always gets the same folder
    with pytest.raises(SourceError, match="video file"):
        parse_source(str(tmp_path / "missing.mp4"))


def test_parts_are_labelled_on_the_video_and_in_the_title():
    from clipper.captions import Overlay, build_ass
    from clipper.config import load_config
    from clipper.post import build_caption

    cfg = load_config()
    script = build_ass([], cfg.captions, [Overlay("Part 2", 0.0, 40.0, "Part"), Overlay("Part 3 next", 37.0, 40.0, "PartNext")])
    assert "Style: Part," in script and "Style: PartNext," in script and "PART 2" in script and "PART 3 NEXT" in script
    title, _desc, _tags = build_caption({"title": "He loses everything", "part": 2, "parts": 3, "channel": ""}, None)
    assert title == "He loses everything (Part 2)"
    assert build_caption({"title": "Part 2: he loses everything", "part": 2, "parts": 3, "channel": ""}, None)[0] == "Part 2: he loses everything"
    assert build_caption({"title": "One clip", "channel": ""}, None)[0] == "One clip"


def test_the_prompt_tells_claude_which_part_this_is():
    from clipper import llm

    p = llm.clip_prompt({"part": 1, "parts": 3, "trigger": "manual", "transcript": "hello", "start_s": 0, "end_s": 50}, [])
    assert "part 1 of 3" in p and "chosen by hand" in p and "Chat reaction" not in p
    q = llm.clip_prompt({"score": 4.2, "trigger": "chat", "transcript": "hello", "start_s": 0, "end_s": 30}, [])
    assert "Chat reaction: 4.2" in q and "part" not in q.split("Transcript")[0].lower()


def test_the_page_refuses_an_impossible_own_clip_before_starting(tmp_path):
    from clipper.config import load_config
    from clipper.control import Control

    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    f = tmp_path / "rec.mp4"
    f.write_bytes(b"x")
    c = Control(cfg)
    with pytest.raises(ValueError, match="after the start"):
        c.start_cut({"source": str(f), "start": 100, "end": 50})
    with pytest.raises(ValueError, match="more parts"):
        c.start_cut({"source": str(f), "start": 0, "end": 2000})
    with pytest.raises(ValueError):
        c.start_cut({"source": "not a link", "start": 0, "end": 30})
