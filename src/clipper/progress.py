"""How far a job has got, as a percentage, in a small file the review page reads.

A job (a ``clipper`` command started by the page) names its steps once with ``begin`` and then reports as it works:
``stage`` when a step starts, ``item`` for clip 3 of 8, ``sub`` for how far the current download or render is.
The page turns that into a bar: "41% · Step 2 of 4: Downloading the clips · clip 3 of 8".

Nothing happens unless ``begin`` was called with a place to write (the page passes it in ``CLIPPER_PROGRESS``),
so the same code runs silently from a terminal and inside the watcher.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_s: dict = {"path": None, "plan": [], "idx": -1, "on": False, "item": (0, 1), "frac": 0.0, "text": "", "last": 0.0, "t0": 0.0}


def begin(stages: list[tuple[str, str, float]], path: str | Path | None = None) -> None:
    """Name the job's steps: (key, what the page calls it, share of the whole job's time)."""
    target = path or os.environ.get("CLIPPER_PROGRESS")
    with _lock:
        _s.update(path=Path(target) if target else None, plan=[s for s in stages if s[2] > 0], idx=-1, on=False,
                  item=(0, 1), frac=0.0, text="", last=0.0, t0=time.time())
    _write(force=True)


def stage(key: str, text: str = "") -> None:
    """A step starts. A step this job did not name is ignored, with everything reported inside it."""
    with _lock:
        keys = [s[0] for s in _s["plan"]]
        _s["on"] = key in keys
        if not _s["on"]:
            return
        _s.update(idx=keys.index(key), item=(0, 1), frac=0.0, text=text)
    _write(force=True)


def item(index: int, count: int, text: str = "") -> None:
    """Working on thing number ``index`` (counted from 0) of ``count`` in this step."""
    with _lock:
        if not _s["on"]:
            return
        _s.update(item=(max(0, index), max(1, count)), frac=0.0, text=text)
    _write(force=True)


def sub(fraction: float, text: str | None = None) -> None:
    """How far the current thing is, from 0 to 1. Never goes backwards within one thing."""
    with _lock:
        if not _s["on"]:
            return
        _s["frac"] = max(_s["frac"], min(1.0, max(0.0, float(fraction))))
        if text is not None:
            _s["text"] = text
    _write()


def finish(text: str = "") -> None:
    with _lock:
        if not _s["plan"]:
            return
        _s.update(idx=len(_s["plan"]) - 1, on=True, item=(0, 1), frac=1.0, text=text)
    _write(force=True)


def snapshot() -> dict:
    """What the page is told right now."""
    with _lock:
        plan, idx = _s["plan"], _s["idx"]
        total = sum(s[2] for s in plan) or 1.0
        if idx < 0:
            return {"percent": 0.0, "stage": "Starting", "stage_no": 0, "stages": len(plan), "text": "", "started": _s["t0"], "at": time.time()}
        i, n = _s["item"]
        inside = min(1.0, (i + _s["frac"]) / n)
        got = sum(s[2] for s in plan[:idx]) + plan[idx][2] * inside
        return {"percent": round(100 * got / total, 1), "stage": plan[idx][1], "stage_no": idx + 1, "stages": len(plan),
                "text": _s["text"], "started": _s["t0"], "at": time.time()}


def _write(force: bool = False) -> None:
    path = _s["path"]
    if path is None or (not force and time.time() - _s["last"] < 0.5):
        return
    _s["last"] = time.time()
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(snapshot()), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # the page is reading the file this instant; the next report, half a second on, replaces it


def read(path: Path, max_age_s: float = 3600) -> dict | None:
    """The last report in ``path``, or None when there is none that can be read."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and time.time() - float(data.get("at") or 0) < max_age_s else None


# ---- reading a percentage out of what the download and video tools print ----

import re  # noqa: E402

_TIME = re.compile(r"time=(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_TWITCH = re.compile(r"\[STATUS\] - (.+?)(?: (\d{1,3})%)? \[(\d+)/(\d+)\]")
_TWITCH_CHAT = re.compile(r"\[STATUS\] - (Downloading|Backfilling)[^%]*? (\d{1,3})%\s*$")
_TWITCH_VIDEO = (0.02, 0.86, 0.04, 0.08)  # fetching info, downloading, verifying, finalizing


def is_progress_line(line: str) -> bool:
    """A line that only says how far a tool is: worth a percentage, not worth keeping in an error message."""
    return ("time=" in line and "bitrate=" in line) or line.startswith("[STATUS]")


def tool_fraction(line: str, total_s: float = 0.0) -> float | None:
    """How far a tool is, from one line it printed: FFmpeg's position in the video, or TwitchDownloader's step."""
    m = _TIME.search(line)
    if m and total_s > 0:
        return min(1.0, (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) / total_s)
    m = _TWITCH.search(line)
    if m:
        k, n, pct = int(m.group(3)), int(m.group(4)), int(m.group(2) or 0) / 100
        shares = _TWITCH_VIDEO if n == 4 else tuple(1 / n for _ in range(n))
        return min(1.0, sum(shares[: k - 1]) + shares[k - 1] * pct)
    m = _TWITCH_CHAT.search(line)  # the chat download has two steps and does not number them
    if m:
        pct = int(m.group(2)) / 100
        return 0.9 * pct if m.group(1) == "Downloading" else 0.9 + 0.1 * pct
    return None


def from_tool(total_s: float = 0.0, label: str | None = None):
    """A line reader for ``tools.run(on_line=...)`` that reports the tool's progress on the current thing.

    ``label`` adds the tool's own percentage to the text ("chat 40% downloaded"), for a step that is a small
    share of the whole job and would otherwise look stuck.
    """
    def on_line(line: str) -> None:
        f = tool_fraction(line, total_s)
        if f is not None:
            sub(f, label.format(pct=int(f * 100)) if label else None)

    return on_line
