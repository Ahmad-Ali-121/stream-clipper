import json

import pytest

from clipper.brief import Brief
from clipper.captions import Overlay, Word, build_ass
from clipper.config import CaptionSettings, RenderSettings
from clipper.post import build_caption, export_clip, post_sheet
from clipper.render import credit_text, cut_window, layout_filter
from clipper.review import apply_edit
from clipper.sources import SourceError, parse_source
from clipper.work import clip_id, read_json, write_json

# ---- sources ----


def test_parse_twitch_and_youtube_urls():
    assert parse_source("https://www.twitch.tv/videos/2930061578?t=1h2m").key == "twitch_2930061578"
    assert parse_source("https://www.twitch.tv/lacy/v/2930061578").video_id == "2930061578"
    assert parse_source("2930061578").url == "https://www.twitch.tv/videos/2930061578"
    for u in ("https://youtu.be/abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk&t=5", "https://www.youtube.com/live/abcdefghijk"):
        s = parse_source(u)
        assert (s.platform, s.video_id) == ("youtube", "abcdefghijk")


def test_channel_url_is_rejected_with_guidance():
    with pytest.raises(SourceError, match="channel link is not enough"):
        parse_source("https://www.twitch.tv/lacy")


def test_clip_id_is_position_in_stream():
    assert clip_id(2320) == "0h38m40s"
    assert clip_id(9010.7) == "2h30m10s"


# ---- render geometry ----

R = RenderSettings()


def test_crop_layout_is_a_centred_9_16_window():
    f = layout_filter(1920, 1080, R, "crop")
    assert f.startswith("[0:v]crop=608:1080:656:0,scale=1080:1920")
    assert f.endswith("[v0]")


def test_crop_x_moves_the_window_and_is_clamped():
    from dataclasses import replace

    assert "crop=608:1080:0:0" in layout_filter(1920, 1080, replace(R, crop_x=0.0), "crop")
    assert "crop=608:1080:1312:0" in layout_filter(1920, 1080, replace(R, crop_x=1.0), "crop")


def test_split_layout_stacks_facecam_over_gameplay_without_showing_the_face_twice():
    from dataclasses import replace

    r = replace(R, facecam=[0.0, 0.0, 0.28, 0.28], split_top=0.38)
    f = layout_filter(1920, 1080, r, "split")
    top_h = 730
    assert f"[fa]crop=538:302:0:0,scale=1080:{top_h}" in f
    # gameplay crop starts right of the facecam (x >= 538), not at the centred 470
    assert f"[ga]crop=980:1080:538:0,scale=1080:{1920 - top_h}" in f
    assert f.endswith("[top][bot]vstack=inputs=2[v0]")


def test_bad_layout_and_facecam_are_clear_errors():
    from dataclasses import replace

    with pytest.raises(ValueError, match="layout"):
        layout_filter(1920, 1080, R, "sideways")
    with pytest.raises(ValueError, match="facecam"):
        layout_filter(1920, 1080, replace(R, facecam=[0, 0, 2, 2]), "split")


def test_cut_window_uses_review_overrides_and_source_length():
    state = {"id": "x", "start_s": 2300, "end_s": 2330, "fetch_start_s": 2297, "source_duration_s": 36.05}
    assert cut_window(state) == (3.0, 30.0)
    assert cut_window({**state, "cut_start_s": 2305, "cut_end_s": 2320}) == (8.0, 15.0)
    offset, duration = cut_window({**state, "cut_end_s": 2400})  # never past the end of the file
    assert offset == 3.0 and duration == pytest.approx(33.05)
    with pytest.raises(ValueError, match="empty"):
        cut_window({**state, "cut_start_s": 2330, "cut_end_s": 2320})


def test_credit_text():
    assert credit_text({"channel": "Lacy", "platform": "twitch"}) == "Clip: twitch.tv/Lacy"
    assert credit_text({"channel": ""}) == ""


