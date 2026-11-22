"""Locate and run the external programs the pipeline depends on.

Tools are looked up on PATH first, then under ``<data_dir>/tools`` (where
setup puts FFmpeg and TwitchDownloaderCLI). Nothing is hard-coded.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)


class ToolError(RuntimeError):
    pass


class Stopped(BaseException):
    """The person pressed Stop while a tool was working: the tool was ended and the job must end too.

    Not an ordinary error on purpose: code that catches a failed tool to try again, or to carry on with the next clip,
    must not catch this one.
    """


_stop = threading.local()


def stop_on(event: threading.Event | None) -> None:
    """From now on, tools started by this thread are ended as soon as ``event`` is set (None switches it off)."""
    _stop.event = event


def _stop_event() -> threading.Event | None:
    return getattr(_stop, "event", None)


def _wait(proc: subprocess.Popen, timeout_s: float | None, wait) -> None:
    """Wait for a tool to end, looking twice a second whether the person pressed Stop.

    ``wait(seconds)`` is the tool's own way of waiting; it raises TimeoutExpired when that time is up."""
    event = _stop_event()
    if event is None:
        return wait(timeout_s)
    end = None if timeout_s is None else time.monotonic() + timeout_s
    while True:
        if event.is_set():
            kill_tree(proc)
            raise Stopped()
        try:
            return wait(0.5)
        except subprocess.TimeoutExpired:
            if end is not None and time.monotonic() > end:
                raise


@lru_cache(maxsize=None)
def _find(name: str, tools_dir: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    root = Path(tools_dir)
    if root.is_dir():
        for candidate in sorted(root.rglob(f"{name}.exe")) + sorted(root.rglob(name)):
            if candidate.is_file():
                return str(candidate)
    return None


def find_tool(name: str, data_dir: Path) -> str:
    path = _find(name, str(data_dir / "tools"))
    if not path:
        raise ToolError(f"{name} was not found on PATH or under {data_dir / 'tools'}. Run setup.ps1 to see what is missing.")
    return path


def kill_tree(proc: subprocess.Popen) -> None:
    """Stop a program and the programs it started (yt-dlp and TwitchDownloader both run FFmpeg, which would
    otherwise keep downloading and keep the unfinished file locked)."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, stdin=subprocess.DEVNULL)
    else:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _run_plain(cmd: Sequence[str], timeout_s: float | None, cwd: Path | None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,  # never wait on a prompt
                            text=True, encoding="utf-8", errors="replace", cwd=str(cwd) if cwd else None)
    got: list = []
    try:
        _wait(proc, timeout_s, lambda t: got.extend(proc.communicate(timeout=t)))
    except (subprocess.TimeoutExpired, Stopped):
        kill_tree(proc)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(list(cmd), proc.returncode, got[0], got[1])


def _run_watched(cmd: Sequence[str], timeout_s: float | None, cwd: Path | None, on_line) -> subprocess.CompletedProcess[str]:
    """Run a command while passing every line it prints to ``on_line`` (for a percentage on the page).

    Output and errors come back together as ``stdout``; lines that only report progress are left out of it,
    so an error message still ends with the tool's real last words.
    """
    import re
    import threading

    from .progress import is_progress_line

    proc = subprocess.Popen(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(cwd) if cwd else None)
    kept: list[str] = []

    def pump() -> None:
        buf, warned = b"", False
        while True:
            data = proc.stdout.read1(8192)
            if not data:
                break
            *lines, buf = re.split(rb"[\r\n]", buf + data)  # progress lines end with a carriage return, not a new line
            for raw in lines:
                line = raw.decode("utf-8", errors="replace")
                if not line.strip():
                    continue
                if not is_progress_line(line):
                    kept.append(line)
                try:
                    on_line(line)
                except Exception as e:  # a progress reading must never stop the work itself; say so once
                    if not warned:
                        warned = True
                        log.warning("progress could not be read from a tool's output (%s); the work goes on without a percentage", e)
        if buf.strip():
            kept.append(buf.decode("utf-8", errors="replace"))

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        _wait(proc, timeout_s, lambda t: proc.wait(timeout=t))
    except (subprocess.TimeoutExpired, Stopped):
        kill_tree(proc)
        reader.join(timeout=5)
        raise
    reader.join(timeout=10)
    return subprocess.CompletedProcess(list(cmd), proc.returncode, "\n".join(kept), "")


def run(
    cmd: Sequence[str],
    *,
    timeout_s: float | None = None,
    retries: int = 0,
    retry_wait_s: float = 5.0,
    what: str = "",
    cwd: Path | None = None,
    on_line=None,
) -> subprocess.CompletedProcess[str]:
    """Run a command, capture its output, retry on failure, raise ToolError with the tail of stderr.

    Retries exist because the internet connection drops: a failed download is
    normal here, not exceptional.
    """
    label = what or Path(cmd[0]).name
    last = ""
    for attempt in range(retries + 1):
        if (event := _stop_event()) is not None and event.is_set():
            raise Stopped()  # Stop was pressed between two tools: the next one is not started
        started = time.monotonic()
        try:
            proc = _run_watched(cmd, timeout_s, cwd, on_line) if on_line else _run_plain(cmd, timeout_s, cwd)
        except subprocess.TimeoutExpired:
            last = f"timed out after {timeout_s:.0f}s"
        else:
            if proc.returncode == 0:
                log.debug("%s finished in %.1fs", label, time.monotonic() - started)
                return proc
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
            last = f"exit code {proc.returncode}: " + " | ".join(tail)
        if attempt < retries:
            log.warning("%s failed (%s); retry %d of %d in %.0fs", label, last, attempt + 1, retries, retry_wait_s)
            time.sleep(retry_wait_s)
    raise ToolError(f"{label} failed: {last}")
