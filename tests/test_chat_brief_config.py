import json

import pytest

from clipper.brief import Brief, ClipInfo, check_clip, load_brief, passes, source_allowed
from clipper.chat import ChatFormatError, parse_chat
from clipper.cli import main
from clipper.config import ConfigError, load_config

# ---- chat loaders ----

TWITCH = {
    "comments": [
        {"content_offset_seconds": 12.5, "commenter": {"display_name": "Bob"}, "message": {"body": "LOL"}},
        {"content_offset_seconds": 3, "commenter": {"name": "amy"}, "message": {"body": "hi"}},
        {"commenter": {"display_name": "broken"}, "message": {"body": "no offset"}},
    ]
}


def yt_line(offset_ms, author, text, ts_usec=None):
    renderer = {"message": {"runs": [{"text": text}, {"emoji": {"emojiId": "💀"}}]}, "authorName": {"simpleText": author}}
    if ts_usec is not None:
        renderer["timestampUsec"] = str(ts_usec)
    replay = {"actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": renderer}}}]}
    if offset_ms is not None:
        replay["videoOffsetTimeMsec"] = str(offset_ms)
    return json.dumps({"replayChatItemAction": replay})


def test_twitch_pretty_and_minified_sorted_by_time():
    for text in (json.dumps(TWITCH, indent=2), json.dumps(TWITCH)):
        msgs = parse_chat(text)
        assert [(m.t, m.author, m.text) for m in msgs] == [(3.0, "amy", "hi"), (12.5, "Bob", "LOL")]


def test_youtube_replay_offsets():
    msgs = parse_chat(yt_line(5000, "a", "wow ") + "\n" + yt_line(1500, "b", "hey "))
    assert [(m.t, m.author, m.text) for m in msgs] == [(1.5, "b", "hey 💀"), (5.0, "a", "wow 💀")]


def test_youtube_live_capture_falls_back_to_timestamps():
    msgs = parse_chat(yt_line(None, "a", "x", ts_usec=1_000_000_000) + "\n" + yt_line(None, "b", "y", ts_usec=1_004_500_000))
    assert [m.t for m in msgs] == [0.0, 4.5]


def test_native_jsonl_with_torn_last_line():
    text = '{"t": 2, "author": "a", "text": "one"}\n{"t": 1, "author": "b", "text": "two"}\n{"t": 3, "auth'
    assert [m.t for m in parse_chat(text)] == [1.0, 2.0]


def test_empty_and_unknown_formats():
    assert parse_chat("  \n") == []
    with pytest.raises(ChatFormatError):
        parse_chat("this is not json")
    with pytest.raises(ChatFormatError):
        parse_chat('{"something": "else"}')


# ---- campaign brief checker ----

BRIEF = Brief(
    name="Test campaign",
    allowed_sources=["twitch.tv/streamer", "https://www.youtube.com/watch?v=abcdefghijk"],
    min_duration_s=15,
    max_duration_s=60,
    required_tags=["#ad"],
    require_disclosure_overlay=True,
)


def good_clip(**kw):
    base = dict(
        source_url="https://youtu.be/abcdefghijk?t=120",
        duration_s=32,
        width=1080,
        height=1920,
        caption="He did NOT expect that #ad #gta6",
        has_disclosure_overlay=True,
    )
    base.update(kw)
    return ClipInfo(**base)


def fails(clip):
    return [i.message for i in check_clip(clip, BRIEF) if i.level == "fail"]


def test_good_clip_passes_with_manual_watermark_check():
    issues = check_clip(good_clip(), BRIEF)
    assert passes(issues)
    assert [i.level for i in issues] == ["manual"]


def test_source_matching():
    assert source_allowed("https://www.twitch.tv/Streamer", BRIEF.allowed_sources)
    # A Twitch VOD URL carries no channel name, so it cannot match a channel entry:
    # the pipeline must record the channel URL as the source, not the VOD URL.
    assert not source_allowed("https://www.twitch.tv/videos/123", BRIEF.allowed_sources)
    assert source_allowed("https://www.youtube.com/live/abcdefghijk", BRIEF.allowed_sources)
    assert not source_allowed("https://www.youtube.com/watch?v=ZZZZZZZZZZZ", BRIEF.allowed_sources)
    assert not source_allowed("https://kick.com/streamer", BRIEF.allowed_sources)


def test_each_rule_fails_on_its_own():
    assert "allowed sources" in fails(good_clip(source_url="https://youtu.be/ZZZZZZZZZZZ"))[0]
    assert "longer" in fails(good_clip(duration_s=75))[0]
    assert "shorter" in fails(good_clip(duration_s=8))[0]
    assert "not 9:16" in fails(good_clip(width=1920, height=1080))[0]
    assert "disclosure" in fails(good_clip(has_disclosure_overlay=False))[0]


def test_tag_must_be_a_whole_tag():
    assert "#ad" in fails(good_clip(caption="great clip #advert #adidas"))[0]
    assert fails(good_clip(caption="great clip #AD")) == []


def test_load_brief_rejects_unknown_keys(tmp_path):
    p = tmp_path / "b.yaml"
    p.write_text("name: x\nmax_lenght: 60\n", encoding="utf-8")
    with pytest.raises(ValueError, match="max_lenght"):
        load_brief(p)


# ---- config ----


def test_project_config_loads_and_matches_gist_defaults():
    cfg = load_config()
    m = cfg.moments
    assert (m.bucket_s, m.window_s, m.gap_s, m.top, m.before_s, m.after_s) == (10, 300, 90, 5, 20, 10)
    assert "NO WAY" in m.hype_words
    assert cfg.captions.play_res_y == 1920


def test_config_typo_and_bad_value_fail_loudly(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("moments:\n  bukcet_s: 5\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="bukcet_s"):
        load_config(p)
    p.write_text("moments:\n  mode: live\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mode"):
        load_config(p)


def test_local_override_and_data_dir_env(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("moments:\n  top: 5\n", encoding="utf-8")
    (tmp_path / "config.local.yaml").write_text("moments:\n  top: 9\npaths:\n  data_dir: " + json.dumps(str(tmp_path / "d")) + "\n", encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.moments.top == 9
    assert cfg.data_dir == tmp_path / "d"
    monkeypatch.setenv("CLIPPER_DATA_DIR", str(tmp_path / "env"))
    assert cfg.data_dir == tmp_path / "env"


# ---- CLI end to end ----


def test_cli_moments_writes_json(tmp_path, capsys):
    lines = []
    for start in range(0, 1800, 10):
        lines += [json.dumps({"t": start + k, "author": f"bg{start}_{k}", "text": "hi"}) for k in range(2)]
    lines += [json.dumps({"t": 600 + i * 0.1, "author": f"s{i}", "text": "NO WAY"}) for i in range(60)]
    chat = tmp_path / "chat.jsonl"
    chat.write_text("\n".join(lines), encoding="utf-8")
    out = tmp_path / "out" / "moments.json"
    assert main(["moments", str(chat), "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data[0]["start_s"] == 580 and data[0]["end_s"] == 610
    assert "#1  0:09:40 - 0:10:10" in capsys.readouterr().out


def test_cli_reports_missing_file_cleanly(tmp_path):
    assert main(["moments", str(tmp_path / "nope.json")]) == 2