def test_overlays_are_written_with_their_styles():
    ass = build_ass(
        [Word(0, 0.5, "hi")],
        CaptionSettings(),
        [Overlay("He did {what}", 0, 3, "Hook"), Overlay("Clip: twitch.tv/x", 0, 30, "Credit"), Overlay("Ad", 0, 4, "Disclosure"), Overlay("", 0, 3, "Hook")],
    )
    lines = [x for x in ass.splitlines() if x.startswith("Dialogue: 1,")]
    assert lines == [
        "Dialogue: 1,0:00:00.00,0:00:03.00,Hook,,0,0,0,,{\\q0}HE DID (WHAT)",
        "Dialogue: 1,0:00:00.00,0:00:30.00,Credit,,0,0,0,,{\\q0}Clip: twitch.tv/x",
        "Dialogue: 1,0:00:00.00,0:00:04.00,Disclosure,,0,0,0,,{\\q0}Ad",
    ]
    with pytest.raises(ValueError, match="overlay style"):
        build_ass([], CaptionSettings(), [Overlay("x", 0, 1, "Banner")])


# ---- review edits ----


@pytest.fixture
def work(tmp_path):
    d = tmp_path / "clips" / "0h38m40s"
    d.mkdir(parents=True)
    write_json(
        d / "clip.json",
        {"id": "0h38m40s", "rank": 1, "start_s": 2300, "end_s": 2330, "fetch_start_s": 2297, "fetch_end_s": 2333, "status": "candidate",
         "channel": "Lacy", "platform": "twitch", "source_url": "https://www.twitch.tv/lacy", "vod_url": "https://www.twitch.tv/videos/1",
         "duration_s": 30.0, "width": 1080, "height": 1920},
    )
    (d / "final.mp4").write_bytes(b"video")
    return tmp_path


def test_approve_keeps_render_but_changing_the_cut_discards_it(work):
    d = work / "clips" / "0h38m40s"
    assert apply_edit(work, "0h38m40s", {"status": "approved"})["status"] == "approved"
    assert (d / "final.mp4").exists()
    apply_edit(work, "0h38m40s", {"cut_start_s": 2305.0, "hook": "New hook"})
    assert not (d / "final.mp4").exists()  # stale render removed so it cannot be posted by mistake
    assert read_json(d / "clip.json")["hook"] == "New hook"


def test_review_rejects_bad_edits(work):
    with pytest.raises(ValueError, match="fetched range"):
        apply_edit(work, "0h38m40s", {"cut_start_s": 2000.0})
    with pytest.raises(ValueError, match="status"):
        apply_edit(work, "0h38m40s", {"status": "posted"})
    with pytest.raises(ValueError, match="cannot be edited"):
        apply_edit(work, "0h38m40s", {"source_url": "https://evil.example"})
    with pytest.raises(KeyError):
        apply_edit(work, "../../etc", {"status": "approved"})


# ---- post sheets ----

BRIEF = Brief(name="Lacy", allowed_sources=["twitch.tv/lacy"], required_tags=["#LACY"], required_text=["Lacy"])


def test_caption_includes_required_campaign_text_and_tags(work):
    state = read_json(work / "clips" / "0h38m40s" / "clip.json")
    state["hook"] = "He could not name one job"
    title, description, tags = build_caption(state, BRIEF)
    assert title == "He could not name one job"
    assert "Lacy" in description and "https://www.twitch.tv/lacy" in description
    assert tags[0] == "#LACY" and "#clips" in tags
    assert build_caption({**state, "tags": ["gta6", "#Lacy"]}, BRIEF)[2] == ["#LACY", "#gta6"]


def test_post_sheet_reports_brief_result(work):
    state = read_json(work / "clips" / "0h38m40s" / "clip.json")
    sheet, issues = post_sheet(state, "Lacy_0h38m40s.mp4", BRIEF)
    assert "passes the automatic checks" in sheet and "=== TIKTOK ===" in sheet
    bad, _ = post_sheet({**state, "source_url": "https://www.twitch.tv/someoneelse"}, "x.mp4", BRIEF)
    assert "WOULD BE REJECTED" in bad


def test_export_copies_video_and_sheet(work, tmp_path):
    video, ok = export_clip(work / "clips" / "0h38m40s", tmp_path / "ready", BRIEF)
    assert ok and video.read_bytes() == b"video" and video.name == "Lacy_0h38m40s.mp4"
    assert "YOUTUBE SHORTS" in video.with_suffix(".txt").read_text(encoding="utf-8")
    assert json.loads((work / "clips" / "0h38m40s" / "clip.json").read_text(encoding="utf-8"))["brief_ok"] is True


