import pytest

from clipper import hw, llm
from clipper.compose import align_words, card_filter, card_graphics, spread_words
from clipper.config import Config
from clipper.trends import clean_seed, rank_hashtags
from clipper.work import read_json, write_json

# ---- trending hashtags ----


def test_rank_hashtags_prefers_tags_shared_by_many_well_viewed_videos():
    videos = [
        {"title": "wild chase #gta6 #gta #shorts", "views": 5_000_000},
        {"title": "#GTA6 #gta #LuciaCaminos", "views": 135_000_000},
        {"title": "first mission #gta6 #gta", "views": 900_000},
        {"title": "one huge video #gta6 #randomtag", "views": 900_000_000},
        {"title": "no tags at all", "views": 10},
    ]
    ranked = rank_hashtags(videos, seed="gta6")
    tags = [r["tag"] for r in ranked]
    assert tags[0] == "#gta"  # on three videos: beats a tag on one enormous video
    assert "#gta6" not in tags  # the seed itself is not a discovery
    assert "#shorts" not in tags  # generic tags are dropped
    assert ranked[0]["videos"] == 3


def test_clean_seed():
    assert clean_seed("GTA 6") == "gta6" and clean_seed("#Lacy") == "lacy" and clean_seed("!!") == ""


# ---- text writer ----


def test_clean_copy_normalises_model_output():
    out = llm.clean_copy({
        "keep": True, "reason": " fine ", "hook": "one two three four five six seven eight nine ten eleven twelve",
        "title": "t" * 150, "description": "d", "tags": ["gta 6", "#Lacy", "lacy", "#", "#funny!"], "intro": " hi ", "rank_line": "x",
    })
    assert len(out["hook"].split()) == 10 and len(out["title"]) == 100
    assert out["tags"] == ["#gta6", "#Lacy", "#funny"]  # cleaned, de-duplicated ignoring case, empties dropped
    assert out["intro"] == "hi" and out["reason"] == "fine"


def test_fallback_copy_without_ai():
    state = {"channel": "Lacy", "transcript": "headshot two three yo how many bullets do you need to kill"}
    out = llm.fallback_copy(state, ["#gta", "#lacy", "#luciacaminos"], max_tags=3)
    assert out["keep"] and out["hook"] == "Wait for it" and out["title"] == "Lacy stream moment"  # general on purpose, never half a sentence
    assert out["tags"] == ["#Lacy", "#gta", "#luciacaminos"]  # streamer first, no duplicate of it
    quiet = llm.fallback_copy({"channel": "Lacy", "transcript": "ok"}, [])
    assert not quiet["keep"] and "speech" in quiet["reason"]


def test_prompt_contains_what_the_model_needs():
    p = llm.clip_prompt({"channel": "Lacy", "stream_title": "GTA 6", "transcript": "no way", "score": 4.4, "start_s": 10, "end_s": 40}, ["#gta"])
    assert "Streamer: Lacy" in p and "Transcript:\nno way" in p and "#gta" in p and "30 seconds" in p


def test_no_key_means_unavailable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "load_env", lambda: None)
    assert not llm.available(Config())
    with pytest.raises(llm.LlmUnavailable):
        llm.write_copy({"id": "x"}, [], Config())


def test_text_typed_by_a_person_is_never_replaced(tmp_path, monkeypatch):
    from clipper import pipeline, trends

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "load_env", lambda: None)
    monkeypatch.setattr(trends, "trending", lambda seeds, cfg, top=12: [{"tag": "#gta"}, {"tag": "#luciacaminos"}])
    d = tmp_path / "clip"
    d.mkdir()
    (d / "final.mp4").write_bytes(b"old render")
    state = {"id": "clip", "channel": "Lacy", "hook": "My own hook", "transcript": "one two three four five six seven eight"}
    write_json(d / "clip.json", state)
    out = pipeline.write_clip_copy(d, dict(state), Config())
    assert out["hook"] == "My own hook" and "hook" not in out["copy_auto"]
    assert out["tags"] == ["#Lacy", "#gta", "#luciacaminos"] and out["trend_tags"] == ["#gta", "#luciacaminos"]
    assert (d / "final.mp4").exists()  # hook unchanged, so the render is still valid
    # Text written automatically earlier may be rewritten; and a new hook makes the old render stale.
    auto_state = {**read_json(d / "clip.json"), "hook": "", "copy_at": None}
    out2 = pipeline.write_clip_copy(d, auto_state, Config())
    assert out2["hook"] == "Wait for it" and not (d / "final.mp4").exists()


# ---- narration timing and hardware choices ----


def test_spread_words_fills_the_duration_in_order():
    words = spread_words("Lacy has been live for an hour", 3.0)
    assert [w.text for w in words] == ["Lacy", "has", "been", "live", "for", "an", "hour"]
    assert words[0].start == 0 and words[-1].end == pytest.approx(3.0, abs=0.01)
    assert all(a.end <= b.start + 1e-6 for a, b in zip(words, words[1:]))
    assert words[0].end - words[0].start > words[5].end - words[5].start  # "Lacy" gets longer than "an"
    assert spread_words("", 3.0) == [] and spread_words("hi", 0) == []


