"""Ahmad, 2026-11-27: the program must not go to the Set up page by itself or download anything without approval;
the Set up entry in the menu is a warning instead."""

from pathlib import Path

import pytest

import clipper
from clipper import health
from clipper.config import load_config

PAGE = (Path(clipper.__file__).parent / "review_page.html").read_text(encoding="utf-8")


def _cfg(tmp_path):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    return cfg


def _with_model(cfg, name="large-v3-turbo"):
    snap = Path(cfg.data_dir) / "models" / f"models--someone--faster-whisper-{name}" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x")


def test_the_page_never_opens_set_up_by_itself():
    draw = PAGE[PAGE.index("function suDraw(s){"):PAGE.index("if(curPage!=='setup')return;")]
    assert "go('setup')" not in draw and "setupSeen" not in PAGE and "SU.opened" not in PAGE
    # not even because the program was closed on that page last time
    assert "if(first==='setup')first='new';go(HAS_PANEL?first:'clips')" in PAGE
    # the one place that names the page is the person's own click on the menu entry
    assert PAGE.count("go('setup')") == 1 and 'id="n-setup" onclick="go(\'setup\')"' in PAGE


def test_the_menu_entry_is_the_warning():
    for piece in (".navlist button.attn{color:var(--warn);background:var(--warn-soft)", '<symbol id="i-warn"',
                  "nb.classList.toggle('attn',left>0)", "setAttribute('href',left?'#i-warn':'#i-check')", "left?left+' to do'",
                  "Nothing is downloaded before you press."):
        assert piece in PAGE, piece
    assert "prefers-reduced-motion:reduce){.navlist button.attn svg.i{animation:none}" in PAGE   # the blink can be switched off


def test_a_job_from_the_page_does_not_download_the_speech_model(tmp_path, monkeypatch):
    from clipper import hw, transcribe
    from clipper.tools import ToolError

    cfg = _cfg(tmp_path)
    cfg.transcribe.backend, cfg.transcribe.device, cfg.transcribe.compute_type = "local", "cpu", "int8"
    tried = []
    monkeypatch.setattr(hw, "trial_load", lambda *a, **k: (tried.append(a), (False, "x"))[1])
    monkeypatch.setattr(transcribe, "_model", None)

    monkeypatch.setenv(health.ASK_FIRST, "1")       # as under `clipper review` and the jobs it starts
    assert health.captions_blocked(cfg) == health.NEED_CAPTIONS
    with pytest.raises(ToolError, match="Open Set up in the menu"):
        transcribe.load_model(cfg)
    assert tried == []                              # refused before the trial that would have fetched 1.6 GB

    _with_model(cfg, str(cfg.transcribe.model).split("/")[-1])
    assert health.captions_blocked(cfg) == ""       # the model is on the computer: nothing to ask
    assert health.captions_blocked(cfg, "tiny") == health.NEED_CAPTIONS   # another model than the one that is there

    cfg2 = _cfg(tmp_path / "other")
    cfg2.transcribe.backend = "groq"                # captions made on Groq's servers: no model needed here
    assert health.captions_blocked(cfg2) == ""


def test_a_terminal_user_is_not_stopped(tmp_path, monkeypatch):
    """From a terminal the model is still fetched by itself, with a loud line: that person typed the command."""
    from clipper import hw, transcribe
    from clipper.tools import ToolError

    cfg = _cfg(tmp_path)
    cfg.transcribe.device, cfg.transcribe.compute_type = "cpu", "int8"
    monkeypatch.delenv(health.ASK_FIRST, raising=False)
    monkeypatch.setattr(transcribe, "_model", None)
    monkeypatch.setattr(hw, "trial_load", lambda *a, **k: (False, "the trial ran"))
    assert health.captions_blocked(cfg) == ""
    with pytest.raises(ToolError, match="the trial ran"):
        transcribe.load_model(cfg)


def test_step_five_says_what_it_measured(tmp_path, monkeypatch):
    """Ahmad asked how step 5 decides whether the computer can run the free AI: the step now shows the numbers."""
    import json

    from clipper import localai, setupguide

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(localai, "_ram_gb", lambda: 17.0)   # what a "16 GB" PC reports, in billions of bytes
    monkeypatch.setattr(localai.os, "cpu_count", lambda: 8)
    assert localai.facts(cfg) == "15.8 GB of memory, 8 processor threads"
    assert localai.supported(cfg) == (True, "")
    Path(cfg.data_dir, "hardware.json").write_text(json.dumps({"vram_mb": 4096}), encoding="utf-8")
    assert localai.facts(cfg).endswith(", an NVIDIA card with 4 GB")
    step = setupguide.status(cfg, [], {"supported": True, "facts": "16 GB of memory, 8 processor threads"})["steps"][-1]
    assert step["id"] == "localai" and step["state"] == "todo" and step["actions"]
    assert "it has 16 GB of memory, 8 processor threads" in step["detail"] and "about 8 GB of memory and a processor with 4 threads" in step["detail"]
    small = setupguide.status(cfg, [], {"supported": False, "why": "this computer has 4 GB of memory; the local model needs about 8 GB"})["steps"][-1]
    assert small["actions"] == [] and "4 GB of memory" in small["detail"] and "Nothing is downloaded" in small["detail"]


def test_the_page_program_switches_asking_first_on_and_jobs_stop_at_the_start(tmp_path, monkeypatch):
    from clipper.control import Control

    cli = (Path(clipper.__file__).parent / "cli.py").read_text(encoding="utf-8")
    review = cli[cli.index("def _cmd_review("):cli.index("return serve(")]
    assert 'os.environ[ASK_FIRST] = "1"' in review

    cfg = _cfg(tmp_path)
    cfg.transcribe.backend = "local"
    c = Control(cfg)
    monkeypatch.setattr(c, "reload", lambda: cfg)
    monkeypatch.setenv(health.ASK_FIRST, "1")
    for start, raw in ((c.start_run, {"url": "https://www.twitch.tv/videos/123456789"}), (c.start_watch, {"channels": "somebody"}),
                       (c.start_cut, {"source": "https://www.twitch.tv/videos/123456789", "start": "0:10", "end": "0:40"}),
                       (c.start_range_cut, {"key": "twitch_1/0h00m10s-0h00m40s"})):
        with pytest.raises(ValueError, match="Captions are not set up on this computer yet"):
            start(raw)
    assert c.run_proc is None and c.proc is None     # nothing was started