# ---- live watch ----


def test_channel_name_from_url_or_name():
    from clipper.watch import channel_name

    assert channel_name("https://www.twitch.tv/Lacy") == "lacy"
    assert channel_name("KaiCenat") == "kaicenat"
    for bad in ("https://www.twitch.tv/videos/123", "a", "not a channel!"):
        with pytest.raises(ValueError):
            channel_name(bad)


def test_parse_irc_chat_lines():
    from clipper.watch import parse_irc_line

    assert parse_irc_line(":bob!bob@bob.tmi.twitch.tv PRIVMSG #lacy :W drew") == ("bob", "W drew")
    assert parse_irc_line("@badges=;color=#FF0000 :amy!amy@amy.tmi.twitch.tv PRIVMSG #lacy :no way: really?") == ("amy", "no way: really?")
    assert parse_irc_line("PING :tmi.twitch.tv") is None
    assert parse_irc_line(":tmi.twitch.tv 001 justinfan73512 :Welcome, GLHF!") is None


def test_live_moments_ignore_the_time_before_the_watch_began(tmp_path):
    import time as _time
    from types import SimpleNamespace

    from clipper.config import Config
    from clipper.watch import new_moments

    cfg = Config()
    work = tmp_path
    lines = []
    # Watch begins 2 hours into the stream. Ordinary chat: 3 chatters per 10 s for 10 minutes.
    for start in range(7200, 7800, 10):
        lines += [json.dumps({"t": start + k, "author": f"u{start}_{k}", "text": "hi"}) for k in range(3)]
    (work / "chat.json").write_text("\n".join(lines), encoding="utf-8")
    w = SimpleNamespace(work=work, watch_from=7200.0, meta={"started_at": _time.time() - 7900})
    # Without dropping the empty first two hours, every bucket would look like a spike over a silent baseline.
    assert new_moments(w, cfg) == []
    spike = [json.dumps({"t": 7700 + i * 0.1, "author": f"s{i}", "text": "NO WAY"}) for i in range(60)]
    (work / "chat.json").write_text("\n".join(lines + spike), encoding="utf-8")
    found = new_moments(w, cfg)
    assert [m["peak_s"] for m in found] == [7700]
    # A moment the VOD has not caught up with yet is left for the next poll.
    w.meta["started_at"] = _time.time() - 7720
    assert new_moments(w, cfg) == []


def test_review_edit_of_text_marks_it_as_typed_and_drops_stale_videos(tmp_path):
    from clipper.review import apply_edit
    from clipper.work import read_json, write_json

    d = tmp_path / "clips" / "0h01m00s"
    d.mkdir(parents=True)
    write_json(d / "clip.json", {"id": "0h01m00s", "start_s": 60, "end_s": 90, "hook": "a", "hook_b": "b", "intro": "i",
                                 "copy_auto": ["hook", "hook_b", "intro", "title"]})
    for name in ("final.mp4", "final_b.mp4", "explained.mp4"):
        (d / name).write_bytes(b"x")
    state = apply_edit(tmp_path, "0h01m00s", {"hook_b": "my own b", "explainer": True})
    assert state["explainer"] is True and state["copy_auto"] == ["hook", "intro", "title"]  # hook_b is now a person's text
    assert (d / "final.mp4").exists() and (d / "explained.mp4").exists() and not (d / "final_b.mp4").exists()
    apply_edit(tmp_path, "0h01m00s", {"intro": "new line"})
    assert (d / "final.mp4").exists() and not (d / "explained.mp4").exists()
    apply_edit(tmp_path, "0h01m00s", {"hook": "new hook"})
    assert not (d / "final.mp4").exists() and "hook" not in read_json(d / "clip.json")["copy_auto"]


