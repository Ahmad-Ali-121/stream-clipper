"""Ahmad, 2026-11-27: select several clips and act on them together; captions higher, lower, left or right;
20 caption fonts that come with the program; the narrator's voice chosen on the page, from this computer or ElevenLabs."""

import dataclasses
import json
from pathlib import Path

import pytest

import clipper
from clipper import control, fonts, tts
from clipper.captions import build_ass
from clipper.config import load_config

PAGE = (Path(clipper.__file__).parent / "review_page.html").read_text(encoding="utf-8")
SERVER = (Path(clipper.__file__).parent / "review.py").read_text(encoding="utf-8")


# ---------------- fonts ----------------

def test_twenty_fonts_come_with_the_program_each_with_its_licence():
    rows = fonts.bundled()
    assert len(rows) == 20 and len({r["family"] for r in rows}) == 20
    for r in rows:
        f = fonts.FONT_DIR / r["file"]
        assert f.is_file() and f.stat().st_size > 10_000, r["file"]
        assert (fonts.FONT_DIR / (f.stem + ".LICENSE.txt")).is_file(), "no licence text beside " + r["file"]
        assert r["license"] in ("OFL-1.1", "Apache-2.0") and r["label"] and isinstance(r["weight"], int)
        assert "[" not in r["file"]                                   # no variable fonts: the subtitle engine picks their weight badly


def test_a_caption_style_can_name_a_bundled_font_and_gets_its_own_weight():
    assert fonts.find("poppins extrabold")["file"] == "Poppins-ExtraBold.ttf" and fonts.find("No Such Font") is None
    assert fonts.ass_name("bangers") == "Bangers" and fonts.ass_name("Arial Black") == "Arial Black"
    # a face with one weight only is not thickened artificially; a heavy weight and a font from Windows stay bold
    assert fonts.bold_flag("Bangers") == 0 and fonts.bold_flag("Poppins ExtraBold") == -1 and fonts.bold_flag("Arial Black") == -1
    c = load_config().captions
    word = lambda font: next(line for line in build_ass([], dataclasses.replace(c, font=font)).splitlines() if line.startswith("Style: Word,"))  # noqa: E731
    assert word("bebas neue").startswith("Style: Word,Bebas Neue,") and ",0,0,0,0,100,100,0,0," in word("bebas neue")
    assert ",-1,0,0,0,100,100,0,0," in word("Arial Black")


def test_the_renderer_is_shown_the_fonts_folder_wherever_subtitles_are_burned_in(monkeypatch):
    got = fonts.ass_filter("final.ass")
    assert got.startswith("ass=final.ass:fontsdir=") and "\\\\:" in got and "\\" not in got.split("fontsdir=")[1].replace("\\\\:", "")
    src = Path(clipper.__file__).parent
    assert "ass_filter(stem + '.ass')" in (src / "render.py").read_text(encoding="utf-8")
    assert (src / "compose.py").read_text(encoding="utf-8").count("{_ass(ass_name)}") == 2
    assert 'ass_filter("cover.ass")' in (src / "thumbs.py").read_text(encoding="utf-8")
    monkeypatch.setattr(fonts, "bundled", lambda: ())
    assert fonts.ass_filter("final.ass") == "ass=final.ass"          # a copy without the folder still renders, with Windows' fonts
    for piece in ('re.fullmatch(r"/fonts/([A-Za-z0-9_.\\-]+\\.ttf)"', 'not in {f["file"] for f in fonts.bundled()}'):
        assert piece in SERVER, piece                                 # only the listed files are handed out
    for piece in ('id="k-pick"', "function pickFont(){", "function fontLoad(f){", "new FontFace(f.family,'url(/fonts/'+encodeURIComponent(f.file)+')')"):
        assert piece in PAGE, piece


# ---------------- where the captions sit ----------------

def test_captions_can_be_moved_up_down_left_and_right():
    c = load_config().captions
    assert c.offset_x == 0
    word = lambda **k: next(line for line in build_ass([], dataclasses.replace(c, **k)).splitlines() if line.startswith("Style: Word,")).split(",")  # noqa: E731
    assert word()[-4:-1] == ["60", "60", str(c.margin_v)]
    assert word(offset_x=100, margin_v=900)[-4:-1] == ["260", "60", "900"]        # 100 px to the right: the left margin grows by 200
    assert word(offset_x=-150)[-4:-1][:2] == ["60", "360"]
    assert word(offset_x=5000)[-4:-1][:2] == ["660", "60"]                        # never further than 300 px
    assert control.clean_options({"caption_position": {"margin_v": 3000, "offset_x": -999}}) == {"captions": {"margin_v": 1500, "offset_x": -300}}
    assert control.clean_options({"caption_position": {"margin_v": 10, "offset_x": 40.4}}) == {"captions": {"margin_v": 80, "offset_x": 40}}
    with pytest.raises(ValueError, match="two numbers"):
        control.clean_options({"caption_position": {"margin_v": "high"}})
    for piece in ('id="p-up" min="80" max="1500"', 'id="p-side" min="-300" max="300"', "function savePos(){", "caption_position:{margin_v:+val('p-up'),offset_x:+val('p-side')}",
                  "Back to the usual place"):
        assert piece in PAGE, piece


