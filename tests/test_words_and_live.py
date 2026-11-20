"""Transcript search helpers, chatbot parts, subtitle files, picked stretches, the hype meter, start-by-itself."""

import json

import pytest

from clipper import words
from clipper.config import load_config


def _cfg(tmp_path):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    return cfg


def test_a_long_transcript_is_cut_into_parts_with_a_ready_prompt():
    lines = [{"t": i * 10.0, "e": i * 10 + 9.0, "text": f"line {i}"} for i in range(400)]  # 66 minutes
    parts = words.chatbot_parts(lines, "My stream")
    assert [p["n"] for p in parts] == [1, 2, 3] and parts[1]["start_s"] == 1500.0
    assert "PART 2 of 3" in parts[1]["prompt"] and '"My stream"' in parts[1]["prompt"]
    assert "[0:25:00] line 150" in parts[1]["prompt"] and "line 149" not in parts[1]["prompt"]  # each line in one part only
    assert words.chatbot_parts([], "x") == []


def test_a_chatbots_answers_are_read_as_moments():
    r = words.parse_answers("""Here you go:
1. 1:02:30-1:03:15 | He drives into the lake
- [0:25:20] - [0:25:55]: Gun pickup
12:00 to 12:04 too short
* 1:40:00–1:50:00 | far too long
2:10:00-2:10:40 | after the end
1:02:50-1:03:20 | overlaps the first""", 7650)
    assert r["moments"] == [{"start_s": 1520.0, "end_s": 1555.0, "title": "Gun pickup"}, {"start_s": 3750.0, "end_s": 3795.0, "title": "He drives into the lake"}]
    assert len(r["skipped"]) == 4 and "shorter than 8" in r["skipped"][0]
    assert words.parse_answers('{"moments": [{"start": "10:00", "end": "10:40", "title": "x"}]}')["moments"][0]["start_s"] == 600.0
    assert words.parse_answers("nothing useful here") == {"moments": [], "skipped": []}


def test_subtitle_files_are_written_and_read_back():
    cues = [(1.0, 3.0, "hello there friend"), (4.5, 6.0, "bye")]
    assert words.parse_subtitles(words.to_srt(cues)) == cues and words.parse_subtitles(words.to_vtt(cues)) == cues
    assert words.to_srt(cues).startswith("1\n00:00:01,000 --> 00:00:03,000\nhello there friend\n")
    assert words.parse_subtitles("1\n00:00:01,000 --> 00:00:02,000\n<i>styled</i> {\\an8}text\n") == [(1.0, 2.0, "styled text")]
    spread = words.words_from_cues(cues, shift_s=5.0)  # a clip's words are timed from the start of its download
    assert [(w["word"], w["start"]) for w in spread[:3]] == [("hello", 6.0), ("there", 6.667), ("friend", 7.333)]
    with pytest.raises(ValueError, match="no subtitles"):
        words.parse_subtitles("just some text")


def test_a_clips_captions_become_subtitle_lines(tmp_path):
    d = tmp_path / "clips" / "0h00m10s"
    d.mkdir(parents=True)
    (d / "clip.json").write_text(json.dumps({"id": "0h00m10s", "start_s": 10, "end_s": 20, "fetch_start_s": 5}), encoding="utf-8")
    (d / "words.json").write_text(json.dumps([{"start": 4.0, "end": 4.5, "word": "before"}, {"start": 5.2, "end": 5.6, "word": "no"},
                                              {"start": 5.6, "end": 6.0, "word": "way"}, {"start": 9.0, "end": 9.5, "word": "bro"}]), encoding="utf-8")
    cues = words.clip_cues(d, _cfg(tmp_path))
    assert [c[2] for c in cues] == ["no way", "bro"] and cues[0][0] == pytest.approx(0.2)  # timed from the clip's own start


def test_picked_stretches_are_checked_before_anything_is_downloaded(tmp_path):
    from clipper.control import Control

    f = tmp_path / "rec.mp4"
    f.write_bytes(b"x")
    c = Control(_cfg(tmp_path))
    with pytest.raises(ValueError, match="8 to 300 seconds"):
        c.start_cut({"source": str(f), "ranges": [[0, 3]]})
    with pytest.raises(ValueError, match="start and an end"):
        c.start_cut({"source": str(f), "ranges": [["a"]]})
    with pytest.raises(ValueError, match="at most 30"):
        c.start_cut({"source": str(f), "ranges": [[i * 20, i * 20 + 10] for i in range(31)]})


def test_the_hype_meter_follows_chat_against_its_own_usual_pace():
    from clipper.watch import hype

    now = 1000.0
    steady = [(now - 600 + i * 6, i * 3) for i in range(101)]  # 30 messages a minute for ten minutes
    assert hype(steady, now) == {"percent": 25, "label": "quiet", "per_minute": 30}
    burst = steady[:-5] + [(now - 24 + i * 6, 285 + (i + 1) * 20) for i in range(5)]  # then 200 a minute
    assert hype(burst, now)["label"] == "peak hype" and hype(burst, now)["percent"] == 100
    assert hype([], now)["percent"] == 0 and hype([(now - 20, 0), (now, 30)], now)["percent"] == 75  # no "usual" yet: a fixed scale


def test_watching_starts_by_itself_only_when_asked(tmp_path, monkeypatch):
    from clipper.control import Control, clean_options

    cfg = _cfg(tmp_path)
    c = Control(cfg)
    started = []
    monkeypatch.setattr(c, "start_watch", lambda raw: started.append(raw))
    cfg.watch.autostart = False  # whatever this computer's own settings say
    c.autostart()
    assert started == []  # off unless switched on
    cfg.watch.autostart, cfg.watch.channels = True, ["lacy"]
    c.autostart()
    assert started == [{}]
    assert clean_options({"watch_autostart": True})["watch"]["autostart"] is True
    with pytest.raises(ValueError):
        clean_options({"watch_autostart": "yes"})
