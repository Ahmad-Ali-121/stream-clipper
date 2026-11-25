"""Watching live YouTube streams: who is live, their chat as it happens, and cutting clips out of the live feed.

YouTube differs from Twitch in three ways that matter here:
  * There is no recording to fetch ranges from while a stream is running. But the live feed's own lists hold the
    last quarter of an hour, so a clip's pieces are simply downloaded when its moment is found. Nothing is copied
    in the background.
  * The live feed comes as two separate lists, picture only and sound only, which are joined when a clip is cut.
  * The feed's own clock counts seconds of video sent, which can run minutes behind real time (pauses, restarts),
    while chat is stamped with real time. Each piece is therefore placed on the real-time line by when it
    became available, so a chat spike and the video it reacted to line up.

Chat is read by running yt-dlp's live-chat download, which keeps writing chat lines to a file; that file is followed.
Everything is read-only and anonymous. Nothing is posted.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from .buffer import _get, choose_pieces, parse_playlist
from .chat import _youtube_text
from .config import Config
from .sources import Source
from .tools import ToolError, find_tool, run

log = logging.getLogger(__name__)

_HANDLE = re.compile(r"@[A-Za-z0-9._-]{3,30}")
_VIDEO = re.compile(r"(?:youtu\.be/|[?&]v=|/live/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")


def is_youtube(value: str) -> bool:
    v = value.strip()
    return v.startswith("@") or "youtube.com" in v.lower() or "youtu.be" in v.lower()


def channel_spec(value: str) -> tuple[str, str]:
    """('@handle' or a video id, the address to ask YouTube about) for what a person typed.

    Accepts @handle, a channel address (youtube.com/@handle), or the address of the live stream itself.
    """
    v = value.strip()
    m = _VIDEO.search(v)
    if m:
        return m.group(1), f"https://www.youtube.com/watch?v={m.group(1)}"
    m = _HANDLE.search(v)
    if m:
        return m.group(0), f"https://www.youtube.com/{m.group(0)}/live"
    raise ValueError(f"{value!r} is not a YouTube channel. Type its handle, like @channelname, or paste the address of the live stream.")


def find_live(spec: str, cfg: Config) -> tuple[Source, dict] | None:
    """The channel's current live stream as a source with its details, or None when it is not live."""
    from .fetch import _ytdlp

    _name, url = channel_spec(spec)
    try:
        proc = run(_ytdlp(cfg) + ["-J", "--skip-download", url], timeout_s=120, retries=0, what=f"{spec} live details")
        info = json.loads(proc.stdout)
    except ToolError as e:
        if "not currently live" not in str(e) and "live event will begin" not in str(e):
            log.warning("%s: could not check whether it is live (%s)", spec, str(e)[:300])
        return None
    except ValueError:
        return None
    start = info.get("release_timestamp") or info.get("timestamp")
    if not info.get("is_live") or not info.get("id") or not start:
        return None
    watch_url = f"https://www.youtube.com/watch?v={info['id']}"
    handle = info.get("uploader_id") or ""
    meta = {
        "url": watch_url, "platform": "youtube", "video_id": info["id"], "title": info.get("title") or "",
        "channel": info.get("channel") or info.get("uploader") or spec, "channel_id": handle or info.get("channel_id") or "",
        "channel_url": f"https://www.youtube.com/{handle}" if handle else (info.get("channel_url") or watch_url),
        "duration_s": None, "is_live": True, "started_at": float(start), "no_vod": True,
    }
    return Source("youtube", info["id"], watch_url), meta


def chat_line(obj: dict) -> tuple[float, str, str] | None:
    """(time in epoch seconds, author, text) from one line of yt-dlp's live chat file, or None for anything else."""
    for action in (obj.get("replayChatItemAction") or {}).get("actions") or [obj]:
        item = (action.get("addChatItemAction") or {}).get("item") or {}
        r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
        if not r or not r.get("timestampUsec"):
            continue
        text = _youtube_text(r)
        if text:
            return int(r["timestampUsec"]) / 1e6, (r.get("authorName") or {}).get("simpleText") or "", text
    return None