def test_align_words_uses_heard_times_and_fills_gaps():
    heard = [{"start": 0.4, "end": 0.7, "word": "Lacy"}, {"start": 0.7, "end": 0.9, "word": "has"},
             {"start": 1.5, "end": 1.8, "word": "live"}, {"start": 1.8, "end": 2.0, "word": "for."}]
    words = align_words("Lacy has been live for", heard, 2.5)
    assert [w.text for w in words] == ["Lacy", "has", "been", "live", "for"]
    assert (words[0].start, words[0].end) == (0.4, 0.7)  # the silence before the voice is respected
    assert words[2].start == pytest.approx(0.9) and words[2].end == pytest.approx(1.5)  # missed word fills the gap
    assert (words[3].start, words[4].end) == (1.5, 2.0)
    assert all(a.end <= b.start + 1e-6 for a, b in zip(words, words[1:]))


def test_align_words_falls_back_when_little_matches():
    heard = [{"start": 0.2, "end": 0.5, "word": "something"}, {"start": 0.5, "end": 0.9, "word": "else"}]
    assert align_words("Lacy has been live", heard, 2.0) == spread_words("Lacy has been live", 2.0)
    assert align_words("Lacy has been live", [], 2.0) == spread_words("Lacy has been live", 2.0)


def test_card_graphics_rank_or_label_and_progress_bar():
    styles, events = card_graphics(4.2, "00E6FF", big="#3")
    assert any(s.startswith("Style: Rank,") for s in styles)
    assert sum("\\t(0,4200,\\fscx100)" in e for e in events) == 1  # the bar fills over the whole card
    assert any(e.endswith("#3") and ",Rank," in e for e in events) and not any(",Label," in e for e in events)
    _styles, events = card_graphics(4.2, "00E6FF", label="La{cy")
    assert any(e.endswith("LA(CY") and ",Label," in e for e in events)  # braces cannot break the script
    assert all(e.split(",")[2] == "0:00:04.20" for e in events)


def test_card_filter_scales_with_output_size():
    full = card_filter(1080, 1920, 30, 120, "c.ass")
    assert "pad=772:962:6:6:white" in full and "overlay=x=154:y='310+" in full and "ass=c.ass" in full and full.endswith("[v]")
    half = card_filter(540, 960, 30, 120, "c.ass")
    assert "pad=386:480:3:3:white" in half and "s=540x960" in half


def test_encoder_args_per_hardware():
    assert hw.encoder_args("libx264", 20, "veryfast")[:6] == ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    assert "h264_nvenc" in hw.encoder_args("h264_nvenc", 20, "veryfast")
    assert "h264_qsv" in hw.encoder_args("h264_qsv", 20, "veryfast")


def test_forced_settings_skip_detection():
    cfg = Config()
    cfg.render.encoder = "libx264"
    cfg.transcribe.device, cfg.transcribe.compute_type = "cpu", "auto"
    assert hw.encoder(cfg) == "libx264" and hw.whisper_device(cfg) == ("cpu", "int8")


def test_plain_text_is_redone_once_ai_is_available(tmp_path, monkeypatch):
    from clipper import pipeline
    from clipper.config import load_config
    from clipper.work import write_json

    cfg = load_config()
    cfg.trends.enabled = False
    d = tmp_path / "c"
    d.mkdir()
    state = {"id": "c", "channel": "Lacy", "transcript": "one two three four five six seven", "start_s": 0, "end_s": 30}
    write_json(d / "clip.json", state)
    monkeypatch.setattr(llm, "available", lambda cfg: False)
    monkeypatch.setattr(llm, "write_copy", lambda *a: (_ for _ in ()).throw(llm.LlmUnavailable("no key here")))
    state = pipeline.write_clip_copy(d, state, cfg)
    assert state["copy_source"] == "plain" and state["copy_problem"] == "no key here" and state["hook"] == "Wait for it"
    state["hook_b"] = "typed by a person"
    state["copy_auto"] = [k for k in state["copy_auto"] if k != "hook_b"]
    # A key appears: the next call replaces the placeholders, but not what a person typed.
    monkeypatch.setattr(llm, "available", lambda cfg: True)
    monkeypatch.setattr(llm, "write_copy", lambda *a: llm.clean_copy({"hook": "He misses every shot", "hook_b": "ai b", "title": "T", "tags": ["x"]}))
    state = pipeline.write_clip_copy(d, state, cfg)
    assert state["hook"] == "He misses every shot" and state["hook_b"] == "typed by a person"
    assert state["copy_source"] != "plain" and "copy_problem" not in state


def _cli_cfg(tmp_path):
    from clipper.config import load_config

    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    cfg.llm.backend = "claude-code"
    return cfg


