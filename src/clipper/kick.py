"""Kick: finished videos, live streams and chat.

Kick's own page asks ``kick.com/api/v1/video/<id>`` for a video's details, which include the address of its
stream files. This module asks the same question under an honest client name, picks the picture size wanted,
and lets FFmpeg read just the part asked for. Only that part is downloaded, never the whole video.

Chat comes from the same place Kick's own page reads it: 25 messages at a time, by time. That gives the chat replay
of a finished stream and, asked every couple of seconds, the chat of a live one.

A live channel is watched like a Twitch one: Kick records the broadcast as it runs (clips are cut from that
recording), and when a streamer has recordings switched off the live feed's last minutes are kept instead.
Everything is read-only and anonymous; nothing is posted.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
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
        "chat_id": channel.get("id") or live.get("channel_id"), "started_at": _epoch(live.get("start_time") or live.get("created_at")),
        "title": str(live.get("session_title") or ""), "channel": str(channel.get("slug") or ""),
        "channel_name": str((channel.get("user") or {}).get("username") or channel.get("slug") or ""),
        "duration_s": round(float(live.get("duration") or 0) / 1000, 1), "is_live": bool(live.get("is_live")),
        "playlist": playlist,
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


# ---- channels, live streams and chat ----

CHANNELS = "https://kick.com/api/v2/channels/"
NAME = "kick.com/"  # a Kick channel is watched under the name "kick.com/<its name>"
_SLUG = re.compile(r"kick\.com/([A-Za-z0-9_-]{2,40})(?:[/?#]|$)", re.IGNORECASE)
_EMOTE = re.compile(r"\[emote:\d+:([^\]]*)\]")
PAGE = 25  # chat messages Kick gives per question


def slug_of(value: str) -> str | None:
    """'kick.com/Name' or a channel address -> 'name'; None when it is not a Kick channel."""
    m = _SLUG.search(str(value).strip())
    if not m or m.group(1).lower() in ("video", "videos", "api", "category", "categories", "browse", "following"):
        return None
    return m.group(1).lower()


def _epoch(text) -> float | None:
    """Kick's times ('2026-11-20 16:30:48' or with a T and a zone, always UTC) as epoch seconds."""
    from datetime import datetime, timezone

    try:
        when = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).timestamp()


def _ask(url: str, what: str, tries: int = 3):
    """One question to Kick, answered as JSON. Failed questions are tried again; "no such thing" gives None.

    When Kick says it is being asked too often, the next try waits as long as Kick asks (or a quarter of a minute more each time).
    """
    last: Exception | None = None
    for attempt in range(tries):
        wait = 1.5 * (attempt + 1)
        try:
            return json.loads(_get(url))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
            if e.code == 429:
                try:
                    wait = min(120.0, float(e.headers.get("Retry-After") or 0)) or 15.0 * (attempt + 1)
                except (TypeError, ValueError):
                    wait = 15.0 * (attempt + 1)
                if attempt + 1 < tries:
                    log.info("Kick says it is being asked too often (%s); waiting %.0f seconds, then carrying on", what, wait)
        except (OSError, ValueError) as e:
            last = e
        if attempt + 1 < tries:
            time.sleep(wait)
    raise ToolError(f"Kick did not answer for {what} ({last}). Try again in a few minutes")


def channel(slug: str) -> dict | None:
    """A channel as Kick describes it now, or None when there is no such channel.

    {"id", "slug", "name", "records", "feed", "live": None or {"id", "title", "started_at"}}
    """
    data = _ask(CHANNELS + slug, f"the channel {slug}")
    if not isinstance(data, dict) or not data.get("id"):
        return None
    now = data.get("livestream") or {}
    live = None
    if now.get("is_live"):
        live = {"id": str(now.get("id") or ""), "title": str(now.get("session_title") or ""), "started_at": _epoch(now.get("start_time") or now.get("created_at"))}
    return {"id": int(data["id"]), "slug": str(data.get("slug") or slug), "name": str((data.get("user") or {}).get("username") or slug),
            "records": bool(data.get("vod_enabled")), "feed": str(data.get("playback_url") or ""), "live": live}


