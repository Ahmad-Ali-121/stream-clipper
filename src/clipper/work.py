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
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def done(path: Path) -> bool:
    """A stage output counts as finished only if it exists and is not empty."""
    return path.exists() and path.stat().st_size > 0


def free_gb(path: Path) -> float:
    import shutil

    return shutil.disk_usage(path).free / 1e9
