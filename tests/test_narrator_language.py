"""Ahmad, 2026-11-27: the narrator can speak Hindi or Urdu with the free voice model.

Tried for real that day: the program spoke a Hindi and an Urdu line, the speech model listened and wrote back the same words,
and an explainer video with a Hindi narrator showed its captions in Hindi letters. Nobody has yet judged how natural it sounds.
"""

import sys
import types
from dataclasses import replace
from pathlib import Path

import pytest

import clipper
from clipper import control, llm, tts
from clipper.config import load_config
from clipper.tools import ToolError

PAGE = clipper.page_source()
HINDI = "दोस्तों, इस क्लिप में देखिए क्या हुआ। उसने आख़िरी सेकंड में पूरा मैच पलट दिया।"
URDU = "دوستو، اس کلپ میں دیکھیں کیا ہوا۔"
ENGLISH = "Watch what happens in this clip."


def _cfg(tmp_path, language="English", **voice):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    return replace(cfg, voice=replace(cfg.voice, backend="kokoro", language=language, **voice))


def test_the_letters_of_a_line_are_told_apart():
    assert tts.script_of(HINDI) == "devanagari" and tts.script_of(URDU) == "arabic" and tts.script_of(ENGLISH) == "latin"
    assert tts.script_of("GTA 6 में डकी भाई ने 3 गाड़ियाँ उड़ा दीं") == "devanagari"      # a game's name inside a Hindi line stays a Hindi line
    assert tts.script_of("123 !?") is None


def test_a_line_in_the_wrong_letters_is_refused_with_what_to_do(tmp_path):
    for language, line, wrote in (("Hindi", ENGLISH, "English letters"), ("English", HINDI, "Hindi letters"), ("Urdu", HINDI, "Hindi letters"), ("Hindi", URDU, "Urdu letters")):
        cfg = _cfg(tmp_path, language)
        assert tts.fits_narrator(line, cfg) is False
        with pytest.raises(ToolError) as e:
            tts.check_line(line, cfg)
        said = str(e.value)
        assert f"set to speak {language}" in said and f"written in {wrote}" in said and "Versions, What the narrator says" in said and "Settings page under Narrator voice" in said
    assert tts.check_line(HINDI, _cfg(tmp_path, "Hindi")) == "Hindi" and tts.check_line(URDU, _cfg(tmp_path, "Urdu")) == "Urdu"
    assert tts.check_line("123", _cfg(tmp_path, "Hindi")) == "Hindi"                 # nothing to object to
    with pytest.raises(ToolError, match="must be English, Hindi or Urdu"):
        tts.narrator_language(_cfg(tmp_path, "Klingon"))


class _FakeVoice:
    """Stands in for the voice model: remembers what it was asked to say."""

    def __init__(self):
        self.calls = []
        self.tokenizer = types.SimpleNamespace(vocab=set("abcdefghijklmnopqrstuvwxyz .,?"))

    def create(self, text, **how):
        self.calls.append((text, how))
        return [0.0] * 2400, 24000


def _ready(tmp_path, monkeypatch):
    folder = tmp_path / "models" / "kokoro"
    folder.mkdir(parents=True)
    for name in ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"):
        (folder / name).write_bytes(b"x")
    fake = _FakeVoice()
    monkeypatch.setattr(tts, "_kokoro", fake)
    monkeypatch.setitem(sys.modules, "kokoro_onnx", types.SimpleNamespace(Kokoro=lambda *a: fake))
    monkeypatch.setitem(sys.modules, "soundfile", types.SimpleNamespace(write=lambda path, samples, rate: Path(path).write_bytes(b"RIFF" + bytes(len(samples)))))
    asked = []

    def phonemize(text, code, **how):
        asked.append((text, code, how))
        return "(en) dosto (hi) is klip"
    monkeypatch.setitem(sys.modules, "phonemizer", types.SimpleNamespace(phonemize=phonemize))
    return fake, asked


def test_hindi_and_urdu_are_spoken_by_the_hindi_voices_and_english_as_before(tmp_path, monkeypatch):
    fake, asked = _ready(tmp_path, monkeypatch)
    out = tts.speak(HINDI, tmp_path / "say" / "line", _cfg(tmp_path, "Hindi", kokoro_voice_hindi="hm_psi"))
    assert out.suffix == ".wav" and out.exists()
    text, code, how = asked[-1]
    assert code == "hi" and how["language_switch"] == "remove-flags"                 # no "(en)" marks left to be spoken as junk
    assert "।" not in text and "." in text and "़" not in text and "आखिरी" in text      # danda to full stop, nukta taken off
    sounds, call = fake.calls[-1]
    assert call == {"voice": "hm_psi", "speed": pytest.approx(1.05), "is_phonemes": True} and "(" not in sounds
    tts.speak(URDU, tmp_path / "say" / "line", _cfg(tmp_path, "Urdu"))
    text, code, how = asked[-1]
    assert code == "ur" and "۔" not in text and "،" not in text and fake.calls[-1][1]["voice"] == "hf_alpha"
    tts.speak(ENGLISH, tmp_path / "say" / "line", _cfg(tmp_path, "English", kokoro_voice="bf_emma"))
    assert fake.calls[-1] == (ENGLISH, {"voice": "bf_emma", "speed": pytest.approx(1.05), "lang": "en-gb"}) and len(asked) == 2   # English goes the old way
    with pytest.raises(ToolError, match="kokoro_voice_hindi must be one of"):
        tts.speak(HINDI, tmp_path / "say" / "line", _cfg(tmp_path, "Hindi", kokoro_voice_hindi="af_heart"))
    before = len(fake.calls)
    with pytest.raises(ToolError, match="reads Hindi letters"):
        tts.speak(ENGLISH, tmp_path / "say" / "line", _cfg(tmp_path, "Hindi"))
    assert len(fake.calls) == before                                                   # refused before a sound was made


