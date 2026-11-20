"""Faults found in the second quality pass (2026-11-21), each pinned by a test."""

import json
from dataclasses import replace

import pytest

from clipper.config import load_config


def test_round_brackets_are_speech_and_a_bracket_with_punctuation_still_closes():
    from clipper.ytcaptions import parse_json3

    def words(*lines):
        return [w["word"] for w in parse_json3({"events": [{"tStartMs": i * 4000, "dDurationMs": 3000, "segs": [{"utf8": t}]} for i, t in enumerate(lines)]})]

    assert words("We won (which is rare), so", "let us go again") == ["We", "won", "which", "is", "rare,", "so", "let", "us", "go", "again"]
    assert words("start [laughs]! and then", "more words") == ["start", "and", "then", "more", "words"]
    assert words("[Music playing] hello", "[Applause]", "bye") == ["hello", "bye"]


def test_a_moment_keeps_what_found_it_when_more_signals_are_added():
    from clipper.signals import add_signal_moments

    base = {"activity": 0, "messages": 0, "score": 4.0}
    found = [{**base, "peak_s": 500.0, "start_s": 480, "end_s": 510, "trigger": "chat+audio", "rank": 1},
             {**base, "peak_s": 2000.0, "start_s": 1985, "end_s": 2015, "trigger": "audio", "rank": 2},
             {**base, "peak_s": 3000.0, "start_s": 2985, "end_s": 3015, "trigger": "video", "rank": 3}]
    out = add_signal_moments(found, {"said": [(4000.0, 8.0), (505.0, 7.0)]}, extra=5)
    assert sorted((round(m["peak_s"]), m["trigger"]) for m in out) == [(500, "chat+audio+said"), (2000, "audio"), (3000, "video"), (4000, "said")]


def test_the_look_only_changes_when_something_visible_changes(tmp_path):
    from clipper.render import look_signature

    cfg = load_config()
    cfg.brand.logo = cfg.brand.music = cfg.brand.outro_text = ""
    cfg.render.template = "none"
    base = look_signature(cfg)
    cfg.render.hook_seconds = float(cfg.render.hook_seconds)  # 3 saved as 3.0 by the page
    cfg.captions.preset = "My profile"  # the same look saved under a name
    cfg.brand.logo_corner, cfg.brand.music_peak, cfg.brand.outro_seconds = "bottom-right", 0.7, 9  # settings of things that are off
    cfg.render.card_color, cfg.render.post_name = "ABCDEF", "Someone"  # settings of a template that is not used
    assert look_signature(cfg) == base
    cfg.captions.font_size += 2
    changed = look_signature(cfg)
    assert changed != base
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"one")
    cfg.brand.logo = str(logo)
    with_logo = look_signature(cfg)
    assert with_logo != changed
    logo.write_bytes(b"another, longer logo")  # another picture saved under the same name
    assert look_signature(cfg) != with_logo


def test_older_drafts_are_judged_by_the_record_they_carry():
    from clipper.pipeline import _look_changed
    from clipper.render import look_signature, look_signature_v1

    cfg = load_config()
    assert not _look_changed({"look2_used": look_signature(cfg)}, cfg)
    assert not _look_changed({"look_used": look_signature_v1(cfg)}, cfg)  # made before the new record: not remade for nothing
    assert not _look_changed({"style_used": cfg.captions.preset}, cfg)
    assert _look_changed({"look2_used": "v2-something-else"}, cfg)


def test_a_tall_video_is_fitted_into_the_blurred_layout_not_squashed():
    from clipper.render import layout_filter

    r = replace(load_config().render, game=[])
    tall = layout_filter(1080, 2400, r, "blur")
    assert "scale=864:1920" in tall and "overlay=108:0" in tall  # the same shape as the source, centred
    assert "scale=1080:608" in layout_filter(1920, 1080, r, "blur")


def test_excited_phrases_end_where_a_word_ends():
    from clipper import said

    plain = [{"start": i * 0.4, "word": w} for i, w in enumerate("so what the first thing we do is get into the car oh nothing happened oh many times".split())]
    assert said.strength(plain, 30) < 2
    real = [{"start": i * 0.4, "word": w} for i, w in enumerate("what the f oh no get in oh man".split())]
    assert said.strength(real, 30) >= 4


def test_chatbot_answers_in_a_code_fence_and_with_odd_hyphens_are_read():
    from clipper.words import parse_answers

    fenced = '```json\n[{"start": "10:00", "end": "10:40", "title": "x"}]\n```'
    assert parse_answers(fenced)["moments"] == [{"start_s": 600.0, "end_s": 640.0, "title": "x"}]
    assert parse_answers("1:02:30‑1:03:15 | with a non-breaking hyphen")["moments"][0]["start_s"] == 3750.0


def test_the_hype_meter_survives_a_pause_in_its_readings():
    from clipper.watch import hype

    now = 1000.0
    samples = [(now - 300 + i * 6, i * 6) for i in range(40)] + [(now, 300)]  # one message a second; the last-but-one reading is 66 s old
    assert hype(samples, now)["per_minute"] > 30


def test_a_damaged_decisions_record_is_never_written_over_with_less(tmp_path, caplog):
    from clipper.review import apply_edit
    from clipper.work import LEDGER, restore_decisions

    work = tmp_path / "work" / "twitch_1"
    for cid in ("0h00m10s", "0h00m50s"):
        d = work / "clips" / cid
        d.mkdir(parents=True)
        (d / "clip.json").write_text(json.dumps({"id": cid, "start_s": 10, "end_s": 40, "status": "candidate"}), encoding="utf-8")
    apply_edit(work, "0h00m10s", {"status": "approved"})
    (tmp_path / LEDGER).write_text("{ this is not json", encoding="utf-8")
    apply_edit(work, "0h00m50s", {"status": "rejected"})  # the record cannot be read: it is left alone, and that is said
    assert (tmp_path / LEDGER).read_text(encoding="utf-8") == "{ this is not json" and "DECISIONS RECORD COULD NOT BE READ" in caplog.text
    assert restore_decisions(tmp_path) == 0  # at the next start the damaged file is put aside and a new one is filled from the clips
    fresh = json.loads((tmp_path / LEDGER).read_text(encoding="utf-8"))
    assert {k: v["status"] for k, v in fresh.items()} == {"twitch_1/0h00m10s": "approved", "twitch_1/0h00m50s": "rejected"}
    assert list(tmp_path.glob("decisions.damaged-*.json"))


def test_an_own_clip_does_not_take_over_a_found_clips_folder(tmp_path):
    from clipper.pipeline import _own_drafts
    from clipper.sources import Source

    found = tmp_path / "clips" / "0h12m30s"
    found.mkdir(parents=True)
    (found / "clip.json").write_text(json.dumps({"id": "0h12m30s", "trigger": "chat", "status": "approved", "start_s": 740, "end_s": 770}), encoding="utf-8")
    (found / "final.mp4").write_bytes(b"x")
    (cid,) = _own_drafts(Source("youtube", "x", "https://example.invalid/v"), tmp_path, {}, [(750.4, 790.0)], False)
    assert cid == "0h12m30sx" and (found / "final.mp4").exists()
    assert json.loads((found / "clip.json").read_text(encoding="utf-8"))["status"] == "approved"


def test_a_box_starting_in_the_last_sliver_of_the_picture_is_refused():
    from clipper.review import clean_box

    with pytest.raises(ValueError, match="outside the picture"):
        clean_box([0.9996, 0.5, 0.04, 0.04], "face")
    assert clean_box([0.9, 0.9, 0.5, 0.5], "face") == [0.9, 0.9, 0.1, 0.1]