def live_recording(slug: str) -> dict | None:
    """The recording Kick is making of the channel's broadcast right now: {"uuid", "playlist"}; None when there is none."""
    videos = _ask(CHANNELS + slug + "/videos", f"{slug}'s videos") or []
    for v in videos if isinstance(videos, list) else []:
        uuid = str((v.get("video") or {}).get("uuid") or "")
        if v.get("is_live") and v.get("source") and uuid:
            return {"uuid": uuid.lower(), "playlist": str(v["source"])}
    return None


def playlist_times(playlist: str) -> tuple[float | None, float]:
    """(when second 0 of a Kick video was broadcast, as epoch seconds; how many seconds it holds so far)."""
    from datetime import datetime

    text = _get(stream_address(playlist, 0)).decode("utf-8", errors="replace")
    first = re.search(r"#EXT-X-PROGRAM-DATE-TIME:(\S+)", text)
    start = None
    if first:
        try:
            start = datetime.fromisoformat(first.group(1).replace("Z", "+00:00")).timestamp()
        except ValueError:
            start = None
    return start, sum(float(x) for x in re.findall(r"#EXTINF:([0-9.]+)", text))


def live_feed(slug: str, max_height: int) -> str:
    """Address of the channel's live feed, at the best picture size not taller than ``max_height``."""
    found = channel(slug)
    if not found or not found["live"] or not found["feed"]:
        raise ToolError(f"{NAME}{slug} is not live")
    return stream_address(found["feed"], max_height)


def chat_text(content: str) -> str:
    """A chat message as plain words: Kick writes an emote as [emote:123:name]; only its name is kept."""
    return " ".join(_EMOTE.sub(r" \1 ", str(content or "")).split())


def chat_page(channel_id: int, before: int | None = None, tries: int = 3) -> tuple[list[dict], int | None]:
    """Up to 25 chat messages sent before ``before`` (microseconds; now when None), and where the next older page starts.

    Each message is {"id", "at" (epoch seconds), "author", "text"}.
    """
    data = _ask(f"{CHANNELS}{int(channel_id)}/messages" + (f"?cursor={int(before)}" if before else ""), "the chat", tries) or {}
    data = data.get("data") or {}
    out = []
    for m in data.get("messages") or []:
        at = _epoch(m.get("created_at"))
        text = chat_text(m.get("content"))
        if at is not None and text and m.get("type", "message") in ("message", "reply"):
            out.append({"id": str(m.get("id") or ""), "at": at, "author": str((m.get("sender") or {}).get("username") or ""), "text": text})
    try:
        older = int(data.get("cursor")) if data.get("cursor") else None
    except (TypeError, ValueError):
        older = None
    return out, older