def test_b_version_is_exported_with_its_own_title_and_an_ab_note(tmp_path):
    from clipper.post import export_clip
    from clipper.work import write_json

    d = tmp_path / "clips" / "0h01m00s"
    d.mkdir(parents=True)
    write_json(d / "clip.json", {"id": "0h01m00s", "channel": "Lacy", "start_s": 60, "end_s": 90, "hook": "A hook", "hook_b": "B hook",
                                 "title": "Title A", "title_b": "Title B", "duration_s": 30, "width": 1080, "height": 1920})
    (d / "final.mp4").write_bytes(b"a")
    (d / "final_b.mp4").write_bytes(b"b")
    a, _ = export_clip(d, tmp_path / "ready", suffix="_A")
    b, _ = export_clip(d, tmp_path / "ready", video_name="final_b.mp4", suffix="_B", main=False)
    assert a.name == "Title A.mp4" and b.name == "Title B.mp4" and b.read_bytes() == b"b"  # each version carries its own title
    sheet_a, sheet_b = a.with_suffix(".txt").read_text(encoding="utf-8"), b.with_suffix(".txt").read_text(encoding="utf-8")
    assert "Title: Title A" in sheet_a and "Title: Title B" in sheet_b
    assert "This is version B" in sheet_b and "Never submit both to the same campaign" in sheet_a


# ---- audio and video signals ----


def test_parse_mode_and_feed_lines():
    import pytest as _pytest

    from clipper.signals import parse_feed_lines, parse_mode

    assert parse_mode("B") == "audio" and parse_mode("all") == "all" and parse_mode("", "video") == "video"
    with _pytest.raises(ValueError):
        parse_mode("E")
    lines = ["frame:0    pts:155     pts_time:29675.3", "lavfi.astats.Overall.RMS_level=-28.96",
             "frame:1    pts:16155   pts_time:29676.3", "lavfi.astats.Overall.RMS_level=-inf", "junk"]
    assert parse_feed_lines(lines) == [(29675.3, -28.96), (29676.3, -90.0)]


def test_a_loud_burst_stands_out_and_quiet_start_does_not():
    from clipper.signals import bucket_values, pick_peaks, unusual

    samples = [(float(t), -30.0 + (t % 3)) for t in range(600)]  # ten minutes of ordinary sound, one value a second
    samples[400:404] = [(400.0, -8.0), (401.0, -7.0), (402.0, -9.0), (403.0, -30.0)]  # a shout at 6:40
    values = bucket_values(samples, 10, top=3)
    scores = unusual(values, window=30, floor=1.5)
    assert min(scores) == 12  # nothing is judged before two minutes of history exist
    peaks = pick_peaks(scores, threshold=5.0, gap_buckets=30)
    assert [i for i, _ in peaks] == [40]


def test_signals_time_is_aligned_from_the_fastest_arrival(tmp_path):
    import json as _json

    from clipper.signals import load_signals

    p = tmp_path / "signals.jsonl"
    rows = [{"k": "a", "pts": 1000.0, "lag": -50.0, "v": -30}, {"k": "a", "pts": 1010.0, "lag": -57.0, "v": -20}, {"k": "v", "pts": 1010.0, "lag": -55.0, "v": 0.1}]
    p.write_text("\n".join(_json.dumps(r) for r in rows) + "\n{broken", encoding="utf-8")
    data = load_signals(p, feed_delay_s=8.0)
    assert data["a"] == [(935.0, -30.0), (945.0, -20.0)] and data["v"] == [(945.0, 0.1)]  # offset = -57 - 8
    # After an ad break the feed's clock jumps: that stretch is lined up on its own.
    rows += [{"k": "a", "pts": 90000.0, "lag": -88900.0, "v": -10}, {"k": "a", "pts": 90001.0, "lag": -88901.0, "v": -11}]
    p.write_text("\n".join(_json.dumps(r) for r in rows), encoding="utf-8")
    assert load_signals(p, feed_delay_s=8.0)["a"][2:] == [(1091.0, -10.0), (1092.0, -11.0)]


def test_merge_marks_chat_moments_and_adds_signal_only_ones():
    from clipper.signals import merge_moments

    chat = [{"peak_s": 500.0, "start_s": 480.0, "end_s": 510.0, "score": 3.1, "activity": 40, "messages": 60, "hype": 2}]
    out = merge_moments(chat, {"audio": [(485.0, 6.2), (2000.0, 7.5)], "video": [(490.0, 8.0)]})
    assert [m["trigger"] for m in out] == ["chat+audio+video", "audio"]
    assert out[0]["signals"] == {"audio": 6.2, "video": 8.0} and out[0]["score"] == 3.1
    assert (out[1]["start_s"], out[1]["end_s"], out[1]["score"]) == (1985.0, 2015.0, 7.5)