def test_an_english_only_elevenlabs_model_cannot_be_the_hindi_narrator(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_ENV", str(tmp_path / "none.env"))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_made_up_0123456789")
    sent = []
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(post=lambda *a, **k: sent.append(1)))
    cfg = replace(_cfg(tmp_path, "Hindi"), voice=replace(_cfg(tmp_path, "Hindi").voice, backend="elevenlabs", elevenlabs_model="eleven_turbo_v2"))
    with pytest.raises(ToolError, match="speaks English only"):
        tts.speak(HINDI, tmp_path / "line", cfg)
    assert sent == []                                                                  # nothing was sent, so nothing was paid for


def test_the_settings_accept_the_language_and_keep_each_languages_voice(tmp_path):
    assert control.clean_options({"voice_language": "Hindi"}) == {"voice": {"language": "Hindi"}}
    with pytest.raises(ValueError, match="English, Hindi or Urdu"):
        control.clean_options({"voice_language": "French"})
    assert control.clean_options({"voice_kokoro": "hm_omega"}) == {"voice": {"kokoro_voice_hindi": "hm_omega"}}
    assert control.clean_options({"voice_kokoro": "af_heart"}) == {"voice": {"kokoro_voice": "af_heart"}}
    with pytest.raises(ValueError, match="not one of the voices"):
        control.clean_options({"voice_kokoro": "zz_nobody"})
    c = control.Control(_cfg(tmp_path, "Urdu", kokoro_voice_hindi="hf_beta", kokoro_voice="am_puck"))
    seen = c.voice_status()
    assert seen["language"] == "Urdu" and seen["kokoro_voice"] == "hf_beta" and [v[0] for v in seen["kokoro_voices"]] == ["hf_alpha", "hf_beta", "hm_psi", "hm_omega"]
    c = control.Control(_cfg(tmp_path, "English", kokoro_voice_hindi="hf_beta", kokoro_voice="am_puck"))
    seen = c.voice_status()
    assert seen["kokoro_voice"] == "am_puck" and len(seen["kokoro_voices"]) == 12 and seen["languages"] == ["English", "Hindi", "Urdu"]


def test_the_ai_is_told_to_write_the_narration_in_the_narrators_letters(tmp_path):
    hindi = llm.language_note(_cfg(tmp_path, "Hindi"))
    assert "intro, intro_b and rank_line in Hindi, in Devanagari letters only" in hindi and "Not one Latin letter" in hindi
    urdu = llm.language_note(_cfg(tmp_path, "Urdu"))
    assert "in Urdu, in Urdu (Arabic) script only" in urdu
    cfg = _cfg(tmp_path, "English")
    assert "Devanagari" not in llm.language_note(cfg) and "Arabic" not in llm.language_note(cfg)
    cfg.llm.text_language = "Roman Urdu"
    assert "the narrator's voice is English" in llm.language_note(cfg)                 # as before, when only the titles are in another language
    cfg = _cfg(tmp_path, "Hindi")
    cfg.llm.text_language = "Roman Urdu"
    both = llm.language_note(cfg)
    assert "hook, hook_b, title, title_b and description in" in both and "Devanagari letters only" in both and "voice is English" not in both


def test_without_ai_the_narrator_still_gets_a_line_it_can_read(tmp_path):
    state = {"channel": "Lacy", "transcript": "one two three four five six seven"}
    assert llm.fallback_copy(state, [])["intro"] == "Here's Lacy."
    for language in ("Hindi", "Urdu"):
        line = llm.fallback_copy(state, [], narrator=language)["intro"]
        assert tts.fits_narrator(line, _cfg(tmp_path, language)), line


def test_the_page_lets_the_narrators_language_be_chosen():
    for piece in ('<select id="v-lang" onchange="voiceLang()">', '<option value="Hindi">Hindi (people who speak Urdu understand it too)</option>', '<option value="Urdu">Urdu</option>',
                  "act('/api/options',{voice_language:val('v-lang')})", "VOICE.lang!==v.language", "A line in English letters is refused with a message, never spoken wrongly.",
                  "it was checked by a speech model, not yet by a person"):
        assert piece in PAGE, piece
