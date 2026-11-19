import pytest

from clipper import health


def test_summary_counts_what_needs_attention():
    items = [{"state": "ok"}, {"state": "warn"}, {"state": "todo"}]
    assert health.summary(items)["text"] == "Not fully checked yet"
    assert health.summary(items + [{"state": "bad"}]) == {"bad": 1, "warn": 1, "todo": 1, "text": "1 thing(s) to fix"}
    assert health.summary([{"state": "ok"}])["text"] == "Everything is ready"
    assert health.summary([{"state": "ok"}, {"state": "warn"}])["text"] == "Ready, with notes"


def test_quick_checks_name_what_is_missing_and_how_to_fix_it(tmp_path, monkeypatch):
    from clipper import tools
    from clipper.config import load_config

    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)  # nothing installed system-wide either
    items = {i["id"]: i for i in health.quick_checks(cfg)}
    assert items["ffmpeg"]["state"] == "bad" and "Repair" in items["ffmpeg"]["fix"]
    assert items["face"]["state"] == "bad" and items["voice"]["state"] == "warn"
    assert items["speech"]["state"] == "todo" and items["update"]["state"] == "todo"
    assert items["disk"]["state"] in ("ok", "warn", "bad") and "GB free" in items["disk"]["detail"]
    assert all(i["fix"] for i in items.values() if i["state"] in ("bad", "todo"))  # every problem says what to do


def test_a_speech_model_that_cannot_load_is_an_error_not_a_crash(tmp_path, monkeypatch):
    from clipper import hw, transcribe
    from clipper.config import load_config
    from clipper.tools import ToolError

    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    cfg.transcribe.device, cfg.transcribe.compute_type = "cpu", "int8"
    monkeypatch.setattr(transcribe, "_model", None)
    monkeypatch.setattr(hw, "trial_load", lambda *a, **k: (False, "it crashed: exit code -1073741819"))
    with pytest.raises(ToolError, match="could not be loaded"):
        transcribe.load_model(cfg)