def test_options_panel_validates_and_saves_to_the_local_file(tmp_path):
    import pytest as _pytest
    import yaml as _yaml

    from clipper.control import clean_options, save_local

    assert clean_options({"mode": "D", "ab_versions": False, "channels": "Lacy, https://www.twitch.tv/xQc"}) == {
        "watch": {"mode": "all", "channels": ["lacy", "xqc"]}, "render": {"ab_versions": False}}
    for bad in ({"mode": "E"}, {"opening": "yes"}, {"paths": "x"}, {"channels": "not a channel!"}):
        with _pytest.raises(ValueError):
            clean_options(bad)
    (tmp_path / "config.local.yaml").write_text("paths:\n  data_dir: D:/clipper-data\n", encoding="utf-8")
    save_local({"watch": {"mode": "audio"}}, folder=tmp_path)
    save_local({"render": {"opening": False}, "watch": {"channels": ["lacy"]}}, folder=tmp_path)
    assert _yaml.safe_load((tmp_path / "config.local.yaml").read_text(encoding="utf-8")) == {
        "paths": {"data_dir": "D:/clipper-data"}, "watch": {"mode": "audio", "channels": ["lacy"]}, "render": {"opening": False}}


def _clip(tmp_path, **extra):
    from clipper.work import write_json

    d = tmp_path / "twitch_1" / "clips" / "0h01m00s"
    d.mkdir(parents=True)
    write_json(d / "clip.json", {"id": "0h01m00s", "status": "candidate", "start_s": 40, "end_s": 70, "fetch_start_s": 35, "fetch_end_s": 75,
                                 "channel": "Someone", "score": 3.2, "tags": ["#a"], "copy_auto": ["title", "tags", "hook"], **extra})
    return d


def test_page_can_edit_upload_text_and_keeps_it_from_the_automatic_writer(tmp_path):
    from clipper.review import apply_edit

    d = _clip(tmp_path)
    state = apply_edit(d.parent.parent, "0h01m00s", {"title": "My title", "tags": "one #two, three"})
    assert state["title"] == "My title" and state["tags"] == ["#one", "#two", "#three"]
    assert state["copy_auto"] == ["hook"]  # typed fields are no longer the writer's to replace


def test_clip_view_has_what_the_page_draws(tmp_path):
    from clipper.review import clip_view

    d = _clip(tmp_path, error="12:00:00 draft failed: example")
    v = clip_view(d)
    assert v["key"] == "twitch_1/0h01m00s" and v["status"] == "candidate" and v["video"] == ""
    assert (v["lo"], v["hi"]) == (35, 75) and v["error"].endswith("example") and v["mtime"] > 0
    (d / "source.mp4").write_bytes(b"x")
    assert clip_view(d)["video"] == "source.mp4"


def test_page_file_fills_in_title_safely():
    from clipper.review import page_html

    page = page_html('A </script> "title"', True)
    assert "__TITLE__" not in page and "__HAS_PANEL__" not in page
    assert '</script> "title"' not in page  # the title cannot close the script block


def test_finished_stream_button_refuses_what_is_not_a_video_link(tmp_path):
    import pytest

    from clipper.config import load_config
    from clipper.control import Control

    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    c = Control(cfg)
    with pytest.raises(ValueError):
        c.start_run({"url": "https://www.twitch.tv/somechannel"})
    with pytest.raises(ValueError):
        c.start_run({"url": "https://www.twitch.tv/videos/1234567890", "top": 99})


def test_finished_stream_signal_rows_carry_the_stream_second(tmp_path):
    import json

    from clipper.signals import load_signals

    p = tmp_path / "signals_vod.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [{"k": "a", "t": 12.0, "v": -20.5}, {"k": "v", "t": 12.5, "v": 0.3}, {"k": "a", "t": 11.0, "v": -30.0}]), encoding="utf-8")
    data = load_signals(p, feed_delay_s=8.0)
    assert data["a"] == [(11.0, -30.0), (12.0, -20.5)] and data["v"] == [(12.5, 0.3)]  # no delay applied, sorted


