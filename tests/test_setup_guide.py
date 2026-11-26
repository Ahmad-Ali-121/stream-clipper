"""The step-by-step setup guide: what it offers on a computer in each state, and that it never looks at an account."""

import json
import subprocess
from pathlib import Path

import pytest

import clipper
from clipper import setupguide
from clipper.config import load_config

READY = [{"id": i, "name": n, "state": "ok", "detail": "", "fix": ""} for i, n in
         (("ffmpeg", "FFmpeg (cuts)"), ("TwitchDownloaderCLI", "TwitchDownloader (fetches)"), ("face", "Face model"), ("voice", "Narrator voice"),
          ("runtime", "Windows C++ runtime"), ("speech", "Captions"))]
NO_LOCAL = {"ready": False, "supported": False, "why": "it has 4 GB of memory"}


def _cfg(tmp_path):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    return cfg


def _steps(cfg, tools, items=READY, local=NO_LOCAL):
    setupguide._cache.update(at=9e18, value=tools, asking=False)   # as if the tools had just been asked
    return {s["id"]: s for s in setupguide.status(cfg, items, local)["steps"]}


def test_a_new_computer_is_led_through_one_thing_at_a_time(tmp_path):
    cfg = _cfg(tmp_path)
    bare = [dict(i, state="bad") for i in READY[:5]] + [dict(READY[5], state="todo")]
    nothing = {"claude": {"installed": False, "signed_in": False}, "codex": {"installed": False, "signed_in": False}, "node": False, "winget": True}
    s = _steps(cfg, nothing, bare, {"ready": False, "supported": True})
    assert [a["label"] for a in s["tools"]["actions"]] == ["Install the video tools"] and "FFmpeg" in s["tools"]["detail"]
    assert s["speech"]["state"] == "wait"                                    # the tools come first
    assert [a["label"] for a in s["claude"]["actions"]] == ["Install Claude Code"]
    assert [a["what"] for a in s["codex"]["actions"]] == ["node"]             # Codex needs Node.js, so that is offered first
    assert [a["what"] for a in s["localai"]["actions"]] == ["localai"]
    assert not s["tools"]["optional"] and s["claude"]["optional"] and s["codex"]["optional"] and s["localai"]["optional"]
    assert all(x["size"] for x in s.values())                                # every step says how much it downloads
    whole = setupguide.status(cfg, bare, NO_LOCAL)
    assert whole["required_left"] == ["tools", "speech"] and whole["finished"] is False


def test_each_tool_moves_from_install_to_sign_in_to_done(tmp_path):
    cfg = _cfg(tmp_path)
    tools = {"claude": {"installed": True, "signed_in": False}, "codex": {"installed": False, "signed_in": False}, "node": True, "winget": True}
    s = _steps(cfg, tools)
    assert [(a["do"], a["what"]) for a in s["claude"]["actions"]] == [("signin", "claude")]
    assert [(a["do"], a["what"]) for a in s["codex"]["actions"]] == [("install", "codex")]     # Node.js is there: the tool itself is next
    tools["codex"] = {"installed": True, "signed_in": False}
    assert [(a["do"], a["what"]) for a in _steps(cfg, tools)["codex"]["actions"]] == [("signin", "codex")]
    tools = {"claude": {"installed": True, "signed_in": True}, "codex": {"installed": True, "signed_in": True}, "node": True, "winget": True}
    s = _steps(cfg, tools)
    assert all(x["state"] == "done" and not x["actions"] for x in s.values())  # the local AI "cannot run here" counts as nothing to do
    assert "Nothing is downloaded" in s["localai"]["detail"]
    assert setupguide.status(cfg, READY, NO_LOCAL)["required_left"] == []


