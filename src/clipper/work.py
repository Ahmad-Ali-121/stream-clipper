"""Work-folder layout and small JSON helpers.

  <data_dir>/work/<platform>_<video id>/
      meta.json            title, channel, duration of the source
      chat.json            downloaded chat
      moments.json         ranked moments
      clips/1h02m30s/      one folder per moment, named by its position in the stream
          clip.json        everything known about this clip (see ClipState)
          source.mp4       the fetched range (landscape, untouched)
          words.json       word timestamps, relative to source.mp4
          captions.ass
          final.mp4        the rendered 9:16 clip
          post.md          title, description and hashtags per platform

Every stage writes its output to a temporary name and renames it when complete,
so a crash or a dropped connection never leaves a half-written file that a
later run would mistake for a finished one.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from .sources import Source


def work_dir(data_dir: Path, source: Source) -> Path:
    d = data_dir / "work" / source.key
    d.mkdir(parents=True, exist_ok=True)
    return d


def clip_id(peak_s: float) -> str:
    """Clips are named by where they sit in the stream (h-mm-ss), not by rank.

    Rank changes whenever moments are re-scored (new chat on a live stream, a
    tuned setting); the position in the stream does not, so an approved clip
    keeps its folder.
    """
    s = int(peak_s)
    return f"{s // 3600}h{s % 3600 // 60:02d}m{s % 60:02d}s"


def clip_dir(work: Path, cid: str) -> Path:
    d = work / "clips" / cid
    d.mkdir(parents=True, exist_ok=True)
    return d


def clip_dirs(work: Path) -> list[Path]:
    root = work / "clips"
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "clip.json").exists()) if root.is_dir() else []


def read_json(path: Path, default: Any = None) -> Any:
    """The file's contents. With a ``default``, a file that is missing, empty or damaged gives the default (and a
    line in the log) instead of an error, so one bad file cannot blank a whole list on the review page."""
    if not path.exists():
        return default
    if default is None:
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logging.getLogger(__name__).warning("%s cannot be read (%s); it is treated as empty", path, e)
        return default
    return default if data is None or type(data) is not type(default) else data


def _replace(tmp: Path, path: Path) -> None:
    """Put the finished file in place. On Windows this is refused while another program (the review page, a job)
    has the old file open for the instant it takes to read it, so it is tried again for up to two seconds."""
    import time

    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.1)


class FileInUse(ValueError):
    """A file is open in another program (playing on the page, or a job is working on it), so it cannot be removed yet."""


def remove(path: Path, wait_s: float = 1.5) -> None:
    """Delete a file if it is there. Windows refuses while the file is open; that is tried again briefly, then said plainly."""
    import time

    end = time.monotonic() + wait_s
    while True:
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if time.monotonic() >= end:
                raise FileInUse(f"the file {path.name} of clip {path.parent.name} is in use right now: its video is playing, or a job is "
                                "still working on it. Pause the video or wait for the job, then try again.") from None
            time.sleep(0.1)


def _tmp(path: Path) -> Path:
    # One name per writer: two writers of the same file must not share (and replace) each other's half-written copy.
    import threading

    return path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")


_update_lock = __import__("threading").Lock()


def update_clip(path: Path, changes: dict, drop: tuple = ()) -> dict:
    """Change a few keys of a clip's state as it is on disk now.

    For a step that took a while (a render, an export): writing back the copy it read at the start would undo
    whatever a person edited in the meantime, so only what the step produced is written.
    """
    with _update_lock:
        state = read_json(path, {})
        state.update(changes)
        for key in drop:
            state.pop(key, None)
        write_json(path, state)
    return state


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(path)
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _replace(tmp, path)


# What only a person sets, on the review page. An automatic step (the watcher making a draft, a render, the text
# writer) may be half-way through with an older copy of a clip's state when the person approves it or ticks a box;
# these keys are therefore always taken from what is on disk at the moment of writing, never from that older copy.
HUMAN_KEYS = ("status", "explainer", "disclosure", "notes")


def save_clip(path: Path, state: dict) -> dict:
    """Write a clip's state from an automatic step, keeping whatever a person decided in the meantime."""
    disk = read_json(path, {}) or {}
    for key in HUMAN_KEYS:
        if key in disk:
            state[key] = disk[key]
    write_json(path, state)
    return state


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(path)
    tmp.write_text(text, encoding="utf-8")
    _replace(tmp, path)


def done(path: Path) -> bool:
    """A stage output counts as finished only if it exists and is not empty."""
    return path.exists() and path.stat().st_size > 0


def free_gb(path: Path) -> float:
    import shutil

    return shutil.disk_usage(path).free / 1e9
