"""Locate and run the external programs the pipeline depends on.

Tools are looked up on PATH first, then under ``<data_dir>/tools`` (where
setup puts FFmpeg and TwitchDownloaderCLI). Nothing is hard-coded.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)


class ToolError(RuntimeError):
    pass


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


def run(
    cmd: Sequence[str],
    *,
    timeout_s: float | None = None,
    retries: int = 0,
    retry_wait_s: float = 5.0,
    what: str = "",
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command, capture its output, retry on failure, raise ToolError with the tail of stderr.

    Retries exist because the internet connection drops: a failed download is
    normal here, not exceptional.
    """
    label = what or Path(cmd[0]).name
    last = ""
    for attempt in range(retries + 1):
        started = time.monotonic()
        try:
            proc = subprocess.run(
                list(cmd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                stdin=subprocess.DEVNULL,  # never let a tool sit waiting on a prompt
                cwd=str(cwd) if cwd else None,
            )
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