# ---------------- the narrator ----------------

def test_the_narrator_voice_is_chosen_from_the_best_voices_and_british_ones_read_british(tmp_path, monkeypatch):
    ids = [v for v, _ in tts.KOKORO_VOICES]
    assert ids[:2] == ["af_heart", "af_bella"] and len(ids) == 12 and "am_michael" in ids and "bm_george" in ids
    assert control.clean_options({"voice_kokoro": "bm_george", "voice_speed": 9}) == {"voice": {"kokoro_voice": "bm_george", "speed": 1.4}}
    with pytest.raises(ValueError, match="not one of the voices"):
        control.clean_options({"voice_kokoro": "zz_nobody"})
    said = []

    class Fake:
        def create(self, text, voice, speed, lang):
            said.append((voice, lang))
            return [0.0] * 100, 24000

    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    folder = tmp_path / "models" / "kokoro"
    folder.mkdir(parents=True)
    for n in ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"):
        (folder / n).write_bytes(b"x")
    monkeypatch.setattr(tts, "_kokoro", Fake())
    monkeypatch.setitem(__import__("sys").modules, "kokoro_onnx", type("M", (), {"Kokoro": Fake}))
    monkeypatch.setitem(__import__("sys").modules, "soundfile", type("S", (), {"write": staticmethod(lambda path, samples, rate: Path(path).write_bytes(b"wav"))}))
    for voice in ("bm_george", "af_heart"):
        cfg.voice.backend, cfg.voice.kokoro_voice = "kokoro", voice
        tts.speak("hello", tmp_path / "a", cfg)
    assert said == [("bm_george", "en-gb"), ("af_heart", "en-us")]


class _Answer:
    def __init__(self, code, data):
        self.status_code, self._data, self.text = code, data, json.dumps(data)

    def json(self):
        return self._data


def _eleven(monkeypatch, answers, key="sk_test"):
    import requests

    monkeypatch.setattr(tts, "elevenlabs_key", lambda: key)
    asked = []

    def get(url, headers=None, timeout=0):
        path = url.replace(tts.ELEVEN_API, "")
        asked.append((path, (headers or {}).get("xi-api-key")))
        return answers[path]

    monkeypatch.setattr(requests, "get", get)
    return asked


LIMITED = _Answer(401, {"detail": {"status": "missing_permissions", "message": "The API key you used is missing the permission models_read"}})
VOICES = _Answer(200, {"voices": [{"voice_id": "own1", "name": "My clone", "category": "cloned", "labels": {}},
                                  {"voice_id": "JBF", "name": "George", "category": "premade", "labels": {"gender": "male", "accent": "british", "use_case": "narrative_story"}},
                                  {"voice_id": "ADA", "name": "Adam", "category": "premade", "labels": {"gender": "male"}}]})
PLAN = _Answer(200, {"character_count": 35, "character_limit": 30000})


def test_checking_elevenlabs_says_what_is_true_about_the_key(monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(tts, "elevenlabs_key", lambda: "")
    none = tts.elevenlabs_check(cfg)
    assert none["ok"] is False and "ELEVENLABS_API_KEY" in none["why"]

    bad = _Answer(401, {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}})
    _eleven(monkeypatch, {"/v1/models": bad, "/v1/voices": bad, "/v1/user/subscription": bad})
    refused = tts.elevenlabs_check(cfg)
    assert refused["ok"] is False and "refused the key" in refused["why"]

    # A key that may make speech and list voices, but not list the models (found on 2026-11-27 with Ahmad's own key):
    # it is a working key, not a refused one.
    asked = _eleven(monkeypatch, {"/v1/models": LIMITED, "/v1/voices": VOICES, "/v1/user/subscription": PLAN})
    got = tts.elevenlabs_check(cfg)
    assert got["ok"] is True and got["models_listed"] is False and got["best_model"] == "eleven_multilingual_v2" and "may not list" in got["note"]
    assert [v["name"] for v in got["voices"]] == ["Adam", "George", "My clone"]          # ready-made voices first, then by name
    assert got["voices"][1]["about"] == "male, british, narrative_story" and got["left"] == 29965
    assert all(k == "sk_test" for _, k in asked) and all(p.startswith("/v1/") for p, _ in asked)   # the key goes to ElevenLabs only, nothing is spoken

    listed = _Answer(200, [{"model_id": "eleven_flash_v2_5", "name": "Flash", "can_do_text_to_speech": True}, {"model_id": "eleven_multilingual_v2", "name": "Multilingual v2", "can_do_text_to_speech": True},
                           {"model_id": "eleven_english_sts_v2", "name": "Speech to speech", "can_do_text_to_speech": False}])
    _eleven(monkeypatch, {"/v1/models": listed, "/v1/voices": VOICES, "/v1/user/subscription": PLAN})
    full = tts.elevenlabs_check(cfg)
    assert [m["id"] for m in full["models"]] == ["eleven_multilingual_v2", "eleven_flash_v2_5"] and full["best_model"] == "eleven_multilingual_v2" and full["note"] == ""


