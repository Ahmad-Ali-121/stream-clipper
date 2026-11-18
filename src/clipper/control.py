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
        self._lock = threading.Lock()

    def reload(self) -> Config:
        self.cfg = load_config()
        return self.cfg

    # ---- options ----

    def status(self) -> dict:
        cfg = self.cfg
        tail: list[str] = []
        if self.log_path.exists():
            tail = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        return {
            "mode": parse_mode(cfg.watch.mode), "modes": MODE_TEXT, "channels": " ".join(cfg.watch.channels),
            "ab_versions": cfg.render.ab_versions, "opening": cfg.render.opening,
            "watching": self.watching(), "watch_log": tail,
            "finishing": self.finishing, "finish_log": self.finish_lines[-12:],
        }

    def save(self, raw: dict) -> dict:
        save_local(clean_options(raw))
        self.reload()
        return self.status()

    # ---- watcher ----

    def watching(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

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
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            out = self.log_path.open("w", encoding="utf-8")
            out.write(f"{time.strftime('%H:%M:%S')} starting: {' '.join(names)} ({cfg.watch.mode})\n")
            out.flush()
            self.proc = subprocess.Popen(
                [sys.executable, "-c", "from clipper.cli import main; raise SystemExit(main())", "watch", *names, "--mode", cfg.watch.mode],
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
                with self.log_path.open("a", encoding="utf-8") as fh:
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
            results = stage_finish(self.reload())
            self.finish_lines += [f"READY {video}" for video, _ok in results]
            self.finish_lines.append(f"{time.strftime('%H:%M:%S')} done: {len(results)} file(s) in {self.cfg.data_dir / 'ready'}")
        except Exception as e:  # shown on the page instead of killing the server
            log.exception("finish failed")
            self.finish_lines.append(f"FAILED: {e}")
        finally:
            self.finishing = False