def chat_between(channel_id: int, start: float, end: float, on_progress=None, workers: int = 4, slice_s: float = 600.0,
                 keep: Path | None = None) -> list[dict]:
    """Every chat message sent between two moments (epoch seconds), oldest first.

    Kick hands chat out 25 messages at a time, going backwards. The stretch is cut into ten-minute slices that are
    read a few at a time. Each finished slice is written to ``keep`` at once, so a download that stops (the internet
    drops, Kick stops answering) continues from there on the next try instead of starting again.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    edges = []
    t = start
    while t < end:
        edges.append((t, min(end, t + slice_s)))
        t += slice_s

    def read(a: float, b: float) -> list[dict]:
        got, before = [], int((b + 1) * 1_000_000)
        for _ in range(4000):  # a ten-minute slice of the busiest chat there is stays far below this
            page, older = chat_page(channel_id, before, tries=7)
            got += [m for m in page if a <= m["at"] <= b]
            if not page or older is None or older >= before or min(m["at"] for m in page) < a:
                break
            before = older
        return got

    seen: dict[str, dict] = {}
    had: set[int] = set()  # slices read on an earlier try
    if keep is not None and keep.exists():
        for line in keep.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
                if row["for"] == [channel_id, start, end, slice_s]:
                    had.add(int(row["slice"]))
                    for m in row["messages"]:
                        seen[m["id"] or f"{m['at']}{m['author']}{m['text']}"] = m
            except (ValueError, KeyError, TypeError):
                continue  # a line cut short when the earlier try stopped
    if had:
        log.info("Kick chat: continuing an earlier download (%d of %d parts were already read)", len(had), len(edges))
    done_n = len(had)
    todo = [(n, a, b) for n, (a, b) in enumerate(edges) if n not in had]
    pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="kickchat")
    try:
        jobs = {pool.submit(read, a, b): n for n, a, b in todo}
        for job in as_completed(jobs):
            got = job.result()
            for m in got:
                seen[m["id"] or f"{m['at']}{m['author']}{m['text']}"] = m
            if keep is not None:
                with keep.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"for": [channel_id, start, end, slice_s], "slice": jobs[job], "messages": got}, ensure_ascii=False) + "\n")
            done_n += 1
            if on_progress:
                on_progress(done_n / len(edges))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # after a failure the other slices are not asked for any more
    return sorted(seen.values(), key=lambda m: m["at"])


def save_chat(channel_id: int, start: float, length_s: float, out: Path, on_progress=None) -> int:
    """Write a stream's whole chat to ``out`` with times counted from the video's start; returns how many messages."""
    keep = out.with_name("chat.reading.jsonl")
    messages = chat_between(channel_id, start, start + length_s, on_progress, keep=keep)
    with out.open("w", encoding="utf-8") as fh:
        for m in messages:
            # "author" first on purpose: a chat file that starts with "t" is taken for one collected by the live watcher.
            fh.write(json.dumps({"author": m["author"], "text": m["text"], "t": round(m["at"] - start, 2)}, ensure_ascii=False) + "\n")
    keep.unlink(missing_ok=True)
    return len(messages)


class KickChatReader(threading.Thread):
    """Reads one Kick channel's chat while it is live and appends it to <work>/chat.json as JSON lines.

    Kick's page gets chat over a connection whose address changes without notice, so the newest messages are simply
    asked for every couple of seconds instead; when chat is faster than one page, older pages are asked for too.
    """

    POLL_S = 2.5
    MAX_PAGES = 8

    def __init__(self, slug: str, channel_id: int, out: Path, stream_start: float) -> None:
        super().__init__(daemon=True, name=f"kickchat-{slug}")
        self.channel, self.channel_id, self.out, self.stream_start = NAME + slug, channel_id, out, stream_start
        self.stop_event = threading.Event()
        self.messages = 0
        self.connected_at: float | None = None
        self._seen: dict[str, None] = {}

    def fresh(self) -> list[dict]:
        """Messages not seen before, oldest first."""
        new: list[dict] = []
        before = None
        for _ in range(self.MAX_PAGES):
            page, older = chat_page(self.channel_id, before)
            unseen = [m for m in page if m["id"] not in self._seen]
            new += unseen
            if len(unseen) < len(page) or len(page) < PAGE or older is None or self.connected_at is None:
                break  # reached messages already read (or this is the first look: what was said before is not ours)
            before = older
        for m in new:
            self._seen[m["id"]] = None
        for old in list(self._seen)[:-4000]:
            del self._seen[old]
        return sorted(new, key=lambda m: m["at"])

    def run(self) -> None:
        wait = self.POLL_S
        while not self.stop_event.is_set():
            try:
                first = self.connected_at is None
                new = self.fresh()
                if first:
                    self.connected_at = time.time()
                    new = [m for m in new if m["at"] >= self.connected_at - 5]
                if new:
                    with self.out.open("a", encoding="utf-8") as fh:
                        for m in new:
                            fh.write(json.dumps({"t": round(m["at"] - self.stream_start, 2), "author": m["author"], "text": m["text"]}, ensure_ascii=False) + "\n")
                    self.messages += len(new)
                wait = self.POLL_S
            except Exception as e:  # Kick not answering for a moment is normal; ask again
                wait = min(max(wait, 5.0) * 2, 60.0)
                log.warning("%s: chat could not be read (%s); trying again in %.0fs", self.channel, e, wait)
            self.stop_event.wait(wait)

    def stop(self) -> None:
        self.stop_event.set()
