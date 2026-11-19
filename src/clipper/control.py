"""The options panel on the local review page: what to watch, how, and which versions to make.

Everything here runs on this machine only. Options are saved to
``config.local.yaml`` (the personal override file beside config.yaml), the
watcher is the same ``clipper watch`` command run as a child process, and
"finish" is the same ``clipper finish`` step. Nothing is posted anywhere.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from .config import PROJECT_ROOT, Config, load_config
from .signals import MODE_TEXT, parse_mode

log = logging.getLogger(__name__)

LOCAL_FILE = "config.local.yaml"

# Caption looks offered on the page: name -> (label, the caption settings it sets). Fonts are ones every Windows has.
CAPTION_PRESETS = {
    "bold": ("Bold white, yellow highlight", dict(font="Arial Black", font_size=88, text_color="FFFFFF", active_color="FFE600", outline=6, uppercase=True, max_words=3)),
    "impact": ("Impact, green highlight", dict(font="Impact", font_size=100, text_color="FFFFFF", active_color="3CFF6B", outline=7, uppercase=True, max_words=2)),
    "yellow": ("Big yellow, white highlight", dict(font="Arial Black", font_size=98, text_color="FFE600", active_color="FFFFFF", outline=8, uppercase=True, max_words=2)),
    "clean": ("Clean sentence case, blue highlight", dict(font="Segoe UI Black", font_size=76, text_color="FFFFFF", active_color="5CC8FF", outline=4, uppercase=False, max_words=4)),
}


def save_local(values: dict[str, dict[str, Any]], folder: Path = PROJECT_ROOT) -> None:
    """Merge ``{section: {key: value}}`` into config.local.yaml, keeping whatever else is in it."""
    path = folder / LOCAL_FILE
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}
    for section, items in values.items():
        data.setdefault(section, {}).update(items)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    tmp.replace(path)


def clean_options(raw: dict) -> dict[str, dict[str, Any]]:
    """Validate options sent by the page and shape them as config sections. Unknown keys are an error."""
    out: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if key == "mode":
            out.setdefault("watch", {})["mode"] = parse_mode(str(value))
        elif key in ("ab_versions", "opening"):
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be on or off")
            out.setdefault("render", {})[key] = value
        elif key == "speech_language":
            if value not in ("en", "ur", "hi", "auto"):
                raise ValueError("the stream language must be en, ur, hi or auto")
            out.setdefault("transcribe", {})["language"] = value
        elif key == "caption_script":
            if value not in ("native", "roman"):
                raise ValueError("captions must be native or roman")
            out.setdefault("captions", {})["script"] = value
        elif key == "text_language":
            from .llm import TEXT_LANGUAGES

            if value not in TEXT_LANGUAGES:
                raise ValueError(f"the text language must be one of: {', '.join(TEXT_LANGUAGES)}")
            out.setdefault("llm", {})["text_language"] = value
        elif key == "campaign":
            value = str(value or "")
            if value and not (PROJECT_ROOT / "briefs" / f"{value}.yaml").is_file():
                raise ValueError(f"there is no campaign file briefs/{value}.yaml")
            out.setdefault("render", {})["campaign"] = value
        elif key == "caption_preset":
            if value not in CAPTION_PRESETS:
                raise ValueError(f"unknown caption style {value!r}")
            out.setdefault("captions", {}).update(preset=value, **CAPTION_PRESETS[value][1])
        elif key == "llm_backend":
            from .llm import CHOICES

            if value not in CHOICES:
                raise ValueError(f"the AI connection must be one of: {', '.join(CHOICES)}")
            out.setdefault("llm", {})["backend"] = value
        elif key == "llm_prefer":
            from .llm import BACKENDS

            if value not in BACKENDS:
                raise ValueError(f"the preferred connection must be one of: {', '.join(BACKENDS)}")
            out.setdefault("llm", {})["prefer"] = value
        elif key == "llm_fallback":
            if not isinstance(value, bool):
                raise ValueError("llm_fallback must be on or off")
            out.setdefault("llm", {})["fallback"] = value
        elif key in ("llm_model", "ai_model"):
            from .llm import PRICES

            if value not in PRICES:
                raise ValueError(f"unknown model {value!r}; choose one of: {', '.join(PRICES)}")
            out.setdefault("llm" if key == "llm_model" else "ai", {})["model"] = value
        elif key == "channels":
            from .watch import channel_name

            names = [channel_name(c) for c in str(value).replace(",", " ").split()]
            out.setdefault("watch", {})["channels"] = names
        else:
            raise ValueError(f"unknown option: {key}")
    return out


def campaigns() -> list[dict]:
    """The campaign rule files in briefs/, each with a short plain summary of its rules; a broken file says why."""
    from .brief import load_brief

    out = []
    for f in sorted((PROJECT_ROOT / "briefs").glob("*.yaml")):
        try:
            b = load_brief(f)
            rules = [f"{b.min_duration_s:g} to {b.max_duration_s:g} s" if b.max_duration_s else (f"at least {b.min_duration_s:g} s" if b.min_duration_s else "")]
            rules += [f"needs {' '.join(b.required_tags)}"] if b.required_tags else []
            rules += ["on-screen ad label"] if b.require_disclosure_overlay else []
            rules += [f"{len(b.manual_checks)} thing(s) to check by hand"] if b.manual_checks else []
            out.append({"id": f.stem, "name": b.name, "rules": "; ".join(r for r in rules if r), "problem": ""})
        except Exception as e:
            out.append({"id": f.stem, "name": f.stem, "rules": "", "problem": str(e)})
    return out


class Control:
    """State behind the panel: the watcher process and the last finish run."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.log_path = cfg.data_dir / "watch.log"
        self.finish_lines: list[str] = []
        self.finishing = False
        self.finish_progress = {"done": 0, "total": 0, "now": ""}
        self.watch_problem = ""  # why the watcher is not running although nobody stopped it
        self.run_proc: subprocess.Popen | None = None  # "analyse a finished stream", the same as `clipper run URL`
        self.run_log_path = cfg.data_dir / "run.log"
        self.run_problem = ""
        self.maint = {"name": "", "running": False, "lines": []}  # the one maintenance job: full check, repair or update
        self.health_started = False
        self.update_asked = 0.0  # when GitHub was last asked for a newer version
        # Jobs started from the page say how far they are in this file (see progress.py); the page shows it as a percentage.
        self.progress_path = cfg.data_dir / "progress.json"
        self.finish_progress_path = cfg.data_dir / "progress-finish.json"
        os.environ["CLIPPER_PROGRESS"] = str(self.progress_path)
        self.exit_code = 0  # 42 asks the launcher to start the program again (after an update)
        self.shutdown = None  # set by the server: stops it so the program can exit
        self.ai_checking = False
        self.ai_checked = False  # an automatic check was already started in this session
        self.watch_seen = False
        self.run_seen = False  # a run was started from this page, so its log is worth showing
        self._lock = threading.Lock()

    def reload(self) -> Config:
        self.cfg = load_config()
        return self.cfg

    # ---- options ----

    def status(self) -> dict:
        cfg = self.cfg
        self._notice_death()
        tail: list[str] = []
        if self.watch_seen and self.log_path.exists():  # a log left by an earlier session is not this session's news
            tail = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        return {
            "mode": parse_mode(cfg.watch.mode), "modes": MODE_TEXT, "channels": " ".join(cfg.watch.channels),
            "ab_versions": cfg.render.ab_versions, "opening": cfg.render.opening,
            "campaign": cfg.render.campaign, "campaigns": campaigns(),
            "speech_language": cfg.transcribe.language, "caption_script": cfg.captions.script, "text_language": cfg.llm.text_language,
            "caption_preset": cfg.captions.preset,
            "caption_presets": {k: {"label": v[0], **v[1]} for k, v in CAPTION_PRESETS.items()},
            "finish_progress": self.finish_progress,
            "watching": self.watching(), "watch_log": tail, "watch_problem": self.watch_problem,
            "finishing": self.finishing, "finish_log": self.finish_lines[-200:],
            "running": self.running(), "run_log": self._run_tail(), "run_problem": self.run_problem,
            "run_progress": self._progress(self.progress_path, self.running()),
            "finish_percent": self._progress(self.finish_progress_path, self.finishing),
            "ai": self._ai_status(),
            "health": self._health_status(),
        }

    def _ai_status(self) -> dict:
        from . import llm

        cfg = self.cfg
        checks = llm.read_checks(cfg)
        ways = {b: {"name": name, "missing": llm.static_problem(cfg, b), **checks[b]} for b, name in llm.BACKENDS.items()}
        order = llm.candidates(cfg) if not llm.why_unavailable(cfg) else []
        if not self.ai_checking and not self.ai_checked and any(not w["missing"] and not w["fresh"] for w in ways.values()):
            self.start_ai_check()  # nothing recent is known about a connection that could work: find out, once
        return {"backend": cfg.llm.backend, "backends": llm.CHOICES, "prefer": cfg.llm.prefer, "fallback": cfg.llm.fallback,
                "model": cfg.llm.model, "stream_model": cfg.ai.model, "models": list(llm.PRICES),
                "problem": llm.why_unavailable(cfg), "ways": ways, "using": order[0] if order else "", "note": checks["note"],
                "checking": self.ai_checking}

    def start_ai_check(self) -> dict:
        """Really try each connection that is set up, in the background."""
        from . import llm

        with self._lock:
            if self.ai_checking:
                return {"checking": True}
            self.ai_checking = self.ai_checked = True

        def work() -> None:
            try:
                cfg = self.reload()
                llm._remember(cfg, note="")
                for b in llm.BACKENDS:
                    if not llm.static_problem(cfg, b):
                        llm.check(cfg, b)
            except Exception:
                log.exception("the Claude connection check failed")
            finally:
                self.ai_checking = False

        threading.Thread(target=work, daemon=True, name="ai-check").start()
        return {"checking": True}

    # ---- is this computer ready? ----

    def _health_status(self) -> dict:
        from . import health
        from .work import read_json

        try:
            items = health.quick_checks(self.cfg)
        except Exception as e:  # the check list must never take the page down with it
            log.exception("the setup check failed")
            items = [{"id": "check", "name": "The setup check itself", "state": "bad", "detail": str(e), "fix": "Report this line."}]
        saved = read_json(self.cfg.data_dir / "health.json", {}) or {}
        stale = time.time() - float(saved.get("checked_at") or 0) > 24 * 3600
        if stale and not self.health_started and not self.maint["running"]:
            self.health_started = True
            self.start_full_check()  # once per session, when nothing recent is known
        # GitHub is asked at the start and then every half hour, so the Update button appears by itself soon after a
        # new version is published. It used to be asked once a day, with the full check.
        if time.time() - self.update_asked > 1800 and not self.maint["running"]:
            self.update_asked = time.time()

            def ask() -> None:
                from . import health as h

                try:
                    state = h.check_update(self.cfg)
                    if state.get("behind"):
                        log.info("a newer version is on GitHub: %s change(s) behind%s", state["behind"], f" ({state['why']})" if state.get("why") else "")
                except Exception as e:
                    log.error("GitHub could not be asked for a newer version: %s", e, exc_info=True)

            threading.Thread(target=ask, daemon=True).start()
        upd = saved.get("update") or {}
        return {"items": items, "summary": health.summary(items), "maint": {**self.maint, "lines": self.maint["lines"][-200:]},
                "can_update": bool(upd.get("can")), "launcher": bool(os.environ.get("CLIPPER_LAUNCHER"))}

    def _maintain(self, name: str, work) -> dict:
        with self._lock:
            if self.maint["running"]:
                raise ValueError(f"'{self.maint['name']}' is still running; wait for it to finish")
            self.maint = {"name": name, "running": True, "lines": [f"{time.strftime('%H:%M:%S')} {name} started"], "step": None}

        def say(line: str) -> None:
            self.maint["lines"].append(line)

        def step(index: int, count: int, text: str) -> None:  # step number (from 1) of how many, and what it is
            self.maint["step"] = [index, count, text]
            say(f"step {index} of {count}: {text}")

        say.step = step

        def run() -> None:
            try:
                work(say)
                self.maint["lines"].append(f"{time.strftime('%H:%M:%S')} {name} finished")
            except Exception as e:
                import traceback

                log.exception("%s failed", name)
                self.maint["lines"] += [f"FAILED: {name}: {type(e).__name__}: {e}"] + traceback.format_exc().splitlines()
            finally:
                self.maint["running"] = False

        threading.Thread(target=run, daemon=True, name=name).start()
        return self.status()

    @staticmethod
    def _run_logged(cmd: list[str], say, cwd: str) -> int:
        """Run a command, passing every line it prints to ``say``. Returns its exit code."""
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                text=True, errors="replace")
        for line in proc.stdout:
            if line.strip():
                say(line.rstrip())
        return proc.wait()

    def start_full_check(self) -> dict:
        from . import health

        def work(say) -> None:
            say.step(1, 2, "loading the speech model in a separate process (the slow part)")
            health.full_check(self.reload(), step=lambda text: say.step(2, 2, text))

        return self._maintain("Full check", work)

    def start_repair(self) -> dict:
        """Run the setup step again: it downloads whatever is missing and leaves the rest alone."""
        def work(say) -> None:
            if sys.platform != "win32":
                raise ValueError("Repair runs setup.ps1, which is for Windows")
            code = self._run_logged(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PROJECT_ROOT / "setup.ps1"),
                                     "-DataDir", str(self.cfg.data_dir), "-NoShortcut"], say, str(PROJECT_ROOT))
            if code != 0:
                raise ValueError(f"the setup step ended with exit code {code}; the lines above say why")

        return self._maintain("Repair", work)

    def start_update(self) -> dict:
        """Take the newest version from GitHub (fast-forward only), install what it needs, and restart if the launcher can."""
        from . import health

        def work(say) -> None:
            say.step(1, 4, "asking GitHub what is new")
            state = health.update_state(fetch=True)
            if not state["can"]:
                # Not a failure: there is simply nothing to do, or this copy must not be changed. Say so plainly.
                say("No update was made: " + (state["why"] or "this copy is already the newest version") + ".")
                health.full_check(self.reload())
                return
            say(f"{state['behind']} change(s) to take from GitHub")
            for n, cmd in enumerate((["git", "merge", "--ff-only", "origin/main"],
                                     ["uv", "sync", "--frozen"] + (["--extra", "gpu"] if (PROJECT_ROOT / ".venv/Lib/site-packages/nvidia/cublas").exists() else []))):
                say.step(n + 2, 4, ("taking the new version", "installing what it needs")[n])
                say("> " + " ".join(cmd))
                if (code := self._run_logged(cmd, say, str(PROJECT_ROOT))) != 0:
                    raise ValueError(f"'{' '.join(cmd)}' ended with exit code {code}; the lines above say why")
            say.step(4, 4, "checking that everything still works")
            health.full_check(self.reload())
            if os.environ.get("CLIPPER_LAUNCHER") and self.shutdown:
                say("updated. Restarting the program now; this page reconnects by itself.")
                self.exit_code = 42
                threading.Timer(1.5, self.shutdown).start()
            else:
                say("updated. Now close the program (Ctrl+C in its terminal) and start it again.")

        return self._maintain("Update", work)

    def restart(self) -> dict:
        """Start the program again (only possible when it was started with the launcher, which waits for this)."""
        if not (os.environ.get("CLIPPER_LAUNCHER") and self.shutdown):
            raise ValueError("this copy was started from a terminal, so it cannot restart itself. Press Ctrl+C there and start it again, "
                             "or start it with Stream-Clipper.cmd next time")
        if self.watching() or self.running() or self.finishing or self.maint["running"]:
            raise ValueError("something is still running (watching, a finished stream, making videos or a check). Stop it or wait, then restart")
        self.exit_code = 42
        threading.Timer(0.8, self.shutdown).start()
        return {"restarting": True}

    def save(self, raw: dict) -> dict:
        save_local(clean_options(raw))
        self.reload()
        return self.status()

    # ---- watcher ----

    def watching(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _notice_death(self) -> None:
        """A watcher that ended without Stop being pressed is an error: say so in its log and on the page."""
        with self._lock:
            if self.proc is None or self.proc.poll() is None:
                return
            from .hw import describe_exit

            code = self.proc.returncode
            self.proc = None
            self.watch_problem = f"THE WATCHER STOPPED BY ITSELF at {time.strftime('%H:%M:%S')}: {describe_exit(code)}. The lines above it say why."
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(self.watch_problem + "\n")
            log.error(self.watch_problem)

    def start_watch(self, raw: dict) -> dict:
        opts = clean_options({k: raw[k] for k in ("mode", "channels") if k in raw})
        names = opts.get("watch", {}).get("channels") or list(self.cfg.watch.channels)
        if not names:
            raise ValueError("type at least one channel name")
        save_local(opts)
        cfg = self.reload()
        with self._lock:
            if self.watching():
                raise ValueError("already watching; stop it first")
            self.watch_problem, self.watch_seen = "", True
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            out = self.log_path.open("w", encoding="utf-8")
            out.write(f"{time.strftime('%H:%M:%S')} starting: {' '.join(names)} ({cfg.watch.mode})\n")
            out.flush()
            self.proc = subprocess.Popen(
                # -u: unbuffered, so every line reaches the log the moment it is printed.
                [sys.executable, "-u", "-X", "faulthandler", "-c", "from clipper.cli import main; raise SystemExit(main())", "watch", *names, "--mode", cfg.watch.mode],
                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(PROJECT_ROOT),
            )
        return self.status()

    def stop_watch(self) -> dict:
        with self._lock:
            if self.watching():
                if sys.platform == "win32":
                    # The watcher has children of its own (FFmpeg reading the live feed); end the whole tree.
                    subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)
                else:
                    self.proc.terminate()
                try:
                    self.proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                self.proc = None  # stopped on purpose: not a death to report
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"{time.strftime('%H:%M:%S')} stopped\n")
        return self.status()

    # ---- a finished stream ----

    def _new_job(self) -> None:
        """Forget the last job's percentage, so a new job never starts by showing the old one's."""
        self.progress_path.unlink(missing_ok=True)

    def _progress(self, path: Path, active: bool) -> dict | None:
        from . import progress

        return progress.read(path) if active else None

    def running(self) -> bool:
        return self.run_proc is not None and self.run_proc.poll() is None

    def _run_tail(self) -> list[str]:
        if not self.run_seen or not self.run_log_path.exists():
            return []
        with self._lock:
            if self.run_proc is not None and self.run_proc.poll() is not None:
                code, self.run_proc = self.run_proc.returncode, None
                if code != 0:
                    from .hw import describe_exit

                    self.run_problem = f"ANALYSING THE FINISHED STREAM FAILED at {time.strftime('%H:%M:%S')}: {describe_exit(code)}. The lines above it say why."
                    with self.run_log_path.open("a", encoding="utf-8") as fh:
                        fh.write(self.run_problem + "\n")
                    log.error(self.run_problem)
        return self.run_log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]

    def start_run(self, raw: dict) -> dict:
        from .sources import parse_source

        source = parse_source(str(raw.get("url") or ""))  # a clear ValueError for anything that is not a video link
        top = int(raw.get("top") or 8)
        signals = parse_mode(str(raw.get("signals") or "chat"))
        if not 1 <= top <= 30:
            raise ValueError("the number of clips must be between 1 and 30")
        with self._lock:
            if self.running():
                raise ValueError("a finished stream is already being analysed; wait for it to end")
            self.run_problem, self.run_seen = "", True
            self.run_log_path.parent.mkdir(parents=True, exist_ok=True)
            out = self.run_log_path.open("w", encoding="utf-8")
            out.write(f"{time.strftime('%H:%M:%S')} analysing {source.url}: the best {top} moments, found by {signals}{' + AI reading the speech' if raw.get('ai') else ''}\n")
            out.flush()
            self._new_job()
            self.run_proc = subprocess.Popen(
                [sys.executable, "-u", "-X", "faulthandler", "-c", "from clipper.cli import main; raise SystemExit(main())",
                 "run", source.url, "--top", str(top), "--signals", signals] + (["--ai"] if raw.get("ai") else []),
                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(PROJECT_ROOT),
            )
        return self.status()

    def _start_job(self, cmd: list[str], first_line: str) -> dict:
        with self._lock:
            if self.running():
                raise ValueError("another job is still running in this box; wait for it to end or press Stop")
            self.run_problem, self.run_seen = "", True
            self.run_log_path.parent.mkdir(parents=True, exist_ok=True)
            out = self.run_log_path.open("w", encoding="utf-8")
            out.write(f"{time.strftime('%H:%M:%S')} {first_line}\n")
            out.flush()
            self._new_job()
            self.run_proc = subprocess.Popen(
                [sys.executable, "-u", "-X", "faulthandler", "-c", "from clipper.cli import main; raise SystemExit(main())", *cmd],
                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(PROJECT_ROOT),
            )
        return self.status()

    def start_range(self, raw: dict) -> dict:
        """Download one chosen part of a video for the cutting board (the same as ``clipper range``)."""
        from .pipeline import MIN_PART_S, parse_clock
        from .sources import parse_source

        source = parse_source(str(raw.get("source") or ""))
        start = parse_clock(raw.get("start") or 0)
        end = None if raw.get("to_end") else parse_clock(raw.get("end") or 0)
        if end is not None:
            if end <= start:
                raise ValueError("the end time must be after the start time")
            if end - start < MIN_PART_S:
                raise ValueError(f"this part is shorter than {MIN_PART_S:g} seconds, too short for a clip")
            if end - start > self.cfg.fetch.max_range_minutes * 60 + 1:
                raise ValueError(f"the cutting board takes at most {self.cfg.fetch.max_range_minutes:g} minutes at a time; choose a shorter part, or do it in two goes")
        cmd = ["range", source.url, "--start", f"{start:g}"] + (["--end", f"{end:g}"] if end is not None else [])
        return self._start_job(cmd, f"downloading the part you chose from {source.url}")

    def start_range_cut(self, raw: dict) -> dict:
        """Make clips from the pieces marked on the cutting board (the same as ``clipper cut --range``)."""
        import re

        from .pipeline import check_segments
        from .work import read_json

        m = re.fullmatch(r"([A-Za-z0-9_-]+)/([0-9a-z-]+)", str(raw.get("key") or ""))
        folder = self.cfg.data_dir / "work" / m.group(1) / "ranges" / m.group(2) if m else None
        info = read_json(folder / "range.json", {}) if folder else {}
        if not info.get("ready") or not (folder / "range.mp4").exists():
            raise ValueError("this downloaded part is not there any more; download it again")
        pieces = check_segments(raw.get("pieces"), float(info["duration_s"]))  # refuse a bad piece now, with its reason
        cmd = ["cut", info["url"], "--range", m.group(2), "--pieces", ",".join(f"{a:g}-{b:g}" for a, b in pieces)]
        cmd += ["--no-part-label"] if raw.get("part_label") is False else []
        return self._start_job(cmd, f"making {len(pieces)} clip(s) from the cutting board")

    def start_cut(self, raw: dict) -> dict:
        """Make clips from a range the person chose (the same as ``clipper cut``), as the one finished-stream job."""
        from .pipeline import parse_clock, split_range
        from .sources import parse_source

        source = parse_source(str(raw.get("source") or ""))
        start = parse_clock(raw.get("start") or 0)
        end = None if raw.get("to_end") else parse_clock(raw.get("end") or 0)
        if end is not None and end <= start:
            raise ValueError("the end time must be after the start time")
        parts = int(raw.get("parts") or 1)
        part_s = float(raw.get("part_seconds")) if raw.get("part_seconds") else None
        if end is not None:
            split_range(start, end, parts, part_s)  # refuse an impossible split now, with its reason, not in the log later
        cmd = ["cut", source.url, "--start", f"{start:g}", "--parts", str(parts)]
        cmd += ["--end", f"{end:g}"] if end is not None else []
        cmd += ["--part-seconds", f"{part_s:g}"] if part_s else []
        cmd += ["--no-part-label"] if raw.get("part_label") is False else []
        with self._lock:
            if self.running():
                raise ValueError("another job is still running in this box; wait for it to end or press Stop")
            self.run_problem, self.run_seen = "", True
            self.run_log_path.parent.mkdir(parents=True, exist_ok=True)
            out = self.run_log_path.open("w", encoding="utf-8")
            out.write(f"{time.strftime('%H:%M:%S')} making your own clip(s) from {source.url}\n")
            out.flush()
            self._new_job()
            self.run_proc = subprocess.Popen(
                [sys.executable, "-u", "-X", "faulthandler", "-c", "from clipper.cli import main; raise SystemExit(main())", *cmd],
                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(PROJECT_ROOT),
            )
        return self.status()

    def stop_run(self) -> dict:
        with self._lock:
            if self.running():
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(self.run_proc.pid), "/T", "/F"], capture_output=True)
                else:
                    self.run_proc.terminate()
                self.run_proc = None  # stopped on purpose: not a failure to report
                with self.run_log_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"{time.strftime('%H:%M:%S')} stopped\n")
        return self.status()

    # ---- finish ----

    def start_finish(self) -> dict:
        with self._lock:
            if self.finishing:
                raise ValueError("finish is already running")
            self.finishing = True
            self.finish_lines = [f"{time.strftime('%H:%M:%S')} rendering approved clips..."]
        threading.Thread(target=self._finish, daemon=True, name="finish").start()
        return self.status()

    def _finish(self) -> None:
        from .pipeline import stage_finish

        try:
            problems: list[str] = []
            self.finish_progress = {"done": 0, "total": 0, "now": ""}

            from . import progress as report

            report.begin([("draft", "Making the videos", 1)], path=self.finish_progress_path)
            report.stage("draft")

            def progress(done: int, total: int, now: str) -> None:
                self.finish_progress = {"done": done, "total": total, "now": now}
                if done < total:
                    report.item(done, total, f"clip {done + 1} of {total}: {now}".strip())
                else:
                    report.finish()

            cfg = self.reload()
            brief = str(PROJECT_ROOT / "briefs" / f"{cfg.render.campaign}.yaml") if cfg.render.campaign else None
            results = stage_finish(cfg, brief_path=brief, problems=problems, progress=progress)
            self.finish_lines += [f"READY {video}" if ok else f"NOT MADE to the campaign's rules: {video} (see its text file for what to fix)"
                                  for video, ok in results]
            self.finish_lines += [f"NOT MADE {p}" for p in problems]
            self.finish_lines.append(f"{time.strftime('%H:%M:%S')} done: {len(results)} file(s) in {self.cfg.data_dir / 'ready'}")
        except Exception as e:  # shown on the page instead of killing the server
            import traceback

            log.exception("finish failed")
            self.finish_lines += [f"FAILED: {type(e).__name__}: {e}"] + traceback.format_exc().splitlines()
        finally:
            self.finishing = False
