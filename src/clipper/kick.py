"""Finished Kick videos: their details, and a chosen part of them.

Kick's own page asks ``kick.com/api/v1/video/<id>`` for a video's details, which include the address of its
stream files. This module asks the same question under an honest client name, picks the picture size wanted,
and lets FFmpeg read just the part asked for. Only that part is downloaded, never the whole video.

A Kick video has no chat replay, so moments are found from sound, picture or AI reading, or picked by hand.
Live Kick streams are not watched.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path

from .config import Config
from .tools import ToolError, find_tool, run

log = logging.getLogger(__name__)

API = "https://kick.com/api/v1/video/"
CLIENT = "stream-clipper/1.0"


def _get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": CLIENT, "Accept": "application/json, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(8_000_000)


def details(video_id: str) -> dict:
    """What Kick says about a video: {"title", "channel", "channel_name", "duration_s", "is_live", "playlist", "started_at"}."""
    try:
        data = json.loads(_get(API + video_id))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ToolError("Kick has no video with that link. It may have been deleted, or it is a clip or a channel link: open the video itself and copy its link") from e
        raise ToolError(f"Kick did not give this video's details (error {e.code}). Try again in a minute") from e
    except (OSError, ValueError) as e:
        raise ToolError(f"Kick could not be reached for this video's details ({e})") from e
    live = data.get("livestream") or {}
    channel = live.get("channel") or {}
    playlist = str(data.get("source") or "")
    if not playlist:
        raise ToolError("Kick did not say where this video's files are; it may be private or still being processed")
    if not playlist.endswith(".m3u8"):
        playlist = playlist.rstrip("/") + "/media/hls/master.m3u8"
    return {
        "title": str(live.get("session_title") or ""), "channel": str(channel.get("slug") or ""),
        "channel_name": str((channel.get("user") or {}).get("username") or channel.get("slug") or ""),
        "duration_s": round(float(live.get("duration") or 0) / 1000, 1), "is_live": bool(live.get("is_live")),
        "playlist": playlist, "started_at": live.get("start_time"),
    }


def pick_variant(master_text: str, base_url: str, max_height: int) -> str:
    """The address of the best picture size not taller than ``max_height`` (the smallest there is, if all are taller)."""
    found: list[tuple[int, str]] = []
    lines = master_text.splitlines()
    for i, line in enumerate(lines):
        m = re.search(r"RESOLUTION=\d+x(\d+)", line)
        if line.startswith("#EXT-X-STREAM-INF") and m:
            address = next((x.strip() for x in lines[i + 1:] if x.strip() and not x.startswith("#")), "")
            if address:
                found.append((int(m.group(1)), address))
    if not found:
        return base_url  # not a list of sizes: it is the stream itself
    fits = [f for f in found if f[0] <= max_height]
    height, address = max(fits) if fits else min(found)
    return address if re.match(r"https?://", address) else base_url.rsplit("/", 1)[0] + "/" + address


def stream_address(playlist: str, max_height: int) -> str:
    try:
        return pick_variant(_get(playlist).decode("utf-8", errors="replace"), playlist, max_height)
    except OSError as e:
        raise ToolError(f"this Kick video's files could not be reached ({e}). Old Kick videos are deleted after a while") from e


def fetch_range(playlist: str, start_s: float, end_s: float, part: Path, cfg: Config, on_line=None) -> None:
    """Download just [start_s, end_s] of a Kick video into ``part`` (mp4), cut exactly at the times asked."""
    ffmpeg = find_tool("ffmpeg", cfg.data_dir)
    length = end_s - start_s
    address = stream_address(playlist, cfg.fetch.max_height)
    # Re-encoded, not copied: a copy can only start where the stream happens to have a full picture, seconds off.
    run([ffmpeg, "-hide_banner", "-v", "error", "-stats", "-y", "-user_agent", CLIENT, "-ss", f"{start_s:.3f}", "-i", address, "-t", f"{length:.3f}",
         "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "17", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(part)],
        timeout_s=max(cfg.fetch.timeout_s, 600 + 4 * length), retries=cfg.fetch.retries, retry_wait_s=cfg.fetch.retry_wait_s,
        what="Kick range download", on_line=on_line)
