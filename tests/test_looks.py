"""Caption movement and profiles, branding, and how they are checked when typed on the page."""

from dataclasses import replace

import pytest

from clipper.captions import Overlay, Word, build_ass
from clipper.config import load_config
from clipper.control import Control, clean_look, clean_options

WORDS = [Word(0.0, 0.4, "no"), Word(0.4, 0.8, "way"), Word(0.8, 1.2, "bro")]


def _events(animation, **more):
    s = replace(load_config().captions, animation=animation, max_words=3, uppercase=False, **more)
    return [line.split(",,", 1)[-1].split(",", 3)[-1] for line in build_ass(WORDS, s).splitlines() if line.startswith("Dialogue: 0")]


def test_each_caption_movement_writes_what_it_says():
    plain, pop, word, build = (_events(a) for a in ("highlight", "pop", "word", "build"))
    assert len(plain) == 3 and "\\t(" not in plain[0]  # the spoken word only changes colour and size
    assert "\\fscx70" in pop[0] and "\\t(0,70," in pop[0]  # ... or pops in
    assert len(word) == 3 and all(e.count(" ") == 0 for e in word)  # one word on screen at a time
    assert build[0].count("\\alpha&HFF&") == 2 and build[2].count("\\alpha&HFF&") == 0  # words not said yet are invisible, not missing
    assert "way" in build[0]  # ... so the line keeps its place


def test_the_outro_ribbon_has_a_place_at_the_bottom_and_at_the_top():
    s = load_config().captions
    script = build_ass(WORDS, s, [Overlay("Follow for part 2", 5.0, 9.0, "Outro"), Overlay("Follow for part 2", 5.0, 9.0, "OutroTop")])
    assert "Style: Outro," in script and "Style: OutroTop," in script and script.count("FOLLOW FOR PART 2") + script.count("Follow for part 2") == 2


def test_a_caption_look_typed_on_the_page_is_checked():
    good = {"font": "Impact", "font_size": 104, "outline": 7, "max_words": 2, "text_color": "#ffffff", "active_color": "3cff6b", "uppercase": True, "animation": "build"}
    assert clean_look(good) == {"font": "Impact", "font_size": 104, "outline": 7, "max_words": 2, "text_color": "FFFFFF", "active_color": "3CFF6B",
                                "uppercase": True, "animation": "build"}
    for bad, why in (({**good, "font": ""}, "font"), ({**good, "font": "Arial{\\b1}"}, "font"), ({**good, "font_size": 500}, "letter size"),
                     ({**good, "text_color": "red"}, "text colour"), ({**good, "max_words": "many"}, "words on screen")):
        with pytest.raises(ValueError, match=why):
            clean_look(bad)
    assert clean_look({**good, "animation": "spin"})["animation"] == "highlight"
    assert clean_options({"caption_look": {**good, "name": "Mine"}})["captions"]["preset"] == "Mine"


def test_branding_options_are_checked(tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    out = clean_options({"brand_logo": str(logo), "brand_logo_corner": "top-right", "brand_logo_size": 0.9, "brand_outro_text": "  Follow   me  ",
                         "brand_music_volume": "0.2", "brand_music": ""})["brand"]
    assert out == {"logo": str(logo), "logo_corner": "top-right", "logo_size": 0.5, "outro_text": "Follow me", "music_volume": 0.2, "music": ""}
    with pytest.raises(ValueError, match="not there"):
        clean_options({"brand_logo": str(tmp_path / "missing.png")})
    with pytest.raises(ValueError, match="corner"):
        clean_options({"brand_logo_corner": "middle"})


def test_a_caption_profile_is_saved_used_and_deleted(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_CONFIG", str(tmp_path / "config.yaml"))  # settings are saved beside this file, not in the project
    (tmp_path / "config.yaml").write_text(f"paths:\n  data_dir: {tmp_path.as_posix()}\n", encoding="utf-8")
    c = Control(load_config())
    look = {"font": "Impact", "font_size": 104, "outline": 7, "max_words": 2, "text_color": "FFFFFF", "active_color": "3CFF6B", "uppercase": True, "animation": "pop"}
    s = c.save({"caption_profile_save": {**look, "name": "Green pop"}})
    assert s["caption_preset"] == "Green pop" and s["caption_profiles"]["Green pop"]["font"] == "Impact" and s["caption_now"]["animation"] == "pop"
    assert (tmp_path / "config.local.yaml").exists()
    s = c.save({"caption_profile_delete": "Green pop", "caption_preset": "bold"})
    assert s["caption_profiles"] == {} and s["caption_preset"] == "bold"
    with pytest.raises(ValueError, match="name"):
        c.save({"caption_profile_save": look})


def test_a_new_look_makes_older_drafts_stale():
    from clipper.render import look_signature

    cfg = load_config()
    before = look_signature(cfg)
    cfg.captions.script = "native"
    assert look_signature(cfg) == before  # the letters are chosen when a clip is transcribed, not when it is drawn
    cfg.captions.animation = "build"
    after = look_signature(cfg)
    assert after != before
    cfg.brand.outro_text = "Follow"
    assert look_signature(cfg) != after
