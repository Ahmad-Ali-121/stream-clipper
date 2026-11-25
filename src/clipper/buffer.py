"""A short rolling copy of a live Twitch feed, for broadcasts Twitch is not recording.

Clips are normally cut from the recording (VOD) Twitch makes while a stream runs.
Some streamers switch recordings off; then there is nothing to cut from. For those
streams only, this keeps the last few minutes of the live feed on disk as the small
pieces Twitch serves (about two seconds each), deletes pieces as they age out, and
cuts a clip's range out of what is still there.

It is never a recording of the stream: only ``watch.buffer_minutes`` exist at any time,
and the folder is emptied when the watch stops.

Times come from the feed's own playlist, which stamps every piece with the wall-clock
time it was broadcast and labels advert pieces, so no delay has to be guessed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from .config import Config
from .tools import ToolError, find_tool, run

log = logging.getLogger(__name__)

_AGENT = {"User-Agent": "Mozilla/5.0"}
JOIN_SLACK_S = 1.0  # pieces this close together count as continuous


@dataclass(frozen=True)
class Piece:
    url: str
    at: float  # epoch seconds when this piece was broadcast
    duration: float
    advert: bool
    init: str = ""  # address of the feed's header piece, when the feed is the newer kind (see parse_playlist)


def parse_playlist(text: str, base_url: str = "") -> list[Piece]:
    """The pieces listed in a Twitch live playlist, with broadcast time and whether each is an advert."""
    out: list[Piece] = []
    at: float | None = None
    duration, title, init = 0.0, "", ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-MAP:"):
            # The newer kind of feed (fragmented MP4; Twitch uses it for streams sent in HEVC or AV1): its pieces
            # cannot be played without this one header piece, which the playlist names once. Seen for real on
            # 2026-11-25: without it every clip of such a stream failed with "trun track id unknown, no tfhd was found".
            m = re.search(r'URI="([^"]+)"', line)
            init = urljoin(base_url, m.group(1)) if m else ""
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            try:
                at = datetime.fromisoformat(line.split(":", 1)[1].replace("Z", "+00:00")).timestamp()
            except ValueError:
                at = None
        elif line.startswith("#EXTINF:"):
            value, _, title = line[len("#EXTINF:"):].partition(",")
            try:
                duration = float(value)
            except ValueError:
                duration = 0.0
        elif line and not line.startswith("#"):
            if at is not None and duration > 0:
                # Twitch titles real pieces "live" and advert pieces after the advertiser ("Amazon|...").
                out.append(Piece(urljoin(base_url, line), at, duration, bool(title.strip()) and title.strip() != "live", init))
                at += duration  # in case the next piece carries no time of its own
            duration, title = 0.0, ""
    return out


def choose_pieces(pieces: list[dict], start_s: float, end_s: float, slack: float = JOIN_SLACK_S) -> list[dict]:
    """The stored pieces that cover [start_s, end_s] of the stream, or a ToolError saying why they do not.

    Each piece is {"t": stream seconds, "d": seconds, "file": path}. Adverts are never stored,
    so an advert break shows up as a gap.
    """
    have = sorted(pieces, key=lambda p: p["t"])
    if not have:
        raise ToolError("the live buffer is still empty; nothing to cut yet")
    if start_s < have[0]["t"] - slack:
        raise ToolError("this moment is no longer in the live buffer (it only keeps the last few minutes)")
    picked = [p for p in have if p["t"] + p["d"] > start_s and p["t"] < end_s]
    if have[-1]["t"] + have[-1]["d"] < end_s - slack:
        raise ToolError("the live buffer has not reached the end of this moment yet")
    missing = ToolError("part of this moment is missing from the live feed (an advert break or a dropped connection)")
    if not picked or picked[0]["t"] > start_s + slack or picked[-1]["t"] + picked[-1]["d"] < end_s - slack:
        raise missing
    for a, b in zip(picked, picked[1:]):
        if b["t"] - (a["t"] + a["d"]) > slack:
            raise missing
    return picked


def _running(pid: int) -> bool:
    """Is a program with this number running on this computer right now?"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = ctypes.c_void_p
        k.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
        k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists, under another account
        code = ctypes.c_uint(0)
        alive = bool(k.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        k.CloseHandle(handle)
        return alive
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def sweep(data_dir: Path) -> int:
    """Remove live-feed copies left on disk by a watcher that ended without the chance to tidy up (the Stop button ends
    it at once; a crash; a closed window; a power cut). The copy is only ever meant to exist while a stream is watched.

    Each buffer's folder is named after the program that owns it, so only folders whose program is no longer running
    are removed: a watcher that is still at work (one started from a terminal, say) keeps its copy.
    Returns how many folders were removed."""
    removed = 0
    for root in (data_dir / "work").glob("*/buffer"):
        for item in list(root.iterdir()) if root.is_dir() else []:
            owner = item.name.split("-")[0]
            if item.is_dir() and owner.isdigit() and _running(int(owner)):
                continue  # its watcher is running
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                try:
                    item.unlink()  # a piece kept by an older version, straight in the folder
                except OSError:
                    pass
            if item.exists():
                log.warning("a leftover copy of a live feed could not be removed (a file in it is in use): %s", item)
            else:
                removed += 1
        try:
            root.rmdir()  # only when nothing is left in it
        except OSError:
            pass
    if removed:
        log.info("%d leftover piece(s) or folder(s) of a live feed copy removed", removed)
    return removed


def live_info(channel: str, cfg: Config) -> dict | None:
    """What Twitch says about the channel's current broadcast, or None when it is offline."""
    from .fetch import _ytdlp

    try:
        proc = run(_ytdlp(cfg) + ["-J", "--skip-download", f"https://www.twitch.tv/{channel}"],
                   timeout_s=90, retries=0, what=f"{channel} live details")
        info = json.loads(proc.stdout)
    except (ToolError, ValueError) as e:
        if "not currently live" not in str(e):  # that message is the normal answer for an offline channel
            log.warning("%s: could not read its live details (%s)", channel, e)
        return None
    if not info.get("is_live") or not info.get("timestamp") or not info.get("id"):
        return None
    return {
        "id": str(info["id"]), "started_at": float(info["timestamp"]),
        "title": re.sub(r"\s*\d{4}-\d\d-\d\d \d\d:\d\d$", "", info.get("description") or info.get("title") or ""),
        "channel": info.get("uploader") or info.get("display_id") or channel,
    }


def feed_url(channel: str, cfg: Config) -> str:
    """Address of the playlist for the best live quality within fetch.max_height."""
    from . import kick
    from .fetch import _ytdlp

    h = cfg.fetch.max_height
    if channel.startswith(kick.NAME):
        return kick.live_feed(channel[len(kick.NAME):], h)
    proc = run(_ytdlp(cfg) + ["-g", "-f", f"b[height<={h}]/b", f"https://www.twitch.tv/{channel}"],
               timeout_s=90, retries=1, retry_wait_s=5, what=f"{channel} live feed address")
    url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    if not url.startswith("http"):
        raise ToolError(f"{channel}: no live feed address found")
    return url


def _get(url: str, timeout: float = 20.0, agent: dict | None = None) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=agent or _AGENT), timeout=timeout) as r:
        return r.read()