class YouTubeChatReader(threading.Thread):
    """Follows one live stream's chat and appends it to <work>/chat.json with times relative to the stream start."""

    def __init__(self, url: str, out: Path, stream_start: float, cfg: Config, label: str = "") -> None:
        super().__init__(daemon=True, name=f"ytchat-{label}")
        self.url, self.out, self.stream_start, self.cfg, self.channel = url, out, stream_start, cfg, label
        self.stop_event = threading.Event()
        self.messages = 0
        self.connected_at: float | None = None
        self.proc: subprocess.Popen | None = None
        self.gaps: list[tuple[float, float]] = []  # stretches (stream seconds) in which chat could not be read
        self._lost_at: float | None = None

    def run(self) -> None:
        from .fetch import _ytdlp

        import shutil

        base = self.out.parent / "livechat"
        wait = 5.0
        while not self.stop_event.is_set():
            # A fresh folder each time: a reader left over from a run that was killed may still hold the old file open.
            shutil.rmtree(base, ignore_errors=True)
            folder = base / time.strftime("%H%M%S")
            try:
                folder.mkdir(parents=True, exist_ok=True)
                # What it complains about goes to a file, not a pipe: a pipe nobody reads fills up after a few
                # thousand letters of warnings and the reader then stands still without a word.
                errors = folder / "errors.txt"
                with errors.open("w", encoding="utf-8", errors="replace") as sink:
                    self.proc = subprocess.Popen(
                        _ytdlp(self.cfg) + ["--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o", str(folder / "chat"), self.url],
                        stdout=subprocess.DEVNULL, stderr=sink, stdin=subprocess.DEVNULL)
                self.connected_at = time.time()
                if self._lost_at is not None and self.connected_at - self._lost_at >= 20:
                    self.gaps.append((self._lost_at - self.stream_start, self.connected_at - self.stream_start))
                self._lost_at = None
                started = time.time()
                self._follow(folder)
                if not self.stop_event.is_set():
                    self._lost_at = time.time()  # the reader ended by itself: chat is not being read until it runs again
                err = errors.read_text(encoding="utf-8", errors="replace").strip()[-300:] if self.proc.poll() is not None else ""
                if not self.stop_event.is_set():
                    log.warning("%s: the chat reader stopped (%s); starting it again in %.0fs", self.channel, err or "no reason given", wait)
                if time.time() - started > 120:
                    wait = 5.0
            except Exception as e:  # a dropped connection is normal; start again
                log.warning("%s: chat problem (%s); trying again in %.0fs", self.channel, e, wait)
            finally:
                self._kill()
            if self.stop_event.wait(wait):
                break
            wait = min(wait * 2, 60.0)

    def _follow(self, folder: Path) -> None:
        """Read new lines from the chat file yt-dlp is writing until it stops or we are told to."""
        position, src = 0, None
        with self.out.open("a", encoding="utf-8") as fh:
            while not self.stop_event.is_set():
                if src is None:
                    found = sorted(folder.glob("chat*live_chat*"))
                    src = found[0] if found else None
                if src is not None and src.exists():
                    with src.open("rb") as f:
                        f.seek(position)
                        chunk = f.read()
                    end = chunk.rfind(b"\n")
                    if end >= 0:  # only whole lines; a half-written last line waits for the next round
                        position += end + 1
                        for raw in chunk[: end + 1].splitlines():
                            try:
                                got = chat_line(json.loads(raw))
                            except ValueError:
                                continue
                            if got:
                                at, author, text = got
                                fh.write(json.dumps({"t": round(at - self.stream_start, 2), "author": author, "text": text}, ensure_ascii=False) + "\n")
                                self.messages += 1
                        fh.flush()
                if self.proc.poll() is not None:
                    return
                self.stop_event.wait(2.0)

    def _kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)
            else:
                self.proc.terminate()

    def stop(self) -> None:
        self.stop_event.set()
        self._kill()


def feed_urls(url: str, cfg: Config) -> tuple[str, str]:
    """(picture list, sound list) for the best live quality within fetch.max_height."""
    from .fetch import _ytdlp

    h = cfg.fetch.max_height
    proc = run(_ytdlp(cfg) + ["-g", "-f", f"bv*[height<={h}][protocol^=m3u8]+ba[protocol^=m3u8]/bv*[height<={h}]+ba", url],
               timeout_s=120, retries=1, retry_wait_s=5, what="live feed address")
    found = [x for x in proc.stdout.strip().splitlines() if x.startswith("http")]
    if len(found) < 2:
        raise ToolError("this live stream gave no separate picture and sound feeds to copy from")
    return found[0], found[1]


