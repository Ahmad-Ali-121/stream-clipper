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


DECIDED = ("approved", "rejected", "deleted")
LEDGER = "decisions.json"  # in the data folder: every review decision, kept apart from the clips themselves


def _ledger_path(clip_json: Path) -> Path | None:
    """The decisions record for a clip.json at <data>/work/<video>/clips/<clip>/clip.json; None for any other file."""
    parts = clip_json.parts
    if clip_json.name != "clip.json" or len(parts) < 6 or parts[-3] != "clips" or parts[-5] != "work":
        return None
    return clip_json.parents[4] / LEDGER


def _ledger_key(clip_json: Path) -> str:
    return f"{clip_json.parents[2].name}/{clip_json.parent.name}"


class LedgerUnreadable(Exception):
    pass


def _read_ledger(path: Path) -> dict:
    """The decisions record. Missing means none yet. There but unreadable (being replaced this instant, or damaged)
    is NOT the same as empty: it is tried again briefly, then reported, so it is never written over with less."""
    import time

    if not path.exists():
        return {}
    problem: Exception | None = None
    for _ in range(5):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
            problem = ValueError("it does not hold a list of decisions")
            break
        except (OSError, ValueError) as e:
            problem = e
            time.sleep(0.05)
    raise LedgerUnreadable(f"{path} cannot be read ({problem})")


_ledger_lock = __import__("threading").Lock()


def _note_decision(clip_json: Path, status: str | None) -> None:
    """Write a decision into the record (or take it out, when a person moved the clip back to review)."""
    ledger = _ledger_path(clip_json)
    if ledger is None:
        return
    import time

    key = _ledger_key(clip_json)
    with _ledger_lock:
        try:
            data = _read_ledger(ledger)
        except LedgerUnreadable as e:
            # The clip's own file still carries the decision; the record is left as it is and refilled at the next start.
            logging.getLogger(__name__).error("THE DECISIONS RECORD COULD NOT BE READ, so this decision was not added to it: %s", e)
            return
        if status in DECIDED:
            if (data.get(key) or {}).get("status") == status:
                return
            data[key] = {"status": status, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        elif key in data:
            del data[key]
        else:
            return
        tmp = _tmp(ledger)
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        _replace(tmp, ledger)


def decided_status(clip_json: Path) -> str | None:
    """What a person decided about this clip according to the record, or None."""
    ledger = _ledger_path(clip_json)
    if not ledger:
        return None
    try:
        return (_read_ledger(ledger).get(_ledger_key(clip_json)) or {}).get("status")
    except LedgerUnreadable as e:
        logging.getLogger(__name__).error("THE DECISIONS RECORD COULD NOT BE READ, so it could not be checked for this clip: %s", e)
        return None


def _keep_decision(path: Path, data: Any) -> None:
    """The last line of defence for a review decision.

    A clip a person approved, rejected or deleted must never slide back to "to review": not because some automatic
    step wrote an older copy of its state, not because its folder was made again, not because the program was
    updated. The decision is looked up in two places, the clip's own file and the separate decisions record, and
    whichever knows of one wins over an automatic "to review". The attempt is logged with where it came from.
    Only the review page's own buttons (and a person asking for a deleted clip again) may reopen a clip; they pass
    ``reopen=True`` to ``write_json``.
    """
    if path.name != "clip.json" or not isinstance(data, dict) or data.get("status") not in (None, "candidate"):
        return
    disk: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            disk = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            disk = {}
    kept = disk.get("status") if disk.get("status") in DECIDED else decided_status(path)
    if kept in DECIDED:
        import traceback

        where = " <- ".join(f"{f.name}:{f.lineno}" for f in traceback.extract_stack(limit=7)[:-2][::-1])
        logging.getLogger(__name__).error(
            "A STEP TRIED TO PUT A DECIDED CLIP BACK TO REVIEW and was stopped: clip %s/%s stays %s. Report this line. It came from: %s",
            path.parent.parent.parent.name, path.parent.name, kept, where)
        data["status"] = kept
        for key in ("decided_at", "status_log"):
            if key in disk:
                data[key] = disk[key]


def restore_decisions(data_dir: Path) -> int:
    """Put back every decision that a clip's own file has lost, from the decisions record. Returns how many were restored.

    Run when the review page starts (so also after every update) and cheap enough to run often. It also fills the
    record from clips decided before the record existed.
    """
    ledger = data_dir / LEDGER
    try:
        known = _read_ledger(ledger)
    except LedgerUnreadable as e:
        # Kept aside, never deleted: a person can look at it. A new record is filled from the clips as they are.
        aside = ledger.with_name(f"decisions.damaged-{__import__('time').strftime('%Y%m%d-%H%M%S')}.json")
        logging.getLogger(__name__).error("THE DECISIONS RECORD IS DAMAGED (%s). It was moved to %s and a new one is filled from the clips as they are now.", e, aside.name)
        try:
            os.replace(ledger, aside)
        except OSError:
            return 0
        known = {}
    restored = 0
    for clip_json in (data_dir / "work").glob("*/clips/*/clip.json"):
        try:
            state = json.loads(clip_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(state, dict):
            continue
        key = _ledger_key(clip_json)
        if state.get("status") in DECIDED:
            _note_decision(clip_json, state["status"])
        elif (known.get(key) or {}).get("status") in DECIDED:
            state["status"] = known[key]["status"]
            state.setdefault("decided_at", known[key].get("at", ""))
            write_json(clip_json, state)
            restored += 1
            logging.getLogger(__name__).warning("clip %s had lost its decision; it is %s again, as you decided on %s", key, state["status"], known[key].get("at", "an earlier day"))
    return restored


def write_json(path: Path, data: Any, reopen: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not reopen:
        _keep_decision(path, data)
    if path.name == "clip.json" and isinstance(data, dict):
        # Every decision also goes into the separate record; a person's "back to review" takes it out again.
        if data.get("status") in DECIDED:
            _note_decision(path, data["status"])
        elif reopen:
            _note_decision(path, None)
    tmp = _tmp(path)
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _replace(tmp, path)


# What only a person sets, on the review page. An automatic step (the watcher making a draft, a render, the text
# writer) may be half-way through with an older copy of a clip's state when the person approves it or ticks a box;
# these keys are therefore always taken from what is on disk at the moment of writing, never from that older copy.
HUMAN_KEYS = ("status", "explainer", "simple", "held", "disclosure", "notes")


def save_clip(path: Path, state: dict) -> dict:
    """Write a clip's state from an automatic step, keeping whatever a person decided in the meantime."""
    disk = read_json(path, {}) or {}
    for key in HUMAN_KEYS:
        if key in disk:
            state[key] = disk[key]
        elif disk:
            state.pop(key, None)  # taken away by the person meanwhile (a clip that left Pending): not put back
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