def test_before_the_tools_have_answered_nothing_is_offered_and_the_page_is_not_kept_waiting(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    asked = []
    monkeypatch.setattr(setupguide, "_ask_tools", lambda: asked.append(1) or {"claude": {"installed": True, "signed_in": True},
                                                                              "codex": {"installed": True, "signed_in": True}, "node": True, "winget": True})
    setupguide._cache.update(at=0.0, value=None, asking=True)   # as if the question were on its way
    s = {x["id"]: x for x in setupguide.status(cfg, READY, NO_LOCAL)["steps"]}
    assert s["claude"]["state"] == "wait" and s["claude"]["detail"] == "Checking..." and not s["claude"]["actions"] and not asked
    assert setupguide._tools(fresh=True)["claude"]["signed_in"] is True and asked == [1]   # "Check again" asks at once


def test_only_yes_or_no_is_taken_from_a_tools_answer(monkeypatch):
    """Claude Code's status answer also names the account. None of that may be kept."""
    monkeypatch.setattr(setupguide, "which", lambda name: "claude.exe")
    answer = json.dumps({"loggedIn": True, "email": "someone@example.com", "orgName": "Their Company", "subscriptionType": "max"})
    monkeypatch.setattr(setupguide, "_ask", lambda cmd, timeout=40: (0, answer))
    got = setupguide.claude_state()
    assert got == {"installed": True, "signed_in": True} and "example.com" not in repr(got)
    monkeypatch.setattr(setupguide, "_ask", lambda cmd, timeout=40: (1, json.dumps({"loggedIn": False})))
    assert setupguide.claude_state()["signed_in"] is False
    monkeypatch.setattr(setupguide, "_ask", lambda cmd, timeout=40: (0, "Logged in using ChatGPT"))
    assert setupguide.codex_state() == {"installed": True, "signed_in": True}
    monkeypatch.setattr(setupguide, "_ask", lambda cmd, timeout=40: (1, "Not logged in"))
    assert setupguide.codex_state()["signed_in"] is False
    code = (Path(clipper.__file__).parent / "setupguide.py").read_text(encoding="utf-8")
    for never in (".codex", ".claude", "credentials", "auth.json", "api_key", "token ="):
        assert never not in code.replace("never sees,\n  reads or stores a password, a key or a token", ""), never   # it runs the tools; it reads none of their files


def test_installing_and_signing_in_use_each_makers_own_way(monkeypatch):
    monkeypatch.setattr(setupguide.sys, "platform", "win32")
    monkeypatch.setattr(setupguide.shutil, "which", lambda name: f"C:\\Windows\\{name}.exe")
    monkeypatch.setattr(setupguide, "which", lambda name: f"C:\\Program Files\\x\\{name}.cmd")
    assert setupguide.install_commands("claude")[0][1][-1] == "irm https://claude.ai/install.ps1 | iex"
    node = setupguide.install_commands("node")[0][1]
    assert node[1:5] == ["install", "-e", "--id", "OpenJS.NodeJS.LTS"]
    assert setupguide.install_commands("codex")[0][1][-3:] == ["install", "-g", "@openai/codex"]
    assert setupguide.signin_command("claude")[1][-2:] == ["auth", "login"] and setupguide.signin_command("codex")[1][-1] == "login"
    for bad in ("", "rm -rf", "tools; del"):
        with pytest.raises(ValueError):
            setupguide.install_commands(bad)
        with pytest.raises(ValueError):
            setupguide.signin_command(bad)
    opened = []
    monkeypatch.setattr(setupguide.subprocess, "Popen", lambda cmd, **k: opened.append((cmd, k)))
    monkeypatch.setattr(setupguide.subprocess, "CREATE_NEW_CONSOLE", 16, raising=False)
    setupguide.open_signin("claude")
    line, how = opened[0]
    # a window of its own, the tool's own sign-in command, and a path with a space kept whole
    assert how["creationflags"] == 16 and line.startswith('cmd.exe /s /c "') and '"C:\\Program Files\\x\\claude.cmd" auth login' in line
    monkeypatch.setattr(setupguide, "which", lambda name: None)
    with pytest.raises(ValueError, match="install it first"):
        setupguide.open_signin("codex")


def test_the_sign_in_line_really_runs_a_program_in_a_folder_with_a_space(tmp_path):
    if not Path(r"C:\Windows\System32\cmd.exe").exists():
        pytest.skip("Windows only")
    tool = tmp_path / "John Smith" / "tool.cmd"
    tool.parent.mkdir()
    tool.write_text("@echo it ran %1\n", encoding="utf-8")
    line = subprocess.list2cmdline([str(tool), "now"])
    out = subprocess.run(setupguide.signin_line(f"echo start & {line} & echo end"), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60).stdout
    assert "it ran now" in out and "end" in out


def test_finishing_is_remembered_and_the_page_has_the_guide(tmp_path):
    cfg = _cfg(tmp_path)
    assert setupguide.status(cfg, READY, NO_LOCAL)["finished"] is False
    setupguide.finish(cfg, ["localai", "codex"])
    got = setupguide.status(cfg, READY, NO_LOCAL)
    assert got["finished"] is True and got["skipped"] == ["codex", "localai"]
    page = (Path(clipper.__file__).parent / "review_page.html").read_text(encoding="utf-8")
    for piece in ('id="n-setup" onclick="go(\'setup\')"', 'id="page-setup"', "function suDraw(s){", "nb.classList.toggle('attn',left>0)", "Checking this computer", "function suSkip(id){",
                  "'/api/setup/'+(what==='signin'?'signin':'install')", "This program never sees your password", "Start using Stream Clipper"):
        assert piece in page, piece
    server = (Path(clipper.__file__).parent / "review.py").read_text(encoding="utf-8")
    for route in ("/api/setup/install", "/api/setup/signin", "/api/setup/refresh", "/api/setup/finish"):
        assert route in server, route


def test_the_big_speech_model_is_never_fetched_without_a_press(tmp_path, monkeypatch):
    """A new install has never tried the speech model. Looking at the page must not start that 1.6 GB download by
    itself; the Set up page's button does. A computer that has tried it before is checked again once a day as before."""
    from clipper import control, health
    from clipper.work import write_json

    monkeypatch.undo()   # this test wants the real start_full_check name back, to watch it
    started = []
    monkeypatch.setattr(control.Control, "start_full_check", lambda self: started.append(1) or {})
    monkeypatch.setattr(health, "check_update", lambda *a, **k: {"behind": 0})
    monkeypatch.setattr(setupguide, "_ask_tools", lambda: {"claude": {"installed": False, "signed_in": False}, "codex": {"installed": False, "signed_in": False},
                                                         "node": False, "winget": True})
    cfg = _cfg(tmp_path)
    c = control.Control(cfg)
    c._health_status()
    assert started == []                                        # nothing known about speech: wait for the person
    write_json(tmp_path / "health.json", {"checked_at": 0, "speech": {"ok": True, "detail": "works on the processor"}})
    c._health_status()
    assert started == []                                        # tried once, but the model is not on this computer (it was deleted, or the try failed)
    model = tmp_path / "models" / ("models--someone--faster-whisper-" + str(cfg.transcribe.model)) / "snapshots" / "abc"
    model.mkdir(parents=True)
    (model / "model.bin").write_bytes(b"x")
    assert health.speech_model_there(cfg)
    c._health_status()
    assert started == [1]                                       # tried before, last check long ago: checked again by itself