class YouTubeWindow:
    """Cuts clips straight from a live YouTube stream, fetching only the pieces a clip needs.

    YouTube's live lists already hold the last quarter of an hour (two-second pieces), so nothing is copied
    in the background: when a moment is found, its pieces are downloaded, joined, and that is all.
    It offers the same ``cut`` / ``span`` / ``stop`` as the Twitch live buffer, so the watcher treats them alike.
    """

    REFRESH_S = 1800  # feed addresses expire after some hours; ask for fresh ones well before that
    # A live feed sometimes skips a few seconds (the encoder restarts, an advert marker). A clip may run across
    # such a skip: it just jumps there, as it did for viewers. A longer hole means the moment is really missing.
    SKIP_OK_S = 6.0

    def __init__(self, url: str, stream_start: float, cfg: Config, label: str = "") -> None:
        self.url, self.stream_start, self.cfg, self.channel = url, stream_start, cfg, label or url
        self.bytes = 0
        self.adverts = 0
        self._urls: tuple[str, str] | None = None
        self._urls_at = 0.0
        self._lock = threading.Lock()

    def _lists(self) -> tuple[list, list, float]:
        """(picture pieces, sound pieces, seconds real time is ahead of the feed's own clock), read now."""
        for attempt in range(2):
            with self._lock:
                if self._urls is None or time.time() - self._urls_at > self.REFRESH_S:
                    self._urls, self._urls_at = feed_urls(self.url, self.cfg), time.time()
                v_url, a_url = self._urls
            try:
                now = time.time()
                video = parse_playlist(_get(v_url).decode("utf-8", "replace"), v_url)
                audio = parse_playlist(_get(a_url).decode("utf-8", "replace"), a_url)
            except Exception as e:
                with self._lock:
                    self._urls = None  # the addresses may have expired; one fresh try
                if attempt:
                    raise ToolError(f"the live stream's feed could not be read ({e})") from e
                continue
            if not video or not audio:
                raise ToolError("the live stream's feed lists are empty (the stream may have ended)")
            # The newest piece in the list was made available just now, so its end is "now" on the real-time line.
            lag = now - (video[-1].at + video[-1].duration)
            return video, audio, lag
        raise ToolError("the live stream's feed could not be read")

    def _rows(self) -> tuple[list[dict], dict[int, object]]:
        video, audio, lag = self._lists()
        sound = {int(round(p.at * 1000)): p for p in audio}
        rows = [{"t": round(p.at + lag - self.stream_start, 3), "d": p.duration, "key": int(round(p.at * 1000)), "piece": p}
                for p in video if int(round(p.at * 1000)) in sound]
        return rows, sound

    def span(self) -> tuple[float, float] | None:
        rows, _ = self._rows()
        return (rows[0]["t"], rows[-1]["t"] + rows[-1]["d"]) if rows else None

    def cut(self, start_s: float, end_s: float, out: Path) -> float:
        """Write [start_s, end_s] of the stream to ``out`` (mp4) and return the stream second it really starts at."""
        import shutil

        rows, sound = self._rows()
        picked = choose_pieces(rows, start_s, end_s, slack=self.SKIP_OK_S)
        out.parent.mkdir(parents=True, exist_ok=True)
        folder = out.with_name(out.stem + ".pieces")
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        part = out.with_name(out.stem + ".part.mp4")
        try:
            head = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:3", "#EXT-X-MEDIA-SEQUENCE:0"]
            lists = {"v": list(head), "a": list(head)}
            inits: dict = {"v": {}, "a": {}}
            last_init = {"v": "", "a": ""}
            for n, row in enumerate(picked):
                if n and row["t"] - (picked[n - 1]["t"] + picked[n - 1]["d"]) > 0.5:
                    for kind in ("v", "a"):  # tell FFmpeg the clock jumps here, so picture and sound stay in step
                        lists[kind].append("#EXT-X-DISCONTINUITY")
                    log.info("%s: the live feed skips %.1f s inside this clip; the clip jumps there too", self.channel,
                             row["t"] - (picked[n - 1]["t"] + picked[n - 1]["d"]))
                for kind, piece, ext in (("v", row["piece"], "ts"), ("a", sound[row["key"]], "aac")):
                    def fetched(url: str) -> bytes:
                        for attempt in range(3):
                            try:
                                return _get(url, timeout=30)
                            except Exception as e:
                                if attempt == 2:
                                    raise ToolError(f"a piece of this moment could not be downloaded ({e})") from e
                                time.sleep(1.0)
                        return b""

                    init = getattr(piece, "init", "") or ""
                    if init:
                        # The newer kind of feed: every piece needs the feed's header piece in front of it. It is
                        # fetched once and named in the list, as the feed's own list does.
                        ext = "m4s"
                        if init not in inits[kind]:
                            head_name = f"{kind}init{len(inits[kind])}.mp4"
                            (folder / head_name).write_bytes(fetched(init))
                            inits[kind][init] = head_name
                        if last_init[kind] != init:
                            last_init[kind] = init
                            lists[kind].append(f'#EXT-X-MAP:URI="{inits[kind][init]}"')
                    data = fetched(piece.url)
                    name = f"{kind}{n:04d}.{ext}"
                    (folder / name).write_bytes(data)
                    self.bytes += len(data)
                    lists[kind] += [f"#EXTINF:{piece.duration:.3f},", name]
            for kind in ("v", "a"):
                (folder / f"{kind}.m3u8").write_text("\n".join(lists[kind] + ["#EXT-X-ENDLIST", ""]), encoding="utf-8")
            # The pieces are read back through FFmpeg's own playlist reader, which understands YouTube's sound pieces
            # (plain AAC with a time tag in front of each) and keeps picture and sound in step.
            run([find_tool("ffmpeg", self.cfg.data_dir), "-hide_banner", "-v", "error", "-y",
                 "-allowed_extensions", "ALL", "-i", "v.m3u8", "-allowed_extensions", "ALL", "-i", "a.m3u8",
                 "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart", str(part)],
                timeout_s=180, what="joining the clip's pieces", cwd=folder)
            os.replace(part, out)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            part.unlink(missing_ok=True)
        return float(picked[0]["t"])

    def start(self) -> None:  # nothing runs in the background; these exist so the watcher can treat it like the Twitch buffer
        pass

    def stop(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        pass