def test_signal_only_moments_are_added_after_the_chat_ones_and_limited():
    from clipper.signals import add_signal_moments

    chat = [{"rank": 1, "peak_s": 1000.0, "start_s": 980.0, "end_s": 1010.0, "score": 4.0, "activity": 30, "messages": 40, "hype": 0, "trigger": "chat"},
            {"rank": 2, "peak_s": 5000.0, "start_s": 4980.0, "end_s": 5010.0, "score": 3.0, "activity": 20, "messages": 25, "hype": 0, "trigger": "chat"}]
    peaks = {"audio": [(990.0, 6.0), (2000.0, 7.0), (3000.0, 9.0), (4000.0, 5.5)], "video": []}
    out = add_signal_moments(chat, peaks, extra=2)
    assert [m["trigger"] for m in out] == ["chat+audio", "chat", "audio", "audio"]
    assert out[0]["signals"] == {"audio": 6.0}
    assert [(m["peak_s"], m["rank"]) for m in out[2:]] == [(3000.0, 3), (2000.0, 4)]  # strongest first, the weakest left out


def test_sound_returning_after_silence_is_not_a_loud_moment(tmp_path):
    import json

    from clipper.config import load_config
    from clipper.signals import signal_peaks

    rows = [{"k": "a", "t": float(t), "v": -90.0} for t in range(0, 300)]           # muted intro
    rows += [{"k": "a", "t": float(t), "v": -25.0} for t in range(300, 2000)]        # normal talking
    rows += [{"k": "a", "t": float(t), "v": -5.0} for t in range(2000, 2010)]        # a shout
    rows += [{"k": "a", "t": float(t), "v": -25.0} for t in range(2010, 2600)]
    p = tmp_path / "signals_vod.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    peaks = signal_peaks(p, load_config(), since_s=0)["audio"]
    assert [round(t) for t, _z in peaks] == [2005]


def test_deleting_a_clip_removes_its_files_and_hides_it(tmp_path):
    from clipper.review import delete_clip, list_clips
    from clipper.work import read_json

    d = _clip(tmp_path, error="old")
    (d / "source.mp4").write_bytes(b"x" * 2_000_000)
    (d / "final.mp4").write_bytes(b"y" * 1_000_000)
    work = d.parent.parent
    assert list_clips([work]) == [d]
    assert delete_clip(d) == 3.0
    assert sorted(f.name for f in d.iterdir()) == ["clip.json"]  # kept, so no later run makes the clip again
    state = read_json(d / "clip.json")
    assert state["status"] == "deleted" and "error" not in state
    assert list_clips([work]) == []


def test_two_review_pages_cannot_share_a_port():
    from http.server import BaseHTTPRequestHandler

    import pytest

    from clipper.review import OneAtATimeServer

    first = OneAtATimeServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    try:
        with pytest.raises(OSError):
            OneAtATimeServer(("127.0.0.1", first.server_address[1]), BaseHTTPRequestHandler)
    finally:
        first.server_close()


def test_caption_words_can_be_fixed_and_removed(tmp_path):
    from clipper.review import save_words
    from clipper.work import read_json, write_json

    d = _clip(tmp_path)
    write_json(d / "words.json", [{"word": "helo", "start": 1.0, "end": 1.2, "probability": 0.9}, {"word": "um", "start": 1.3, "end": 1.4}, {"word": "world", "start": 1.5, "end": 1.9}])
    (d / "final.mp4").write_bytes(b"old render")
    left = save_words(d, [{"word": "hello", "start": 1.0, "end": 1.2, "probability": 0.9}, {"word": " ", "start": 1.3, "end": 1.4}, {"word": "world", "start": 1.5, "end": 1.9}])
    assert left == 2
    assert [w["word"] for w in read_json(d / "words.json")] == ["hello", "world"] and read_json(d / "words.json")[0]["probability"] == 0.9
    state = read_json(d / "clip.json")
    assert state["transcript"] == "hello world" and state["words_edited"] is True
    assert not (d / "final.mp4").exists()  # captions are burned in, so the old render is gone


def test_caption_style_choice_sets_the_caption_settings():
    import pytest

    from clipper.control import CAPTION_PRESETS, clean_options

    out = clean_options({"caption_preset": "impact"})
    assert out["captions"]["preset"] == "impact" and out["captions"]["font"] == "Impact" and out["captions"]["max_words"] == 2
    assert set(CAPTION_PRESETS) >= {"bold", "impact", "yellow", "clean"}
    with pytest.raises(ValueError):
        clean_options({"caption_preset": "comic sans"})