_number = __import__("itertools").count(1)


class LiveBuffer(threading.Thread):
    """Keeps the last ``watch.buffer_minutes`` of one channel's live feed in ``folder``."""

    def __init__(self, channel: str, folder: Path, stream_start: float, cfg: Config) -> None:
        super().__init__(daemon=True, name=f"buffer-{channel}")
        self.channel, self.folder, self.stream_start, self.cfg = channel, folder, stream_start, cfg
        self.keep_s = max(2.0, float(cfg.watch.buffer_minutes)) * 60
        self.agent = {"User-Agent": "stream-clipper/1.0"} if channel.startswith("kick.com/") else None  # Kick is asked under our own name
        self.stop_event = threading.Event()
        self.pieces: list[dict] = []  # {"t", "d", "file"}, oldest first
        self.adverts = 0
        self.first_t: float | None = None  # the stream second of the first piece ever kept: nothing before it was ever held
        self.bytes = 0
        self._seen: dict[str, float] = {}
        self._inits: dict[str, str] = {}  # header piece address -> its file (kept as long as the buffer runs; a few KB each)
        self._lock = threading.Lock()
        shutil.rmtree(folder, ignore_errors=True)  # pieces from an earlier run are stale
        # Its own folder inside: a buffer that was stopped can take a while to notice (it may be waiting for the site),
        # and when it then tidies up it must only remove its own pieces, never those of the buffer started after it.
        self.root = folder
        self.folder = folder / f"{os.getpid()}-{time.time_ns()}-{next(_number)}"  # the clock alone can repeat within one instant
        self.folder.mkdir(parents=True, exist_ok=True)

    def _store(self, piece: Piece) -> None:
        if (self.folder / f"{int(piece.at * 1000)}.ts").exists():
            return  # held already: after a reconnect the same pieces can come back under new addresses
        data = b""
        for attempt in range(3):
            try:
                data = _get(piece.url, agent=self.agent)
                break
            except Exception as e:  # a dropped connection; the piece is tried again, then given up
                if attempt == 2:
                    log.warning("%s: a piece of the live feed could not be fetched (%s)", self.channel, e)
                    return
                time.sleep(1.0)
        head = ""
        if piece.init:
            head = self._inits.get(piece.init, "")
            if not head:
                try:
                    blob = _get(piece.init, agent=self.agent)
                except Exception as e:
                    log.warning("%s: the header piece of the live feed could not be fetched (%s); this piece is left out", self.channel, e)
                    return
                file = self.folder / f"init{len(self._inits)}.mp4"
                file.write_bytes(blob)
                head = self._inits[piece.init] = str(file)
        t = piece.at - self.stream_start
        if self.first_t is None:
            self.first_t = t
        path = self.folder / f"{int(piece.at * 1000)}.ts"
        part = path.with_suffix(".part")
        part.write_bytes(data)
        os.replace(part, path)
        with self._lock:
            self.pieces.append({"t": round(t, 3), "d": piece.duration, "file": str(path), "init": head})
            self.bytes += len(data)

    def _trim(self) -> None:
        with self._lock:
            if not self.pieces:
                return
            newest = self.pieces[-1]["t"]
            old = [p for p in self.pieces if p["t"] < newest - self.keep_s]
            self.pieces = [p for p in self.pieces if p["t"] >= newest - self.keep_s]
        for p in old:
            Path(p["file"]).unlink(missing_ok=True)
        cutoff = time.time() - 600
        for url in [u for u, seen in self._seen.items() if seen < cutoff]:
            del self._seen[url]

    def run(self) -> None:
        url, failures, wait = "", 0, 5.0
        while not self.stop_event.is_set():
            try:
                if not url:
                    url = feed_url(self.channel, self.cfg)
                found = parse_playlist(_get(url, agent=self.agent).decode("utf-8", "replace"), url)
                failures, wait = 0, 5.0
                for piece in found:
                    if piece.url in self._seen or self.stop_event.is_set():
                        continue
                    self._seen[piece.url] = time.time()
                    if piece.advert:
                        self.adverts += 1  # adverts are never written to disk
                        continue
                    self._store(piece)
                self._trim()
                self.stop_event.wait(1.5)
            except Exception as e:  # the feed dropping is normal (hiccups, stream end)
                failures += 1
                if failures >= 3:
                    url = ""  # the address may have expired; ask for a fresh one
                    log.warning("%s: live feed problem (%s); reconnecting in %.0fs", self.channel, e, wait)
                    self.stop_event.wait(wait)
                    wait = min(wait * 2, 60.0)
                else:
                    self.stop_event.wait(2.0)
        shutil.rmtree(self.folder, ignore_errors=True)
        try:
            self.root.rmdir()  # only when no newer buffer has its folder in it
        except OSError:
            pass

    def stop(self) -> None:
        self.stop_event.set()

    def span(self) -> tuple[float, float] | None:
        """(first, last) stream second held right now."""
        with self._lock:
            return (self.pieces[0]["t"], self.pieces[-1]["t"] + self.pieces[-1]["d"]) if self.pieces else None

    def cut(self, start_s: float, end_s: float, out: Path) -> float:
        """Write [start_s, end_s] of the stream to ``out`` (mp4) and return the stream second it really starts at.

        Whole pieces are joined without re-encoding, so the file starts at the piece boundary just before start_s.
        """
        with self._lock:
            have = list(self.pieces)
        picked = choose_pieces(have, start_s, end_s)
        out.parent.mkdir(parents=True, exist_ok=True)
        joined = out.with_name(out.stem + ".part.ts")
        part = out.with_name(out.stem + ".part.mp4")
        try:
            with joined.open("wb") as fh:
                head = None
                for p in picked:
                    try:
                        if p.get("init") and p["init"] != head:  # the newer kind of feed: its header piece goes first
                            head = p["init"]
                            fh.write(Path(head).read_bytes())
                        fh.write(Path(p["file"]).read_bytes())
                    except FileNotFoundError:
                        raise ToolError("this moment is no longer in the live buffer (it only keeps the last few minutes)") from None
            run([find_tool("ffmpeg", self.cfg.data_dir), "-hide_banner", "-v", "error", "-y", "-i", str(joined),
                 "-c", "copy", "-movflags", "+faststart", str(part)], timeout_s=180, what="joining the live buffer")
            os.replace(part, out)
        finally:
            joined.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
        return float(picked[0]["t"])
