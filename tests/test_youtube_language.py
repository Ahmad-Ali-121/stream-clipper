import pytest

from clipper import llm
from clipper.captions import script_font
from clipper.config import load_config
from clipper.render import credit_text
from clipper.watch import channel_name
from clipper.youtube import channel_spec, chat_line, is_youtube


def test_what_counts_as_a_youtube_channel():
    assert is_youtube("@liveinsaan") and is_youtube("https://www.youtube.com/@liveinsaan/live") and is_youtube("https://youtu.be/QjbepgVChIA")
    assert not is_youtube("lacy") and not is_youtube("https://www.twitch.tv/lacy")
    assert channel_spec("@liveinsaan") == ("@liveinsaan", "https://www.youtube.com/@liveinsaan/live")
    assert channel_spec("https://www.youtube.com/@Live.Insaan-2/streams")[0] == "@Live.Insaan-2"
    assert channel_spec("https://www.youtube.com/watch?v=QjbepgVChIA&t=5") == ("QjbepgVChIA", "https://www.youtube.com/watch?v=QjbepgVChIA")
    with pytest.raises(ValueError, match="handle"):
        channel_spec("https://www.youtube.com/")


def test_channel_names_for_both_sites():
    assert channel_name("https://www.twitch.tv/Lacy") == "lacy"
    assert channel_name("@LiveInsaan") == "@LiveInsaan"
    assert channel_name("https://www.youtube.com/watch?v=QjbepgVChIA") == "https://www.youtube.com/watch?v=QjbepgVChIA"
    with pytest.raises(ValueError, match="Twitch name"):
        channel_name("not a channel!")


def test_live_chat_lines_are_read_and_everything_else_skipped():
    line = {"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
        "timestampUsec": "1795093000500000", "authorName": {"simpleText": "@viewer"},
        "message": {"runs": [{"text": "Bhai "}, {"emoji": {"shortcuts": [":fire:"]}}, {"text": " OP"}]}}}}}]}}
    at, author, text = chat_line(line)
    assert at == pytest.approx(1795093000.5) and author == "@viewer" and text.startswith("Bhai") and text.endswith("OP")
    assert chat_line({"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {"liveChatViewerEngagementMessageRenderer": {}}}}]}}) is None
    assert chat_line({}) is None


def test_urdu_and_hindi_letters_pick_a_font_that_has_them():
    assert script_font(["hello", "bhai"]) is None
    assert script_font(["भाई", "ने"]) == "Nirmala UI"
    assert script_font(["hello", "بھائی"]) == "Segoe UI"


def test_captions_use_that_font_and_are_not_upper_cased():
    from clipper.captions import Word, build_ass

    cfg = load_config()
    script = build_ass([Word(0.0, 0.4, "भाई"), Word(0.5, 0.8, "ने")], cfg.captions)
    assert "Style: Word,Nirmala UI," in script and "भाई" in script
    plain = build_ass([Word(0.0, 0.4, "bhai")], cfg.captions)
    assert f"Style: Word,{cfg.captions.font}," in plain and "BHAI" in plain


def test_roman_captions_keep_one_word_per_word(monkeypatch):
    from clipper import transcribe

    cfg = load_config()
    cfg.captions.script = "roman"
    words = [{"word": "भाई", "start": 0.0, "end": 0.4}, {"word": "गेम", "start": 0.5, "end": 0.8}]
    monkeypatch.setattr(llm, "ask", lambda *a, **k: {"words": ["bhai", "game"]})
    out = transcribe.caption_script([dict(w) for w in words], cfg)
    assert [w["word"] for w in out] == ["bhai", "game"] and out[0]["native"] == "भाई" and out[1]["start"] == 0.5
    # A wrong count would shift every caption off its word: refuse it and use the fixed rules, still one word per word.
    monkeypatch.setattr(llm, "ask", lambda *a, **k: {"words": ["bhai"]})
    assert [w["word"] for w in transcribe.caption_script([dict(w) for w in words], cfg)] == ["bhai", "gem"]
    # English speech, or captions set to the script's own letters: nothing is asked.
    monkeypatch.setattr(llm, "ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))
    assert transcribe.caption_script([{"word": "hello", "start": 0, "end": 1}], cfg)[0]["word"] == "hello"
    cfg.captions.script = "native"
    assert transcribe.caption_script([dict(w) for w in words], cfg)[0]["word"] == "भाई"


def test_the_prompt_says_which_language_to_write_in():
    cfg = load_config()
    cfg.transcribe.language, cfg.llm.text_language = "en", "English"
    assert llm.language_note(cfg) == ""
    cfg.transcribe.language, cfg.llm.text_language = "ur", "Roman Urdu"
    note = llm.language_note(cfg)
    assert "speaks Urdu" in note and "Roman Urdu" in note and "narrator's voice is English" in note


def test_credit_line_for_youtube_uses_the_handle():
    assert credit_text({"platform": "youtube", "channel": "Live Insaan", "channel_handle": "@liveinsaan"}) == "Clip: youtube.com/@liveinsaan"
    assert credit_text({"platform": "youtube", "channel": "Live Insaan"}) == "Clip: Live Insaan on YouTube"
    assert credit_text({"platform": "twitch", "channel": "Lacy"}) == "Clip: twitch.tv/Lacy"


def test_fixed_rules_give_readable_roman_letters_without_claude():
    from clipper.transcribe import roman_fallback

    heard = "भाई क्या नहीं यार कमाल ज़िंदगी फ़ोन गया है".split()
    assert [roman_fallback(w) for w in heard] == ["bhai", "kya", "nahin", "yar", "kamal", "zindagi", "fon", "gaya", "hai"]
    assert roman_fallback("hello") == "hello" and roman_fallback("२०") == "20"


def test_urdu_wanted_in_roman_letters_is_heard_as_hindi():
    from clipper.transcribe import speech_language

    cfg = load_config()
    cfg.transcribe.language, cfg.captions.script = "ur", "roman"
    assert speech_language(cfg) == "hi"       # Hindi letters carry the vowels that Latin letters need
    cfg.captions.script = "native"
    assert speech_language(cfg) == "ur"       # asked for Urdu letters: hear it as Urdu
    cfg.transcribe.language = "auto"
    assert speech_language(cfg) is None
    cfg.transcribe.language = "en"
    assert speech_language(cfg) == "en"
