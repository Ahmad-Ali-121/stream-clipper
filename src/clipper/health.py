"""Is this computer ready? One list of checks, each with a plain verdict and the exact fix.

Quick checks look at what is on disk and cost nothing, so the page can ask often.
The full check also really loads the speech model (in a separate process, so a crash is caught)
and asks GitHub whether a newer version exists; its answers are kept in <data_dir>/health.json.

Each check is {"id", "name", "state", "detail", "fix"} where state is:
  "ok"    fine
  "warn"  works, but worth knowing (an optional part is missing, disk is getting full)
  "bad"   something will fail until this is fixed
  "todo"  not checked yet (needs the full check)
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

from .config import PROJECT_ROOT, Config
from .work import free_gb, read_json, write_json

log = logging.getLogger(__name__)

RERUN_SETUP = "Press Repair below. It downloads what is missing (it is the setup step, run again)."


def _item(id_: str, name: str, state: str, detail: str = "", fix: str = "") -> dict:
    return {"id": id_, "name": name, "state": state, "detail": detail, "fix": fix if state in ("bad", "warn", "todo") else ""}


def _git(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(PROJECT_ROOT), capture_output=True, text=True, errors="replace",
                          stdin=subprocess.DEVNULL, timeout=timeout)


def update_state(fetch: bool = False) -> dict:
    """Whether a newer version is on GitHub: {"can": bool, "behind": int, "why": str}.

    Only a plain fast-forward from the ``origin`` remote counts. A copy with its own changes or
    no such remote (a developer's copy) is left alone and says why.
    """
    try:
        if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
            return {"can": False, "behind": 0, "why": "this copy was not installed with git, so it cannot update itself"}
        if _git("remote", "get-url", "origin").returncode != 0:
            return {"can": False, "behind": 0, "why": "this copy has no 'origin' to update from (a developer's copy)"}
        if fetch and (f := _git("fetch", "origin", "--quiet", timeout=120)).returncode != 0:
            return {"can": False, "behind": 0, "why": f"GitHub could not be reached ({f.stderr.strip()[-200:] or 'no reason given'})"}
        target = "origin/main"
        if _git("rev-parse", "--verify", "--quiet", target).returncode != 0:
            return {"can": False, "behind": 0, "why": "no version from GitHub is known yet; run the full check"}
        behind = int(_git("rev-list", "--count", f"HEAD..{target}").stdout.strip() or 0)
        if behind == 0:
            return {"can": False, "behind": 0, "why": ""}
        if _git("merge-base", "--is-ancestor", "HEAD", target).returncode != 0:
            return {"can": False, "behind": behind, "why": "this copy and GitHub have gone separate ways. In a terminal in this folder run:  "
                                                         "git fetch origin   then   git reset --hard origin/main"}
        if _git("status", "--porcelain", "--untracked-files=no").stdout.strip():
            return {"can": False, "behind": behind, "why": "files of the program were changed on this computer; undo those changes before updating"}
        return {"can": True, "behind": behind, "why": ""}
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return {"can": False, "behind": 0, "why": f"git could not be used ({e})"}


def check_update(cfg: Config) -> dict:
    """Ask GitHub for the newest version (a few seconds, nothing else is checked) and remember the answer."""
    path = cfg.data_dir / "health.json"
    state = update_state(fetch=True)
    saved = read_json(path, {}) or {}
    saved["update"], saved["update_at"] = state, time.time()
    write_json(path, saved)
    return state


def quick_checks(cfg: Config) -> list[dict]:
    from . import hw, llm
    from .tools import ToolError, find_tool

    out: list[dict] = []
    for tool, what in (("ffmpeg", "FFmpeg (cuts and renders video)"), ("TwitchDownloaderCLI", "TwitchDownloader (fetches clip ranges and chat)")):
        try:
            out.append(_item(tool, what, "ok", find_tool(tool, cfg.data_dir)))
        except ToolError:
            out.append(_item(tool, what, "bad", f"not found in {cfg.data_dir / 'tools'}", RERUN_SETUP))
    models = cfg.data_dir / "models"
    face = models / "face_detection_yunet_2023mar.onnx"
    out.append(_item("face", "Face detection model (frames the clip)", "ok" if face.exists() else "bad",
                     "" if face.exists() else f"missing from {models}", RERUN_SETUP))
    voice = [models / "kokoro" / n for n in ("voices-v1.0.bin", "kokoro-v1.0.int8.onnx")]
    out.append(_item("voice", "Narrator voice (explainer videos)", "ok" if all(v.exists() for v in voice) else "warn",
                     "" if all(v.exists() for v in voice) else "missing; only the explainer and top-moments videos need it", RERUN_SETUP))
    problem = hw.runtime_problem()
    v = hw.vc_runtime_version()
    out.append(_item("runtime", "Windows C++ runtime (captions need it)", "bad" if problem else "ok",
                     problem or (".".join(map(str, v)) if v else ""),
                     "Install it from https://aka.ms/vs/17/release/vc_redist.x64.exe (or run: winget install Microsoft.VCRedist.2015+.x64), then restart the computer."))
    try:
        gb = free_gb(cfg.data_dir)
        state = "bad" if gb < 2 else ("warn" if gb < 8 else "ok")
        out.append(_item("disk", "Free disk space for clips", state, f"{gb:.0f} GB free on {Path(cfg.data_dir).anchor}",
                         "Delete rejected clips (Rejected list, 'Delete all rejected'), or old folders under the data folder's 'work'."))
    except OSError as e:
        out.append(_item("disk", "Free disk space for clips", "bad", f"the data folder {cfg.data_dir} cannot be read ({e})",
                         "Check that the drive is connected, or run the setup step again with another -DataDir."))
    saved = read_json(cfg.data_dir / "health.json", {}) or {}
    speech = saved.get("speech") or {}
    if speech.get("ok") is True:
        out.append(_item("speech", "Captions (speech-to-text really runs)", "ok", speech.get("detail", "")))
    elif speech.get("ok") is False:
        out.append(_item("speech", "Captions (speech-to-text really runs)", "bad", speech.get("detail", ""),
                         "Fix the red items above first, then press 'Run the full check'. If it still fails, run:  uv run clipper doctor"))
    else:
        out.append(_item("speech", "Captions (speech-to-text really runs)", "todo", "not tried yet on this computer",
                         "Press 'Run the full check'. The first time it downloads the speech model (about 1.6 GB)."))
    hwinfo = read_json(cfg.data_dir / "hardware.json", {}) or {}
    if hwinfo:
        speech_on = "the NVIDIA card" if hwinfo.get("whisper_device") == "cuda" else "the processor"
        out.append(_item("hardware", "Graphics and speed", "ok", f"video encoder {hwinfo.get('encoder')}; speech-to-text on {speech_on}"
                         + (f" (card not usable: {str(hwinfo['cuda_problem']).splitlines()[0][:120]})" if hwinfo.get("cuda_problem") else "")))
    ai_problem = llm.why_unavailable(cfg)
    checks = llm.read_checks(cfg)
    works = [llm.BACKENDS[b] for b in llm.BACKENDS if checks[b]["ok"] is True]
    if ai_problem:
        out.append(_item("ai", "Claude (optional: writes text, reads streams)", "warn", ai_problem,
                         "Optional. Either sign in to Claude Code, or put an API key in the .env file. See the Claude box."))
    elif works:
        out.append(_item("ai", "Claude (optional: writes text, reads streams)", "ok", "works: " + " and ".join(works)))
    else:
        broken = "; ".join(f"{llm.BACKENDS[b]}: {checks[b]['problem']}" for b in llm.BACKENDS if checks[b]["ok"] is False)
        out.append(_item("ai", "Claude (optional: writes text, reads streams)", "warn" if broken else "todo",
                         broken or "set up, not tried yet", "Open the Claude box and press 'Check again'."))
    upd = saved.get("update") or {}
    if upd.get("behind"):
        out.append(_item("update", "Program version", "warn", f"a newer version is available ({upd['behind']} change(s) behind)" + (f". {upd['why']}" if upd.get("why") else ""),
                         "Press Update below." if upd.get("can") else upd.get("why", "")))
    elif upd:
        out.append(_item("update", "Program version", "ok", upd.get("why") or "up to date"))
    else:
        out.append(_item("update", "Program version", "todo", "not compared with GitHub yet", "It is compared by itself within a minute of starting."))
    return out


def summary(items: list[dict]) -> dict:
    bad = sum(1 for i in items if i["state"] == "bad")
    warn = sum(1 for i in items if i["state"] == "warn")
    todo = sum(1 for i in items if i["state"] == "todo")
    text = (f"{bad} thing(s) to fix" if bad else ("Not fully checked yet" if todo else ("Ready, with notes" if warn else "Everything is ready")))
    return {"bad": bad, "warn": warn, "todo": todo, "text": text}


def full_check(cfg: Config) -> None:
    """The slow part: really load the speech model, and ask GitHub for the newest version. Results go to health.json."""
    from . import hw

    path = cfg.data_dir / "health.json"
    saved = read_json(path, {}) or {}
    hw.detect(cfg)  # fills hardware.json on a computer that never ran it
    device, compute_type = hw.whisper_device(cfg)
    models = cfg.data_dir / "models"
    models.mkdir(parents=True, exist_ok=True)
    if device == "cuda" and cfg.transcribe.device == "auto":
        device, compute_type = hw.confirm_cuda(cfg, cfg.transcribe.model, compute_type, str(models))
    log.info("full check: loading the speech model (%s, %s) in a separate process", device, compute_type)
    ok, why = hw.trial_load(cfg.transcribe.model, device, compute_type, str(models))
    saved["speech"] = {"ok": ok, "at": time.time(),
                       "detail": f"works on {'the NVIDIA card' if device == 'cuda' else 'the processor'}" if ok else why.splitlines()[0][:400]}
    saved["update"] = update_state(fetch=True)
    saved["checked_at"] = time.time()
    saved["python"] = sys.version.split()[0]
    write_json(path, saved)
    log.info("full check done: speech %s; update %s", "works" if ok else "FAILS", saved["update"])
