"""Ahmad, 2026-11-27: captions best or fast, as a choice on the Settings page. The other model is never downloaded by the choice itself."""

import json
from pathlib import Path

import pytest

import clipper
from clipper import control, health, setupguide
from clipper.config import load_config

PAGE = clipper.page_source()


def _cfg(tmp_path, model):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    cfg.transcribe.backend, cfg.transcribe.model = "local", model
    return cfg


def _model_on_disk(tmp_path, repo):
    d = tmp_path / "models" / f"models--{repo}" / "snapshots" / "abc"
    d.mkdir(parents=True)
    (d / "model.bin").write_bytes(b"m")


def test_only_the_two_models_offered_can_be_chosen():
    assert control.clean_options({"speech_model": "small"}) == {"transcribe": {"model": "small"}}
    assert control.clean_options({"speech_model": "large-v3-turbo"}) == {"transcribe": {"model": "large-v3-turbo"}}
    for bad in ("large-v3", "", "../x", None):
        with pytest.raises(ValueError, match="Best or Fast"):
            control.clean_options({"speech_model": bad})


def test_a_model_chosen_but_not_downloaded_is_asked_for_not_fetched(tmp_path, monkeypatch):
    _model_on_disk(tmp_path, "Systran--faster-whisper-small")
    (tmp_path / "health.json").write_text(json.dumps({"speech": {"ok": True, "detail": "worked"}}), encoding="utf-8")
    there = {i["id"]: i for i in health.quick_checks(_cfg(tmp_path, "small"))}["speech"]
    assert there["state"] == "ok"                                              # the one that was tried and is here
    cfg = _cfg(tmp_path, "large-v3-turbo")                                     # changed on the Settings page: that one is not here
    item = {i["id"]: i for i in health.quick_checks(cfg)}["speech"]
    assert item["state"] == "todo" and "large-v3-turbo" in item["detail"] and "Download and try it" in item["fix"] and "1.6 GB" in item["fix"]
    monkeypatch.setenv(health.ASK_FIRST, "1")
    assert health.captions_blocked(cfg) == health.NEED_CAPTIONS                 # nothing that needs captions starts, and nothing downloads
    assert not health.captions_blocked(_cfg(tmp_path, "small"))


def test_the_set_up_step_names_the_size_of_the_model_chosen(tmp_path, monkeypatch):
    tools = {"claude": {"installed": False, "signed_in": False}, "codex": {"installed": False, "signed_in": False}, "node": False, "winget": True}
    monkeypatch.setitem(setupguide._cache, "at", 9e18)                         # as if the tools had just been asked: no program is started
    monkeypatch.setitem(setupguide._cache, "value", tools)
    monkeypatch.setitem(setupguide._cache, "asking", False)
    for model, size in (("small", "about 0.5 GB the first time"), ("large-v3-turbo", "about 1.6 GB the first time")):
        cfg = _cfg(tmp_path, model)
        step = next(s for s in setupguide.status(cfg, health.quick_checks(cfg), {"ready": False, "supported": False, "why": "it has 4 GB of memory"})["steps"] if s["id"] == "speech")
        assert step["size"] == size
    never = {i["id"]: i for i in health.quick_checks(_cfg(tmp_path, "small"))}["speech"]
    assert never["state"] == "todo" and "about 0.5 GB" in never["fix"]


def test_the_settings_page_offers_best_or_fast():
    for piece in ('<select id="l-model" onchange="saveModel()">', '<option value="large-v3-turbo">Best:', '<option value="small">Fast:',
                  "act('/api/options',{speech_model:val('l-model')})", "This speech model is not on this computer yet.", "modelDraw(s);"):
        assert piece in PAGE, piece
    ct = (Path(clipper.__file__).parent / "control.py").read_text(encoding="utf-8")
    assert '"speech_model": cfg.transcribe.model, "speech_model_there":' in ct
