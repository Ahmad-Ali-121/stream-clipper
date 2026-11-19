"""The options panel on the local review page: what to watch, how, and which versions to make.

Everything here runs on this machine only. Options are saved to
``config.local.yaml`` (the personal override file beside config.yaml), the
watcher is the same ``clipper watch`` command run as a child process, and
"finish" is the same ``clipper finish`` step. Nothing is posted anywhere.
"""

from __future__ import annotations

import logging
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
        elif key == "channels":
            from .watch import channel_name

            names = [channel_name(c) for c in str(value).replace(",", " ").split()]
            out.setdefault("watch", {})["channels"] = names
        else:
            raise ValueError(f"unknown option: {key}")
    return out


class Control:
    """State behind the panel: the watcher process and the last finish run."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.log_path = cfg.data_dir / "watch.log"
        self.finish_lines: list[str] = []
        self.finishing = False
        self.watch_problem = ""  # why the watcher is not running although nobody stopped it
        self.run_proc: subprocess.Popen | None = None  # "analyse a finished stream", the same as `clipper run URL`
        self.run_log_path = cfg.data_dir / "run.log"
        self.run_problem = ""
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
            "watching": self.watching(), "watch_log": tail, "watch_problem": self.watch_problem,
            "finishing": self.finishing, "finish_log": self.finish_lines[-200:],
            "running": self.running(), "run_log": self._run_tail(), "run_problem": self.run_problem,
        }

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
        if not 1 <= top <= 30:
            raise ValueError("the number of clips must be between 1 and 30")
        with self._lock:
            if self.running():
                raise ValueError("a finished stream is already being analysed; wait for it to end")
            self.run_problem, self.run_seen = "", True
            self.run_log_path.parent.mkdir(parents=True, exist_ok=True)
            out = self.run_log_path.open("w", encoding="utf-8")
            out.write(f"{time.strftime('%H:%M:%S')} analysing {source.url}: the best {top} moments\n")
            out.flush()
            self.run_proc = subprocess.Popen(
                [sys.executable, "-u", "-X", "faulthandler", "-c", "from clipper.cli import main; raise SystemExit(main())",
                 "run", source.url, "--top", str(top)],
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
            results = stage_finish(self.reload(), problems=problems)
            self.finish_lines += [f"READY {video}" for video, _ok in results]
            self.finish_lines += [f"NOT MADE {p}" for p in problems]
            self.finish_lines.append(f"{time.strftime('%H:%M:%S')} done: {len(results)} file(s) in {self.cfg.data_dir / 'ready'}")
        except Exception as e:  # shown on the page instead of killing the server
            import traceback

            log.exception("finish failed")
            self.finish_lines += [f"FAILED: {type(e).__name__}: {e}"] + traceback.format_exc().splitlines()
        finally:
            self.finishing = False
