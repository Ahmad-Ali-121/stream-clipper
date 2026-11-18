import pytest

from clipper.captions import Word, ass_time, build_ass, build_events, group_words, words_from_json
from clipper.config import CaptionSettings

S = CaptionSettings()


def w(start, end, text):
    return Word(start, end, text)


def test_ass_time_format_and_rounding():
    assert ass_time(0) == "0:00:00.00"
    assert ass_time(61.234) == "0:01:01.23"
    assert ass_time(59.999) == "0:01:00.00"  # rounds up and carries
    assert ass_time(3725.5) == "1:02:05.50"
    assert ass_time(-1) == "0:00:00.00"


def test_group_by_word_count():
    words = [w(i * 0.3, i * 0.3 + 0.25, "ab") for i in range(7)]
    assert [len(g) for g in group_words(words, max_words=3, max_chars=99)] == [3, 3, 1]


def test_group_by_width_and_pause():
    words = [w(0, 0.3, "unbelievable"), w(0.3, 0.6, "incredible"), w(2.0, 2.3, "wow"), w(2.3, 2.6, "ok")]
    groups = group_words(words, max_words=3, max_chars=18, max_gap_s=0.6)
    assert [[x.text for x in g] for g in groups] == [["unbelievable"], ["incredible"], ["wow", "ok"]]


def test_events_are_contiguous_and_never_overlap():
    words = [w(0.0, 0.2, "no"), w(0.5, 0.7, "way"), w(0.9, 1.0, "bro"), w(1.05, 1.3, "what"), w(1.3, 1.6, "is"), w(4.0, 4.4, "that")]
    events = build_events(words, S)
    assert len(events) == len(words)
    for a, b in zip(events, events[1:]):
        assert a.end <= b.start + 1e-9
    # inside a line the highlight holds until the next word starts (no blank gap)
    assert events[0].end == pytest.approx(0.5)
    assert events[1].end == pytest.approx(0.9)
    # the last word of a line is held for at least min_word_s (0.9 + 0.12),
    # but never past the next line's start (1.05)
    assert events[2].end == pytest.approx(1.02)
    assert events[4].end == pytest.approx(1.6)
    assert [e.active for e in events] == [0, 1, 2, 0, 1, 0]


def test_short_last_word_gets_minimum_duration():
    events = build_events([w(1.0, 1.01, "hi")], S)
    assert events[0].end - events[0].start == pytest.approx(S.min_word_s)


def test_words_in_same_centisecond_do_not_make_zero_length_events():
    events = build_events([w(1.000, 1.001, "a"), w(1.002, 1.4, "b")], S)
    assert all(ass_time(e.end) != ass_time(e.start) for e in events)


def test_words_from_json_offset_cleaning_and_sorting():
    items = [
        {"start": 105.0, "end": 105.4, "word": " crazy{\\b1} "},
        {"start": 100.2, "end": 100.6, "word": " That's"},
        {"start": 90.0, "end": 95.0, "word": "before the clip"},
        {"start": 99.8, "end": 100.1, "text": "Oh"},
        {"start": 101.0, "end": 101.2, "word": "   "},
    ]
    words = words_from_json(items, offset_s=100.0)
    assert [x.text for x in words] == ["Oh", "That's", "crazy(/b1)"]
    assert words[0].start == 0.0 and words[0].end == pytest.approx(0.1)


def test_build_ass_structure_and_highlight():
    ass = build_ass([w(0.0, 0.4, "no"), w(0.4, 0.9, "way")], S)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    dialogue = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogue) == 2
    # active colour FFE600 (RGB) is written as 00E6FF (BGR)
    assert dialogue[0] == "Dialogue: 0,0:00:00.00,0:00:00.40,Word,,0,0,0,,{\\c&H00E6FF&\\fscx112\\fscy112}NO{\\r} WAY"
    assert dialogue[1].endswith("NO {\\c&H00E6FF&\\fscx112\\fscy112}WAY{\\r}")


def test_bad_colour_is_a_clear_error():
    with pytest.raises(ValueError, match="RRGGBB"):
        build_ass([w(0, 1, "x")], CaptionSettings(active_color="yellow"))


def test_hook_pop_b_style_and_progress_bar():
    from clipper.captions import Overlay, build_ass
    from clipper.config import CaptionSettings

    s = CaptionSettings()
    script = build_ass([], s, [Overlay("first", 0, 3, "Hook", pop=True), Overlay("second", 0, 3, "HookB")], progress_s=30.0)
    lines = script.splitlines()
    hook = next(x for x in lines if ",Hook,," in x)
    hook_b = next(x for x in lines if ",HookB,," in x)
    assert "\\t(0,140," in hook and hook.endswith("FIRST")  # pops in
    assert "\\t(" not in hook_b and hook_b.endswith("SECOND")
    assert any(x.startswith("Style: HookB,") for x in lines) and any(x.startswith("Style: Shape,") for x in lines)
    bar = next(x for x in lines if ",Shape,," in x)
    assert "0:00:30.00" in bar and "\\t(0,30000,\\fscx100)" in bar and "l 1080 0" in bar
    assert not any(",Shape,," in x for x in build_ass([], s).splitlines() if x.startswith("Dialogue"))
