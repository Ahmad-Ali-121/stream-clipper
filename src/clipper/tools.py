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


class _Enough(Exception):
    """The tool has made what it was started for but does not end by itself: it is ended, and that counts as success."""


def _wait(proc: subprocess.Popen, timeout_s: float | None, wait, enough=None) -> None:
    """Wait for a tool to end, looking twice a second whether the person pressed Stop.

    ``wait(seconds)`` is the tool's own way of waiting; it raises TimeoutExpired when that time is up.
    ``enough()`` (optional) says the tool's work is already there, so it need not be waited for any longer."""
    event = _stop_event()
    if event is None and enough is None:
        return wait(timeout_s)
    end = None if timeout_s is None else time.monotonic() + timeout_s
    while True:
        if event is not None and event.is_set():
            kill_tree(proc)
            raise Stopped()
        if enough is not None and enough():
            raise _Enough()
        try:
            return wait(0.5)
        except subprocess.TimeoutExpired:
            if end is not None and time.monotonic() > end:
                raise


_found: dict[tuple[str, str], str] = {}


def _find(name: str, tools_dir: str) -> str | None:
    """Where the tool is. A place found before is remembered while the file is still there; "not found" is never
    remembered, so a tool installed by the Repair button is seen without restarting the program."""
    known = _found.get((name, tools_dir))
    if known and Path(known).is_file():
        return known
    path = _look_for(name, tools_dir)
    if path:
        _found[(name, tools_dir)] = path
    return path


def _look_for(name: str, tools_dir: str) -> str | None:
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


def _own_group(proc: subprocess.Popen) -> None:
    """Put a started program into a group of its own (a Windows job), so that everything it starts can be ended with
    it later, even when the program itself has already gone and its helpers are no longer findable through it."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = ctypes.c_void_p
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        job = k.CreateJobObjectW(None, None)
        if not job:
            return
        if not k.AssignProcessToJobObject(job, int(proc._handle)):
            k.CloseHandle(job)
            return
        proc._clipper_job = job
    except Exception as e:  # without the group the older way (ending the tree by its top) still works
        log.debug("no job group for %s: %s", proc.args[0] if proc.args else "?", e)


def _end_group(proc: subprocess.Popen, kill: bool) -> None:
    job = getattr(proc, "_clipper_job", None)
    if not job:
        return
    proc._clipper_job = None
    try:
        import ctypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        if kill:
            k.TerminateJobObject(job, 1)
        k.CloseHandle(job)
    except Exception as e:
        log.debug("job group could not be ended: %s", e)


def kill_tree(proc: subprocess.Popen) -> None:
    """Stop a program and the programs it started (yt-dlp and TwitchDownloader both run FFmpeg, which would
    otherwise keep downloading and keep the unfinished file locked)."""
    if getattr(proc, "_clipper_job", None):
        _end_group(proc, kill=True)  # everything it started, also when the program itself has ended already
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        return
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


def _run_plain(cmd: Sequence[str], timeout_s: float | None, cwd: Path | None, input_text: str | None = None,
               enough=None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL if input_text is None else subprocess.PIPE,  # never wait on a prompt
                            text=True, encoding="utf-8", errors="replace", cwd=str(cwd) if cwd else None)
    _own_group(proc)
    if input_text is not None:  # what the tool is to read (a request too long or too odd for the command line), then its input ends
        try:
            proc.stdin.write(input_text)
            proc.stdin.close()
        except OSError:
            pass  # it ended before reading: its exit code and last words say why
        proc.stdin = None
    got: list = []

    def last_words() -> tuple[str, str]:
        # The program is ended, but a helper it started can outlive it and keep its output open; waiting for that
        # without a limit would bring back the very hang the time limit and the Stop button exist to end.
        try:
            return proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            log.warning("%s was ended, but something it started is still running and holds its output; not waiting for it", Path(cmd[0]).name)
            return "", ""

    try:
        _wait(proc, timeout_s, lambda t: got.extend(proc.communicate(timeout=t)), enough)
    except (subprocess.TimeoutExpired, Stopped):
        kill_tree(proc)
        last_words()
        raise
    except _Enough:
        kill_tree(proc)
        out, err = last_words()
        log.warning("%s had finished its work but did not end by itself; it was ended", Path(cmd[0]).name)
        return subprocess.CompletedProcess(list(cmd), 0, out, err)
    except BaseException:  # anything else (a fault in the "is it finished?" check): never leave the program running behind us
        kill_tree(proc)
        last_words()
        raise
    _end_group(proc, kill=False)  # ended by itself: nothing is taken from it
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
    _own_group(proc)
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
    _end_group(proc, kill=False)
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
    input_text: str | None = None,
    enough=None,
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
            proc = _run_watched(cmd, timeout_s, cwd, on_line) if on_line else _run_plain(cmd, timeout_s, cwd, input_text, enough)
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