def test_finish_reports_progress_and_carries_on_past_a_bad_clip(tmp_path, monkeypatch):
    from clipper import pipeline
    from clipper.config import load_config
    from clipper.work import write_json

    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    for name in ("0h01m00s", "0h02m00s"):
        d = tmp_path / "work" / "twitch_1" / "clips" / name
        d.mkdir(parents=True)
        write_json(d / "clip.json", {"id": name, "status": "approved", "start_s": 40, "end_s": 70, "channel": "Someone"})
    seen, problems = [], []
    pipeline.stage_finish(cfg, problems=problems, progress=lambda done, total, now: seen.append((done, total)))
    assert seen == [(0, 2), (1, 2), (2, 2)]
    assert len(problems) == 2 and "never downloaded" in problems[0]  # both reported, neither stopped the other


def test_approving_an_untouched_clip_keeps_its_draft_video(tmp_path):
    from clipper.review import apply_edit

    d = _clip(tmp_path, hook="A hook")
    (d / "final.mp4").write_bytes(b"draft")
    # The page always sends the cut and layout back, even when nothing was changed.
    apply_edit(d.parent.parent, "0h01m00s", {"status": "approved", "hook": "A hook", "layout": "", "disclosure": False, "cut_start_s": 40.0, "cut_end_s": 70.0})
    assert (d / "final.mp4").exists()
    apply_edit(d.parent.parent, "0h01m00s", {"cut_start_s": 42.0, "cut_end_s": 70.0})
    assert not (d / "final.mp4").exists()  # a real change still makes the old video stale


def test_an_automatic_step_cannot_undo_what_a_person_decided(tmp_path):
    from clipper.review import apply_edit
    from clipper.work import read_json, save_clip

    d = _clip(tmp_path)
    stale = read_json(d / "clip.json")              # the watcher read the clip while making its draft...
    apply_edit(d.parent.parent, "0h01m00s", {"status": "approved", "explainer": True})   # ...then the person approved it
    stale["transcript"] = "new words"
    save_clip(d / "clip.json", stale)                # ...and the watcher wrote its older copy back
    now = read_json(d / "clip.json")
    assert now["status"] == "approved" and now["explainer"] is True and now["transcript"] == "new words"