def test_choosing_elevenlabs_takes_its_best_model_and_is_refused_without_a_working_key(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    c = control.Control(cfg)
    saved = []
    monkeypatch.setattr(control, "save_local", lambda values, folder=None: saved.append(values))
    monkeypatch.setattr(c, "reload", lambda: cfg)
    monkeypatch.setattr(c, "status", lambda: {"voice": c.voice_status()})
    monkeypatch.setattr(tts, "elevenlabs_key", lambda: "sk_test")
    monkeypatch.setattr(tts, "elevenlabs_check", lambda cfg_: {"ok": True, "models": [{"id": "eleven_v3", "name": "v3"}, {"id": "eleven_turbo_v2_5", "name": "Turbo"}],
                                                              "best_model": "eleven_v3", "voices": [{"id": cfg.voice.elevenlabs_voice_id, "name": "George"}], "left": 100})
    c.check_voice({})                                                    # only asking changes nothing
    assert saved == [] and c.voice_check["ok"] is True
    c.check_voice({"use": True})
    assert saved == [{"voice": {"backend": "elevenlabs", "elevenlabs_model": "eleven_v3"}}]
    monkeypatch.setattr(tts, "elevenlabs_check", lambda cfg_: {"ok": False, "why": "ElevenLabs refused the key in .env"})
    with pytest.raises(ValueError, match="cannot be used yet"):
        c.check_voice({"use": True})
    monkeypatch.setattr(tts, "elevenlabs_key", lambda: "")
    with pytest.raises(ValueError, match="no key is set"):
        control.clean_options({"voice_backend": "elevenlabs"})
    for piece in ('id="v-back"', 'id="v-kokoro"', 'id="v-emodel"', 'id="v-evoice"', 'id="b-vcheck"', "function voiceDraw(s){", "act('/api/voice/check',{use:!!use})",
                  "fetch('/api/voice/sample'", "It uses about 60 characters of your ElevenLabs plan."):
        assert piece in PAGE, piece
    assert '"/api/voice/check": "check_voice"' in SERVER and 'self.path == "/api/voice/sample"' in SERVER
    assert "ELEVENLABS_API_KEY" not in PAGE.replace("ELEVENLABS_API_KEY=your-key", "")   # the page never holds or asks for the key itself


# ---------------- several clips at once ----------------

def test_several_clips_can_be_ticked_and_handled_together():
    for piece in ('id="b-select"', 'id="bulkbar"', 'id="bulk-n"', 'id="bulk-more"', 'id="bulk-stop"', 'id="bulk-note"', "const SEL=new Set(), BULK={busy:false,stop:false};",
                  "function selMode(on){", "function selAll(){", "async function bulk(what){", 'class="pk"', "(SEL.has(k)?' checked':'')",
                  "#clips.selecting .clip:not(.open):has(.pk input:checked)", "if(A.leaves)SEL.delete(keys[i])"):
        assert piece in PAGE, piece
    bar = PAGE[PAGE.index('id="bulkbar"'):PAGE.index('<div class="clips" id="clips"')]
    # approving, rejecting and deleting stay the person's own presses: the help assistant may not press them
    for name in ("approve", "reject", "review"):
        assert 'data-yours onclick="bulk(\'' + name + '\')"' in bar, name
    assert 'data-yours onclick="bulkMore()"' in bar
    for value in ("pending", "whole", "auto", "b_on", "b_off", "x_on", "x_off", "del"):
        assert 'option value="' + value + '"' in bar, value
    js = PAGE[PAGE.index("async function bulk(what){"):PAGE.index("/* The program opens where you were last")]
    assert "what==='del'&&!confirm(" in js and "A.slow&&!confirm(" in js          # deleting, and anything that takes minutes, is asked first
    assert "send('/api/clip/'+keys[i]+(A.url||''),A.body)" in js                  # the same requests as one clip at a time: every rule of a single clip holds
    assert "if(BULK.stop)break;" in js and "bad.push(r.error)" in js              # it can be stopped, and what failed is said with its reason