def test_claude_code_backend_runs_the_command_without_the_api_key(tmp_path, monkeypatch):
    import json
    import subprocess

    cfg = _cli_cfg(tmp_path)
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"], user=kw["input"], cwd=kw["cwd"])
        reply = {"is_error": False, "structured_output": {"answer": "7"}, "usage": {"input_tokens": 5, "output_tokens": 2}, "total_cost_usd": 0.001}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(reply) + "\n", stderr="")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-to-be-passed-on")
    monkeypatch.setattr(llm.shutil, "which", lambda name: r"C:\bin\claude.exe")
    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    assert llm.why_unavailable(cfg) == ""
    out = llm.ask(cfg, "claude-haiku-5-5", "be brief", "which is bigger", {"type": "object"}, "a test")
    assert out == {"answer": "7"} and seen["user"] == "which is bigger"
    assert "ANTHROPIC_API_KEY" not in seen["env"]  # or the command would bill the API instead of using the plan
    assert seen["cmd"][:4] == [r"C:\bin\claude.exe", "-p", "--model", "claude-haiku-5-5"] and "--json-schema" in seen["cmd"]
    assert "tmp" in seen["cwd"]  # an empty folder, not the project
    logged = (tmp_path / "usage.jsonl").read_text(encoding="utf-8")
    assert "claude-code" in logged and "a test" in logged


def test_claude_code_backend_explains_what_is_wrong(tmp_path, monkeypatch):
    import json
    import subprocess

    import pytest

    cfg = _cli_cfg(tmp_path)
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    assert "Open Set up in the menu" in llm.why_unavailable(cfg)  # the guide installs it and starts the sign-in
    with pytest.raises(llm.LlmUnavailable, match="is not installed"):
        llm.ask(cfg, "claude-haiku-5-5", "s", "u", {}, "t")
    monkeypatch.setattr(llm.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(llm.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, stdout=json.dumps({"is_error": True, "result": "Not logged in · Please run /login"}), stderr=""))
    with pytest.raises(llm.LlmUnavailable, match="Not logged in"):
        llm.ask(cfg, "claude-haiku-5-5", "s", "u", {}, "t")


def _auto_cfg(tmp_path, monkeypatch):
    from clipper.config import load_config

    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    cfg.llm.backend, cfg.llm.prefer, cfg.llm.fallback = "auto", "claude-code", True
    monkeypatch.setattr(llm, "load_env", lambda: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(llm.shutil, "which", lambda name: "claude")
    return cfg


def test_automatic_uses_the_preferred_connection_and_falls_back_out_loud(tmp_path, monkeypatch):
    cfg = _auto_cfg(tmp_path, monkeypatch)
    assert llm.candidates(cfg) == ["claude-code", "api"] and llm.why_unavailable(cfg) == ""
    calls = []
    monkeypatch.setattr(llm, "_ask_claude_code", lambda *a: (calls.append("plan"), (_ for _ in ()).throw(llm.LlmUnavailable("usage limit reached")))[1])
    monkeypatch.setattr(llm, "_ask_api", lambda *a: (calls.append("api"), {"ok": True})[1])
    assert llm.ask(cfg, "m", "s", "u", {}, "t") == {"ok": True} and calls == ["plan", "api"]
    checks = llm.read_checks(cfg)
    assert checks["claude-code"]["ok"] is False and checks["api"]["ok"] is True
    assert "usage limit reached" in checks["note"] and "was used instead" in checks["note"]
    # The broken one is now tried last, so the next request does not waste time on it.
    assert llm.candidates(cfg) == ["api", "claude-code"]


def test_fallback_can_be_switched_off(tmp_path, monkeypatch):
    import pytest

    cfg = _auto_cfg(tmp_path, monkeypatch)
    cfg.llm.fallback = False
    calls = []
    monkeypatch.setattr(llm, "_ask_claude_code", lambda *a: (_ for _ in ()).throw(llm.LlmUnavailable("usage limit reached")))
    monkeypatch.setattr(llm, "_ask_api", lambda *a: calls.append("api"))
    with pytest.raises(llm.LlmUnavailable, match="usage limit reached"):
        llm.ask(cfg, "m", "s", "u", {}, "t")
    assert calls == []  # the paid key was not touched


def test_automatic_with_only_one_connection_set_up(tmp_path, monkeypatch):
    cfg = _auto_cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    assert llm.candidates(cfg) == ["api"]
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert llm.candidates(cfg) == [] and "neither way" in llm.why_unavailable(cfg)


def test_a_check_is_remembered_until_the_key_changes(tmp_path, monkeypatch):
    cfg = _auto_cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(llm, "_ask_api", lambda *a: (_ for _ in ()).throw(llm.LlmUnavailable("the ANTHROPIC_API_KEY in .env was rejected")))
    assert llm.check(cfg, "api") == {"ok": False, "problem": "the ANTHROPIC_API_KEY in .env was rejected"}
    assert llm.read_checks(cfg)["api"]["ok"] is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-another")
    assert llm.read_checks(cfg)["api"]["ok"] is None  # a different key: the old result says nothing about it
