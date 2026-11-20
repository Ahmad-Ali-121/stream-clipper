"""Kick links, templates and the background card, the two extra caption movements, the moment mix and small options."""

import json
from dataclasses import replace

import pytest

from clipper.config import load_config

ID = "61802db4-bbbd-4c96-a0a2-30db8bb54598"


def test_kick_video_links_are_recognised_in_both_forms():
    from clipper.sources import SourceError, parse_source

    a = parse_source(f"https://kick.com/xqc/videos/{ID}")
    b = parse_source(f"https://kick.com/video/{ID.upper()}?t=10")
    assert a == b and a.platform == "kick" and a.key == f"kick_{ID}" and a.url == f"https://kick.com/video/{ID}"
    with pytest.raises(SourceError, match="Kick video link"):
        parse_source("https://kick.com/xqc")  # a channel is not a video


def test_kick_details_and_the_picture_size_are_read(monkeypatch):
    from clipper import kick

    reply = {"source": "https://stream.example/abc/", "livestream": {"session_title": "GTA", "duration": 48979000, "is_live": False,
                                                                     "channel": {"slug": "xqc", "user": {"username": "xQc"}}}}
    monkeypatch.setattr(kick, "_get", lambda url, timeout=30: json.dumps(reply).encode())
    d = kick.details(ID)
    assert d["channel"] == "xqc" and d["channel_name"] == "xQc" and d["duration_s"] == 48979.0
    assert d["playlist"] == "https://stream.example/abc/media/hls/master.m3u8"
    master = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n1080p60/playlist.m3u8\n"
              "#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720\n720p/playlist.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=284x160\n160p/playlist.m3u8\n")
    base = "https://stream.example/abc/media/hls/master.m3u8"
    assert kick.pick_variant(master, base, 1080).endswith("/hls/1080p60/playlist.m3u8")
    assert kick.pick_variant(master, base, 720).endswith("/720p/playlist.m3u8")
    assert kick.pick_variant(master, base, 100).endswith("/160p/playlist.m3u8")  # nothing that small: the smallest there is
    assert kick.pick_variant("#EXTM3U\n#EXTINF:2,\nseg0.ts\n", base, 720) == base  # already the stream itself


def test_a_kick_video_has_no_chat_and_says_what_to_do(tmp_path):
    from clipper.fetch import NoChatError, fetch_chat
    from clipper.sources import parse_source

    with pytest.raises(NoChatError, match="Kick"):
        fetch_chat(parse_source(f"https://kick.com/video/{ID}"), tmp_path, load_config())


def test_two_more_caption_movements():
    from clipper.captions import Word, build_ass

    words = [Word(0.0, 0.4, "no"), Word(0.4, 0.8, "way"), Word(0.8, 1.2, "bro")]

    def events(animation):
        s = replace(load_config().captions, animation=animation, max_words=3, uppercase=False)
        return [line.split(",,0,0,0,,", 1)[1] for line in build_ass(words, s).splitlines() if line.startswith("Dialogue: 0")]

    assert events("line") == ["no way bro"] * 3  # plain lines: nothing follows the voice
    pop = events("popline")
    assert pop[0].startswith("{\\fscx80") and not pop[1].startswith("{\\fscx80")  # the bounce only when the line arrives
    assert "\\r" not in pop[1] and pop[1].count("\\c&H") == 2  # the colour moves without undoing anything else


def test_the_moment_mix_leans_the_weights():
    from clipper.scoring import BASE_WEIGHTS, combined, mixed

    action, talk = mixed(None, "action"), mixed(None, "dialogue")
    assert mixed(None, "balanced") == BASE_WEIGHTS and mixed(None, "nonsense") == BASE_WEIGHTS
    assert action["video"] > BASE_WEIGHTS["video"] > talk["video"] and talk["ai_pick"] > BASE_WEIGHTS["ai_pick"] > action["ai_pick"]
    loud = {"trigger": "audio+video", "score": 8.0, "signals": {"audio": 8.0, "video": 9.0}}
    assert combined(loud, action) > combined(loud, BASE_WEIGHTS) > combined(loud, talk)
    assert max(action.values()) <= 0.95


def test_templates_frame_the_picture(tmp_path):
    from clipper import frames

    r = load_config().render
    plain = frames.build(replace(r, template="none"), tmp_path, "Hook", "lacy", 30.0, "Arial", "FFE600", 1)
    assert plain.graph == "[vz]null[t0]" and not plain.inputs and not plain.shows_hook
    news = frames.build(replace(r, template="news"), tmp_path, "He did it", "lacy", 30.0, "Arial", "FFE600", 1)
    assert news.shows_hook and any("BREAKING NEWS" in e for e in news.events) and any("HE DID IT" in e for e in news.events) and news.caption_margin > 400
    post = frames.build(replace(r, template="xpost_dark", post_name="Ahmad Clips", post_handle="ahmadclips"), tmp_path, "He did it", "lacy", 30.0, "Arial", "FFE600", 1)
    assert post.inputs == ["-loop", "1", "-i", "frame_mask.png"] and (tmp_path / "frame_mask.png").exists()  # rounded corners
    assert "alphamerge" in post.graph and post.graph.endswith("[t0]") and "color=c=0x000000" in post.graph
    assert any("Ahmad Clips" in e for e in post.events) and any("@ahmadclips" in e for e in post.events) and post.shows_hook
    card = frames.build(replace(r, template="card", card_bg="gradient", card_color="101020", card_color2="FF3D8B"), tmp_path, "Hook", "lacy", 30.0, "Arial", "FFE600", 1)
    assert "gradients=" in card.graph and not card.shows_hook and card.hook_margin  # the card keeps the usual pop-in opening text
    gone = frames.build(replace(r, template="card", card_bg="image", card_image=str(tmp_path / "missing.png")), tmp_path, "", "", 30.0, "Arial", "FFE600", 1)
    assert "boxblur" in gone.graph  # a removed image falls back to the blurred copy instead of failing the clip


def test_the_new_options_are_checked(tmp_path):
    from clipper.control import clean_options

    assert clean_options({"template": "news", "card_bg": "color", "card_color": "#aabbcc", "post_handle": "  @me  "})["render"] == {
        "template": "news", "card_bg": "color", "card_color": "AABBCC", "post_handle": "@me"}
    assert clean_options({"hook_seconds": "8", "moment_mix": "action"}) == {"render": {"hook_seconds": 8.0}, "moments": {"mix": "action"}}
    assert clean_options({"brand_music_rise_s": 6, "brand_music_peak": 2})["brand"] == {"music_rise_s": 6.0, "music_peak": 0.8}
    for bad, why in (({"template": "x"}, "template"), ({"card_color": "red"}, "colour"), ({"hook_seconds": 90}, "2 to 60"), ({"moment_mix": "wild"}, "moment mix"),
                     ({"card_image": str(tmp_path / "no.png")}, "not there")):
        with pytest.raises(ValueError, match=why):
            clean_options(bad)


def test_the_look_changes_with_the_template_and_opening_text_time():
    from clipper.render import look_signature

    cfg = load_config()
    a = look_signature(cfg)
    cfg.render.template = "card"
    b = look_signature(cfg)
    cfg.render.hook_seconds = 9
    assert len({a, b, look_signature(cfg)}) == 3  # Make videos remakes drafts made with another template or opening time
