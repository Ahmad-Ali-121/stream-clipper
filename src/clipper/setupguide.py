"""The step-by-step setup guide: what this computer still needs, one step at a time, each with one button.

The guide installs the helper programs for the user (the video tools, Claude Code, Node.js and Codex, the free local
AI) and starts each program's own sign-in. Two rules it keeps:

* Signing in happens in the other program's own window and in the user's own browser. This program never sees,
  reads or stores a password, a key or a token. It only asks the tool "are you signed in?" and reads yes or no;
  anything else the tool answers (an account name, an email address) is thrown away unread.
* Nothing large is downloaded without a press: every step says its size first, and the optional ones can be skipped.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .config import PROJECT_ROOT, Config
from .work import read_json, write_json

log = logging.getLogger(__name__)

CLAUDE_INSTALL = "irm https://claude.ai/install.ps1 | iex"      # Anthropic's own installer for Windows
NODE_PACKAGE = "OpenJS.NodeJS.LTS"                               # Node.js, from Windows' own installer (winget)
CODEX_PACKAGE = "@openai/codex"                                  # OpenAI's command-line tool, through npm
_cache: dict = {"at": 0.0, "value": None, "asking": False}


def extra_paths() -> list[str]:
    """Folders where a tool lands when it is installed while this program is already running. Windows only tells
    programs started afterwards, so they are looked in directly (and added to this program's own search path)."""
    home = Path.home()
    found = [home / ".local" / "bin",                                       # Claude Code's own installer
             Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "npm",   # npm's global tools (Codex)
             Path(os.environ.get("ProgramFiles") or r"C:\Program Files") / "nodejs",
             PROJECT_ROOT / "tools" / "node"]
    return [str(p) for p in found if p.is_dir()]


def see_new_tools() -> None:
    """Let this running program find tools that were installed a moment ago."""
    have = os.environ.get("PATH", "").split(os.pathsep)
    add = [p for p in extra_paths() if p not in have]
    if add:
        os.environ["PATH"] = os.pathsep.join(have + add)


def which(name: str) -> str | None:
    see_new_tools()
    return shutil.which(name)


def _ask(cmd: list[str], timeout: float = 40) -> tuple[int | None, str]:
    """Run a tool's own status command quietly. Returns (exit code, what it printed).

    When the tool could not be asked at all the exit code is None and the text says in a few words of our own why
    (never the tool's words). The tool and everything it started are ended at the time limit: Codex is a small
    script that starts Node, and ending only the script would leave this waiting for Node for as long as it hangs.
    Claude is asked without an API key in its surroundings, as it is when it writes the titles: with a key there it
    would answer for the key and not for the plan.
    """
    from . import tools

    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        return None, f"it could not be started ({type(e).__name__})"
    tools._own_group(proc)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        tools.kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        return None, f"it did not answer within {timeout:.0f} seconds"
    tools._end_group(proc, kill=False)
    return proc.returncode, out or ""


def _yes_no(said: str) -> bool | None:
    """The yes or no in a tool's answer, or None when it holds neither. Nothing else of the answer is looked at."""
    import re

    m = re.search(r'"loggedIn"\s*:\s*(true|false)', said)   # Claude Code's JSON, also with a notice printed around it
    if m:
        return m.group(1) == "true"
    low = said.lower()
    if "not logged in" in low or "not signed in" in low or "logged out" in low:
        return False
    if "logged in" in low or "signed in" in low:
        return True
    return None


def _state(name: str, cmd: list[str]) -> dict:
    code, said = _ask(cmd)
    if code is None:   # the tool was not asked: that is not "signed out", and the page says which it is
        return {"installed": True, "signed_in": False, "problem": f"{name} was asked whether it is signed in, but {said}"}
    signed = _yes_no(said)
    if signed is None:
        return {"installed": True, "signed_in": False, "problem": f"{name} answered, but not with a yes or a no (it ended with code {code})"}
    return {"installed": True, "signed_in": signed}


def claude_state() -> dict:
    """{"installed", "signed_in"}: asked from Claude Code itself. Only its yes or no is kept."""
    exe = which("claude")
    if not exe:
        return {"installed": False, "signed_in": False}
    return _state("Claude Code", [exe, "auth", "status", "--json"])


def codex_state() -> dict:
    exe = which("codex")
    if not exe:
        return {"installed": False, "signed_in": False}
    return _state("Codex", [exe, "login", "status"])


def _ask_tools() -> dict:
    return {"claude": claude_state(), "codex": codex_state(), "node": bool(which("node") and which("npm")), "winget": bool(shutil.which("winget"))}


def _tools(fresh: bool = False) -> dict:
    """What Claude Code and Codex say about themselves. Asking takes a couple of seconds, and the page asks for its
    state every few seconds, so the tools are asked in the background and the last answer is used meanwhile.
    Before the first answer, "signed_in" is None: not known yet."""
    import threading

    if fresh:
        _cache.update(at=time.time(), value=_ask_tools())
        return _cache["value"]
    if (_cache["value"] is None or time.time() - _cache["at"] > 30) and not _cache["asking"]:
        _cache["asking"] = True

        def ask() -> None:
            try:
                _cache.update(at=time.time(), value=_ask_tools())
            except Exception as e:  # never let a question to a tool end the thread without a word
                log.warning("the setup guide could not ask the tools: %s", e)
            finally:
                _cache["asking"] = False

        threading.Thread(target=ask, daemon=True, name="setup-tools").start()
    if _cache["value"] is None:
        unknown = {"installed": True, "signed_in": None}
        return {"claude": unknown, "codex": unknown, "node": True, "winget": True}
    return _cache["value"]


def _flags(cfg: Config) -> dict:
    return read_json(cfg.data_dir / "setup.json", {}) or {}


def finish(cfg: Config, skipped: list[str] | None = None) -> None:
    """The person has been through the guide: it does not open by itself any more."""
    flags = _flags(cfg)
    flags.update(finished=True, finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    if skipped is not None:
        flags["skipped"] = sorted({str(s)[:30] for s in skipped})
    write_json(cfg.data_dir / "setup.json", flags)


def status(cfg: Config, health_items: list[dict], local_ai: dict, fresh: bool = False) -> dict:
    """What the guide shows: its steps in order, each {"id", "title", "state", "detail", "size", "optional", "actions"}.

    ``state`` is "done", "todo" (something to press) or "wait" (an earlier step comes first).
    The questions to Claude Code and Codex take a second or two each, so their answers are kept for half a minute.
    """
    ext = _tools(fresh)
    by = {i["id"]: i for i in health_items}
    need = [by[k] for k in ("ffmpeg", "TwitchDownloaderCLI", "face", "voice", "runtime") if k in by]
    # no list at all (the check itself failed) is not "all there"
    tools_ok = bool(need) and all(i["state"] == "ok" for i in need if i["id"] != "voice") and by.get("voice", {}).get("state") in ("ok", None)
    missing = [i["name"].split(" (")[0] for i in need if i["state"] != "ok"]
    unchecked = "" if need else ("This computer could not be checked: " + str(by.get("check", {}).get("detail") or "the list of tools came back empty")
                                 + ". Press Check again; if it stays, the terminal window says more.")
    runtime_bad = by.get("runtime", {}).get("state") == "bad"
    speech = by.get("speech", {})
    steps: list[dict] = []
    steps.append({"id": "tools", "title": "Video tools", "optional": False, "size": "about 300 MB",
                  "what": "FFmpeg and TwitchDownloader cut and download the video; two small models find the face and speak the explainer's lines.",
                  "state": "done" if tools_ok else "todo",
                  "detail": "All there." if tools_ok else unchecked or ("Missing: " + ", ".join(missing) + "."
                            + (" The Windows C++ runtime needs its own installer: " + by["runtime"].get("fix", "") if runtime_bad else "")),
                  "actions": [] if tools_ok or unchecked else [{"do": "install", "what": "tools", "label": "Install the video tools"}]})
    speech_ok = speech.get("state") == "ok"
    steps.append({"id": "speech", "title": "Captions", "optional": False, "size": "about 1.6 GB the first time",
                  "what": "The speech model hears what is said and writes the captions. It is downloaded once and then tried on this computer.",
                  "state": "done" if speech_ok else ("wait" if not tools_ok else "todo"),
                  "detail": "The video tools come first: go back one step." if not speech_ok and not tools_ok else
                            (speech.get("detail") or ("Works on this computer." if speech_ok else "Not tried yet on this computer.")),
                  "actions": [] if speech_ok or not tools_ok else [{"do": "install", "what": "speech", "label": "Download and try it"}]})
    c = ext["claude"]
    steps.append({"id": "claude", "title": "Claude, for titles and ratings", "optional": True, "size": "about 250 MB",
                  "what": "Claude writes each clip's opening text, title and description and judges whether it is worth posting. It uses your own Claude plan. "
                          "Without it everything still works, with placeholder text.",
                  "state": "done" if c["signed_in"] else ("wait" if c["signed_in"] is None else "todo"),
                  "detail": "Checking..." if c["signed_in"] is None else
                            ("Installed and signed in." if c["signed_in"] else (_unsure(c) or "Installed. Not signed in yet.") if c["installed"] else "Not installed."),
                  "actions": [] if c["signed_in"] or c["signed_in"] is None else
                             ([{"do": "signin", "what": "claude", "label": "Sign in to Claude"}] if c["installed"]
                              else [{"do": "install", "what": "claude", "label": "Install Claude Code"}])})
    x = ext["codex"]
    if x["signed_in"] is None:
        acts, detail = [], "Checking..."
    elif x["signed_in"]:
        acts, detail = [], "Installed and signed in."
    elif x["installed"]:
        acts, detail = [{"do": "signin", "what": "codex", "label": "Sign in to ChatGPT"}], _unsure(x) or "Installed. Not signed in yet."
    elif ext["node"]:
        acts, detail = [{"do": "install", "what": "codex", "label": "Install the ChatGPT tool"}], "Not installed."
    elif ext["winget"]:
        acts, detail = [{"do": "install", "what": "node", "label": "Install Node.js first"}], "Needs Node.js, which is not on this computer."
    else:
        acts, detail = [], "Needs Node.js. Install it from https://nodejs.org (the LTS version), then press Check again."
    steps.append({"id": "codex", "title": "ChatGPT, for thumbnails", "optional": True, "size": "Node.js about 90 MB, the tool about 150 MB",
                  "what": "ChatGPT can draw a thumbnail for a clip, through OpenAI's tool Codex and your own ChatGPT plan (pictures need a paid plan). "
                          "Without it, thumbnails are made from the clip itself.",
                  "state": "done" if x["signed_in"] else ("wait" if x["signed_in"] is None else "todo"), "detail": detail, "actions": acts})
    if local_ai.get("ready"):
        la_state, la_detail, la_acts = "done", f"Ready ({local_ai.get('model', '')}).", []
    elif not local_ai.get("supported"):
        la_state, la_detail, la_acts = "done", f"Not used on this computer: {local_ai.get('why', 'it cannot run it')}. Nothing is downloaded.", []
    else:
        la_state, la_detail, la_acts = "todo", "This computer can run it. It is not set up.", [{"do": "install", "what": "localai", "label": "Set up free local AI"}]
    steps.append({"id": "localai", "title": "Free AI on this computer", "optional": True, "size": "about 3 GB",
                  "what": "A free AI that runs on this PC and writes clip text when Claude cannot be reached. Slower and plainer than Claude.",
                  "state": la_state, "detail": la_detail, "actions": la_acts})
    flags = _flags(cfg)
    required_left = [s["id"] for s in steps if not s["optional"] and s["state"] != "done"]
    return {"steps": steps, "finished": bool(flags.get("finished")), "skipped": flags.get("skipped") or [], "required_left": required_left,
            "data_dir": str(cfg.data_dir)}


def _unsure(tool: dict) -> str:
    """The line for a tool that is installed but could not be asked: not the same as signed out, so it is not called that."""
    why = tool.get("problem")
    return f"Installed, but its sign-in could not be checked: {why}. Press Check again; if it stays the same, press the Sign in button." if why else ""


def forget() -> None:
    """Ask the tools again next time (after an install or a sign-in)."""
    _cache.update(at=0.0)   # the last answer stays in use until the new one is there


def install_commands(what: str) -> list[tuple[str, list[str]]]:
    """The commands that install one thing, as (what it is in plain words, command)."""
    if sys.platform != "win32":
        raise ValueError("the setup guide installs things on Windows only; on other systems install them by hand")
    if what == "claude":
        return [("downloading and installing Claude Code from Anthropic", ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", CLAUDE_INSTALL])]
    if what == "node":
        winget = shutil.which("winget")
        if not winget:
            raise ValueError("Windows' own installer (winget) is not on this computer. Install Node.js from https://nodejs.org (the LTS version), then press Check again")
        return [("installing Node.js with Windows' own installer (Windows may ask for permission)",
                 [winget, "install", "-e", "--id", NODE_PACKAGE, "--accept-source-agreements", "--accept-package-agreements", "--silent"])]
    if what == "codex":
        npm = which("npm")
        if not npm:
            raise ValueError("Node.js is not on this computer yet; install it first (the step shows the button)")
        return [("installing OpenAI's tool Codex", [npm, "install", "-g", CODEX_PACKAGE])]
    raise ValueError(f"the guide does not install '{what}'")


def signin_command(what: str) -> tuple[str, list[str]]:
    """(window title, command) that starts a tool's own sign-in in a window of its own."""
    if what == "claude":
        exe = which("claude")
        if not exe:
            raise ValueError("Claude Code is not installed yet; install it first")
        return "Sign in to Claude", [exe, "auth", "login"]
    if what == "codex":
        exe = which("codex")
        if not exe:
            raise ValueError("the ChatGPT tool (Codex) is not installed yet; install it first")
        return "Sign in to ChatGPT", [exe, "login"]
    raise ValueError(f"the guide has no sign-in for '{what}'")


def signin_line(script: str) -> str:
    return 'cmd.exe /s /c "' + script + '"'


def signin_script(title: str, cmd: list[str]) -> str:
    """What the sign-in window runs. The tool's path is always inside quotes, so a folder name with & ( ) or ^ in it
    cannot be read as a command, and what is said at the end depends on how the tool ended."""
    line = '"' + cmd[0] + '" ' + " ".join(cmd[1:])   # no "call": it reads the line a second time and breaks on a ^ in the folder name
    return (f'title {title} & echo. & echo   {title}: follow the steps below. Your browser opens for the sign-in. & echo. & {line} '
            '&& (echo. & echo   Finished. You can close this window and go back to Stream Clipper.) '
            '|| (echo. & echo   The sign-in did not finish. Close this window and press the Sign in button again.) & pause')


def open_signin(what: str) -> None:
    """Open the tool's own sign-in in a new window. The person signs in there and in their browser; this program
    only waits and later asks the tool whether it worked."""
    title, cmd = signin_command(what)
    if sys.platform != "win32":
        raise ValueError("open a terminal and run:  " + " ".join(cmd[-2:] if what == "claude" else cmd[-1:]))
    if '"' in cmd[0] or "%" in cmd[0]:
        raise ValueError(f"the tool is in a folder whose name Windows' command window cannot be given safely ({cmd[0]}). "
                         "Open a terminal yourself and run:  " + " ".join([Path(cmd[0]).stem] + cmd[1:]))
    script = signin_script(title, cmd)
    # One plain command line for cmd.exe: with /s it takes everything between the outer quotes as it is, so a tool in
    # a folder whose name has a space (a user called "John Smith") still runs.
    subprocess.Popen(signin_line(script), creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=str(Path.home()))
    forget()
    log.info("opened the sign-in window for %s", what)
