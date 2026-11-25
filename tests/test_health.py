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


def test_a_pc_without_the_speech_library_gets_a_reason_not_a_crash(tmp_path, monkeypatch):
    """On a new PC without Windows' C++ runtime the speech library cannot even be imported (found on 2026-11-25:
    the installer stopped on a page of code instead of naming the missing part)."""
    import sys

    from clipper import hw, transcribe
    from clipper.config import load_config
    from clipper.tools import ToolError

    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    cfg.transcribe.device, cfg.transcribe.compute_type = "cpu", "int8"
    monkeypatch.setattr(transcribe, "_model", None)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # importing it now fails, as it does without the runtime
    why = "LIKELY CAUSE: Windows' C++ runtime (msvcp140.dll) is missing"
    monkeypatch.setattr(hw, "trial_load", lambda *a, **k: (False, why))
    with pytest.raises(ToolError, match="msvcp140.dll. is missing"):  # the trial's reason is shown, the import is never reached
        transcribe.load_model(cfg)
    monkeypatch.setattr(hw, "trial_load", lambda *a, **k: (True, ""))
    with pytest.raises(ToolError, match="could not be started"):
        transcribe.load_model(cfg)


def test_setup_names_the_missing_windows_runtime_even_when_it_stops_early():
    from clipper.config import PROJECT_ROOT

    text = (PROJECT_ROOT / "setup.ps1").read_text(encoding="utf-8")
    stop = text.index("Tests failed. Fix before using the pipeline.")
    assert "Write-RuntimeNote" in text[stop:text.index("exit $code", stop)]
    assert text.index("MISSING  Microsoft Visual C++ runtime") < text.index("== Tests ==")


def test_restart_warning_follows_the_code_not_file_dates(tmp_path, monkeypatch):
    import os
    import time

    from clipper import review

    before = review.code_stamp()
    target = next(iter(sorted(review.Path(review.__file__).parent.glob("*.py"))))
    old = target.stat()
    try:
        os.utime(target, (time.time(), time.time()))  # touched, not changed
        assert review.code_stamp() == before
    finally:
        os.utime(target, (old.st_atime, old.st_mtime))


def test_a_copy_started_from_a_terminal_says_how_to_restart(tmp_path, monkeypatch):
    from clipper.config import load_config
    from clipper.control import Control

    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    monkeypatch.delenv("CLIPPER_LAUNCHER", raising=False)
    with pytest.raises(ValueError, match="Stream-Clipper.cmd"):
        Control(cfg).restart()
