from clipper.streamread import add_ai_moments, clean_picks, read_rows, transcript_lines


def test_speech_is_grouped_into_timed_lines():
    rows = [{"t": 0.0, "e": 4.0, "text": "hello chat"}, {"t": 5.0, "e": 9.0, "text": "what is up"}, {"t": 31.0, "e": 35.0, "text": "no way"},
            {"t": 40.0, "e": 44.0, "text": "he did not"}, {"t": 70.5, "e": 72.0, "text": "run"}]
    assert transcript_lines(rows, line_s=30) == ["[0] hello chat what is up", "[31] no way he did not", "[70] run"]


def test_a_cut_off_last_line_is_skipped(tmp_path):
    p = tmp_path / "stream_text.jsonl"
    p.write_text('{"t": 1.0, "e": 2.0, "text": "ok"}\n{"t": 3.0, "e"', encoding="utf-8")
    assert read_rows(p) == [{"t": 1.0, "e": 2.0, "text": "ok"}]


def test_picks_are_made_safe():
    raw = [
        {"start_s": 100, "end_s": 130, "strength": 6, "reason": "a"},
        {"start_s": 120, "end_s": 150, "strength": 9, "reason": "overlaps a, stronger"},
        {"start_s": 500, "end_s": 700, "strength": 7, "reason": "too long"},
        {"start_s": 900, "end_s": 905, "strength": 5, "reason": "too short"},
        {"start_s": 3590, "end_s": 3620, "strength": 8, "reason": "past the end"},
        {"start_s": "x", "end_s": 10, "strength": 8, "reason": "broken"},
    ]
    out = clean_picks(raw, top=10, duration_s=3600)
    assert [(m["start_s"], m["end_s"]) for m in out] == [(120.0, 150.0), (500.0, 560.0), (900.0, 930.0)]
    assert clean_picks(raw, top=1, duration_s=3600)[0]["reason"] == "overlaps a, stronger"


def test_ai_marks_moments_it_agrees_with_and_adds_its_own():
    found = [{"rank": 1, "peak_s": 1000.0, "start_s": 980.0, "end_s": 1010.0, "score": 4.0, "activity": 30, "messages": 40, "hype": 0, "trigger": "chat"}]
    picks = [{"start_s": 990.0, "end_s": 1030.0, "strength": 8, "reason": "same moment"}, {"start_s": 2000.0, "end_s": 2040.0, "strength": 7, "reason": "only in the speech"}]
    out = add_ai_moments(found, picks)
    assert out[0]["trigger"] == "chat+ai" and out[0]["ai_pick"] == "same moment" and (out[0]["start_s"], out[0]["end_s"]) == (980.0, 1010.0)
    assert out[1]["trigger"] == "ai" and out[1]["rank"] == 2 and (out[1]["start_s"], out[1]["end_s"]) == (2000.0, 2040.0)
    assert found[0]["trigger"] == "chat"  # the input list is left alone
