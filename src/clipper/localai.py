"""A free AI that runs on this computer (Ollama), for people with no Claude plan or key.

It writes a clip's opening text, title and rating from the clip's words. It is a small model: it cannot look
at frames, it is not used to read a whole stream, and its text is plainer than Claude's. So Claude is always
tried first when it can be reached, and this is the fallback that keeps clips from getting placeholder text.

Whether it is used is decided by this computer, not by a person:
  - a computer too small for it (little memory, few processor threads, a nearly full disk) never downloads it;
  - one that can run it gets a "Set up" button, which installs Ollama and downloads the model (about 3 GB in all);
  - once it is there it is found by itself and used when Claude is not available.
Nothing is sent anywhere: the model answers on 127.0.0.1.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)

HOST = "http://127.0.0.1:11434"
MIN_RAM_GB = 7.5  # the 3B model needs about 3 GB while it answers, beside the browser, FFmpeg and the speech model
MIN_THREADS = 4
MIN_FREE_GB = 8.0
_seen: dict = {"at": 0.0, "value": None}


class LocalAiError(RuntimeError):
    pass


def _ram_gb() -> float:
    if sys.platform == "win32":
        import ctypes

        class Mem(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                        ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong), ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]

        m = Mem()
        m.length = ctypes.sizeof(Mem)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.total / 1e9
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (ValueError, OSError, AttributeError):
        return 0.0


def supported(cfg: Config) -> tuple[bool, str]:
    """(can this computer run the local model, why not). A graphics card makes it fast; a processor alone is enough."""
    from .work import free_gb, read_json

    if cfg.llm.local == "off":
        return False, "it is switched off in the settings file (the line local: off under llm:)"
    ram, threads = _ram_gb(), os.cpu_count() or 1
    vram = int((read_json(cfg.data_dir / "hardware.json", {}) or {}).get("vram_mb") or 0)
    if ram < MIN_RAM_GB and vram < 4000:
        return False, f"this computer has {ram:.0f} GB of memory; the local model needs about 8 GB"
    if threads < MIN_THREADS and vram < 4000:
        return False, f"this computer's processor has {threads} threads; the local model needs at least {MIN_THREADS} to answer in reasonable time"
    return True, ""


def facts(cfg: Config) -> str:
    """What was measured to decide, in words, so the page can show the person why: "16 GB of memory, 8 processor threads"."""
    from .work import read_json

    vram = int((read_json(cfg.data_dir / "hardware.json", {}) or {}).get("vram_mb") or 0)
    card = f", an NVIDIA card with {vram / 1024:.0f} GB" if vram else ""
    shown = _ram_gb() * 1e9 / 2**30   # counted the way Windows does: a "16 GB" PC shows the 15.x GB Windows calls usable, not 17
    return f"{shown:.1f} GB of memory, {os.cpu_count() or 1} processor threads{card}"


def program() -> str:
    """Where the Ollama program is, or ""."""
    found = shutil.which("ollama")
    if found:
        return found
    usual = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(usual) if usual.exists() else ""


def _tags(timeout: float = 2.0) -> list[str] | None:
    """The models Ollama has, or None when it is not running."""
    try:
        with urllib.request.urlopen(HOST + "/api/tags", timeout=timeout) as r:
            return [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
    except (OSError, ValueError):
        return None


def status(cfg: Config, fresh: bool = False) -> dict:
    """Everything the page shows about the local AI. Remembered for half a minute: it is asked on every refresh."""
    if not fresh and _seen["value"] is not None and time.time() - _seen["at"] < 30:
        return _seen["value"]
    ok, why = supported(cfg)
    tags = _tags()
    model = cfg.llm.local_model
    have = bool(tags) and any(t == model or t.split(":")[0] == model.split(":")[0] and ":" not in model for t in tags)
    out = {"supported": ok, "why": why, "facts": facts(cfg), "installed": bool(program()), "running": tags is not None, "model": model, "has_model": have,
           "ready": tags is not None and have and cfg.llm.local != "off"}
    _seen.update(at=time.time(), value=out)
    return out


def start(cfg: Config, wait_s: float = 20.0) -> bool:
    """Start Ollama if it is installed and not running. True when it answers."""
    if _tags() is not None:
        return True
    exe = program()
    if not exe:
        return False
    env = dict(os.environ)
    # Models are big: they go under the data folder (D:), not into the user's profile on C:.
    env.setdefault("OLLAMA_MODELS", str(cfg.data_dir / "models" / "ollama"))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen([exe, "serve"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    end = time.time() + wait_s
    while time.time() < end:
        if _tags(1.0) is not None:
            _seen["value"] = None
            return True
        time.sleep(1)
    return False


def ready(cfg: Config) -> bool:
    """Can a question be asked right now? Starts Ollama when it is installed with the model but not running."""
    s = status(cfg)
    if s["ready"]:
        return True
    if cfg.llm.local == "off" or not s["installed"] or s["running"]:
        return False
    return start(cfg) and status(cfg, fresh=True)["ready"]


def why_not(cfg: Config) -> str:
    s = status(cfg)
    if s["ready"]:
        return ""
    if not s["supported"]:
        return f"the free AI is not used on this computer: {s['why']}"
    if not s["installed"]:
        return "the free AI is not set up (open Settings and press 'Set up the free AI' under AI)"
    if not s["has_model"]:
        return f"the free AI has no model yet (press 'Set up the free AI' to download {s['model']})"
    return "the free AI is installed but not answering"


def ask(cfg: Config, system: str, user: str, schema: dict, what: str) -> dict:
    """One question to the local model, answered as JSON in the given shape. Raises LocalAiError with the reason."""
    if not ready(cfg):
        raise LocalAiError(why_not(cfg))
    body = json.dumps({
        "model": cfg.llm.local_model, "stream": False, "format": schema, "options": {"temperature": 0.4, "num_ctx": 8192},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(HOST + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            reply = json.loads(r.read())
        data = json.loads(reply["message"]["content"])
    except urllib.error.HTTPError as e:
        raise LocalAiError(f"the local AI refused the question ({e.code}: {e.read()[:200].decode('utf-8', 'replace')})") from e
    except (OSError, ValueError, KeyError) as e:
        raise LocalAiError(f"the local AI did not give a usable answer ({type(e).__name__}: {e})") from e
    if not isinstance(data, dict):
        raise LocalAiError("the local AI answered in the wrong shape")
    log.info("free AI (%s) answered in %.0fs: %s", cfg.llm.local_model, time.monotonic() - t0, what)
    return data


def setup(cfg: Config, say, step=None) -> None:
    """Install Ollama if it is missing and download the model. Only on a computer that can run it."""
    from .work import free_gb

    ok, why = supported(cfg)
    if not ok:
        raise ValueError(f"The free AI is not set up on this computer: {why}. Nothing was downloaded.")
    if free_gb(cfg.data_dir) < MIN_FREE_GB:
        raise ValueError(f"less than {MIN_FREE_GB:.0f} GB free on {Path(cfg.data_dir).anchor}; the model needs about 2 GB there. Nothing was downloaded.")
    note = step or (lambda i, n, text: say(text))
    if not program():
        if sys.platform != "win32" or not shutil.which("winget"):
            raise ValueError("Ollama is not installed and cannot be installed from here. Install it from https://ollama.com, then press this again.")
        note(1, 3, "installing Ollama, the program that runs the model (about 1 GB, from its own publisher through Windows' installer)")
        proc = subprocess.Popen(["winget", "install", "--id", "Ollama.Ollama", "-e", "--silent", "--accept-package-agreements", "--accept-source-agreements"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, errors="replace")
        for line in proc.stdout:
            if line.strip() and not set(line.strip()) <= set("-\\|/ █▒"):
                say(line.rstrip()[-200:])
        if proc.wait() != 0 or not program():
            raise ValueError("Ollama could not be installed (the lines above say why). Install it from https://ollama.com, then press this again.")
    note(2, 3, "starting the local AI")
    if not start(cfg, wait_s=40):
        raise ValueError("Ollama is installed but did not start. Start it from the Start menu, then press this again.")
    if not status(cfg, fresh=True)["has_model"]:
        note(3, 3, f"downloading the model {cfg.llm.local_model} (about 2 GB)")
        req = urllib.request.Request(HOST + "/api/pull", data=json.dumps({"model": cfg.llm.local_model, "stream": True}).encode(), headers={"Content-Type": "application/json"})
        last = ""
        with urllib.request.urlopen(req, timeout=3600) as r:
            for raw in r:
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if row.get("error"):
                    raise ValueError(f"the model could not be downloaded: {row['error']}")
                pct = f" {100 * row['completed'] // row['total']}%" if row.get("total") and row.get("completed") else ""
                line = f"{row.get('status', '')}{pct}"
                if line != last and (not pct or int(pct.strip(' %')) % 5 == 0):
                    last = line
                    say(line)
    if not status(cfg, fresh=True)["ready"]:
        raise ValueError("the model was downloaded but the local AI is still not ready; press 'Check again' in a minute")
    say("the free AI is ready. It is used when Claude cannot be reached.")