def test_the_watcher_never_remakes_a_clip_a_person_decided_about(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from clipper import watch
    from clipper.config import load_config
    from clipper.work import write_json

    cfg = load_config()
    work = tmp_path / "twitch_1"
    moments = [{"peak_s": 1000.0 + 600 * i, "start_s": 980.0 + 600 * i, "end_s": 1010.0 + 600 * i, "score": 5.0, "activity": 9, "messages": 9, "hype": 0} for i in range(4)]
    states = [{"status": "approved"}, {"status": "candidate", "draft_done": True}, {"status": "candidate", "exported": "x.mp4"}, {"status": "candidate"}]
    for m, st in zip(moments, states):
        write_json(work / "clips" / watch.clip_id(m["peak_s"]) / "clip.json", st)   # none of them has a final.mp4
    monkeypatch.setattr(watch, "load_chat", lambda p: [])
    monkeypatch.setattr(watch, "done", lambda p: p.name == "chat.json")
    monkeypatch.setattr(watch, "pick_moments", lambda *a: [SimpleNamespace(to_dict=lambda m=m: dict(m)) for m in moments])
    monkeypatch.setattr(watch, "load_chat", lambda p: [SimpleNamespace(t=5000.0)])
    monkeypatch.setattr(watch, "bucket_chat", lambda *a, **k: [])
    monkeypatch.setattr(watch, "score_buckets", lambda *a: [])
    w = SimpleNamespace(work=work, watch_from=0.0, meta={"started_at": 0.0}, feed=None)
    got = watch.new_moments(w, cfg)
    assert [m["peak_s"] for m in got] == [2800.0]  # only the clip nobody has touched and no draft was finished for


def test_done_means_videos_made_and_not_edited_since(tmp_path):
    from clipper.review import videos_made

    d = _clip(tmp_path)
    state = {"status": "approved", "exported": "x.mp4"}
    assert not videos_made(d, state)                      # the draft video was deleted by an edit: make it again
    (d / "final.mp4").write_bytes(b"x")
    assert videos_made(d, state)
    assert not videos_made(d, {**state, "explainer": True})   # explainer ticked after the videos were made
    (d / "explained.mp4").write_bytes(b"x")
    assert videos_made(d, {**state, "explainer": True})
    assert not videos_made(d, {"status": "candidate", "exported": "x.mp4"})


def test_a_silent_clip_does_not_break_the_loudness_step(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from clipper import render

    silent = '{"input_i" : "-inf", "input_tp" : "-inf", "input_lra" : "0.00", "input_thresh" : "-inf", "target_offset" : "inf"}'
    normal = '{"input_i" : "-23.10", "input_tp" : "-4.20", "input_lra" : "6.00", "input_thresh" : "-33.50", "target_offset" : "0.30"}'
    monkeypatch.setattr(render, "run", lambda *a, **k: SimpleNamespace(stderr="noise\n" + silent))
    assert render._measure_loudness("ffmpeg", "source.mp4", 0, 30, -14, tmp_path) is None  # falls back to the one-pass form
    monkeypatch.setattr(render, "run", lambda *a, **k: SimpleNamespace(stderr=normal))
    assert render._measure_loudness("ffmpeg", "source.mp4", 0, 30, -14, tmp_path)["input_i"] == "-23.10"


def test_one_clip_that_cannot_be_drawn_does_not_stop_the_others(tmp_path, monkeypatch):
    from clipper import pipeline, render
    from clipper.config import load_config
    from clipper.tools import ToolError
    from clipper.work import read_json, write_json

    cfg = load_config()
    work = tmp_path / "twitch_1"
    for name in ("0h01m00s", "0h02m00s"):
        d = work / "clips" / name
        d.mkdir(parents=True)
        write_json(d / "clip.json", {"id": name, "status": "candidate", "start_s": 40, "end_s": 70})
        (d / "source.mp4").write_bytes(b"x")

    def fake_render(d, cfg, force=False, **k):
        if d.name == "0h01m00s":
            raise ToolError("render failed: example")
        return d / "final.mp4"

    monkeypatch.setattr(pipeline, "open_work", lambda url, cfg: (None, work))
    monkeypatch.setattr(render, "render_clip", fake_render)
    failed: list[str] = []
    made = pipeline.stage_render("x", cfg, failed=failed)
    assert [p.parent.name for p in made] == ["0h02m00s"] and failed == ["0h01m00s"]
    assert "example" in read_json(work / "clips" / "0h01m00s" / "clip.json")["error"]


def test_a_stream_watched_live_is_read_again_once_it_has_ended(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from clipper import pipeline
    from clipper.config import load_config
    from clipper.work import read_json, write_json

    cfg = load_config()
    work = tmp_path / "twitch_9"
    work.mkdir()
    write_json(work / "meta.json", {"is_live": True, "duration_s": 100})
    (work / "chat.json").write_text('{"t": 5.0, "author": "a", "text": "hi"}\n', encoding="utf-8")   # collected by the watcher
    asked = []

    def fake_meta(source, w, cfg):
        asked.append("meta" if not (w / "meta.json").exists() else "cached")
        return {"is_live": False, "duration_s": 9000, "channel": "Someone"}

    def fake_chat(source, w, cfg, refresh=False):
        asked.append("chat" if not (w / "chat.json").exists() else "kept")
        (w / "chat.json").write_text("[]", encoding="utf-8")
        return w / "chat.json"

    monkeypatch.setattr(pipeline, "open_work", lambda url, cfg: (SimpleNamespace(url="u", platform="twitch", key="twitch_9"), work))
    monkeypatch.setattr(pipeline, "fetch_meta", fake_meta)
    monkeypatch.setattr(pipeline, "fetch_chat", fake_chat)
    monkeypatch.setattr(pipeline, "load_chat", lambda p: [SimpleNamespace(t=10.0)])
    monkeypatch.setattr(pipeline, "find_moments", lambda *a, **k: [])
    pipeline.stage_find("u", cfg)
    assert asked == ["meta", "chat"]                       # both asked for afresh, not taken from the live session
    assert (work / "chat.watched.json").exists()           # the watcher's partial chat is kept aside, not lost
