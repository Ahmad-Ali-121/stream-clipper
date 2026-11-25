"""Watch live streams (Twitch, Kick, YouTube) and make draft clips while they are still running.

For each channel:
  1. Find the VOD Twitch is recording for the current broadcast.
  2. Read chat in real time over Twitch's public chat connection (anonymous, read-only)
     and append it to the stream's chat file with times relative to the stream start.
  3. Every poll: score the chat using past data only, and for each new moment
     fetch just that range from the in-progress VOD, transcribe it and render a draft.

Drafts are left as "candidate" for the human review page. Nothing is posted.
Everything is on disk, so stopping and restarting continues where it left off.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import replace
from pathlib import Path

from .chat import load_chat
from .config import Config
from .fetch import fetch_meta, fetch_range, probe
from .moments import bucket_chat, format_time, pick_moments, score_buckets
from .sources import Source, parse_source
from .tools import ToolError, run
from .work import clip_dir, clip_id, done, free_gb, read_json, save_clip, work_dir, write_json

log = logging.getLogger(__name__)

IRC_URL = "wss://irc-ws.chat.twitch.tv:443"
_PRIVMSG = re.compile(r"^(?:@\S+ )?:([^!\s]+)!\S+ PRIVMSG #\S+ :(.*)$")


def channel_name(value: str) -> str:
    """What a person typed, as the name a channel is watched under.

    Twitch: 'https://www.twitch.tv/Lacy' or 'lacy' -> 'lacy'.
    YouTube: '@handle' or a channel address -> '@handle'; the address of a live stream -> that address.
    Kick: 'kick.com/Name' or its full address -> 'kick.com/name'.
    """
    from . import kick
    from .youtube import channel_spec, is_youtube

    if "kick.com" in value.lower():
        slug = kick.slug_of(value)
        if not slug:
            raise ValueError(f"{value!r} is not a Kick channel. Type it like kick.com/name")
        return kick.NAME + slug

    if is_youtube(value):
        name, url = channel_spec(value)
        return name if name.startswith("@") else url
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", value)
    name = (m.group(1) if m else value).strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{2,25}", name) or name == "videos":
        raise ValueError(f"{value!r} is not a channel. Type a Twitch name (like lacy), a Kick channel (like kick.com/name), or a YouTube handle (like @channelname).")
    return name


def parse_irc_line(line: str) -> tuple[str, str] | None:
    """(author, text) for a chat message line, else None."""
    m = _PRIVMSG.match(line)
    return (m.group(1), m.group(2)) if m else None


def find_live_vod(channel: str, cfg: Config) -> tuple[Source, dict] | None:
    """The VOD being recorded for the channel's current broadcast, or None if offline or VODs are off."""
    from .fetch import _ytdlp

    try:
        proc = run(
            _ytdlp(cfg) + ["--flat-playlist", "--playlist-end", "1", "--print", "%(id)s",
                           f"https://www.twitch.tv/{channel}/videos?filter=archives&sort=time"],
            timeout_s=120, retries=2, retry_wait_s=5, what=f"{channel} VOD list",
        )
    except ToolError as e:
        log.warning("%s: could not list VODs (%s)", channel, e)
        return None
    ids = [x.strip().lstrip("v") for x in proc.stdout.splitlines() if x.strip()]
    if not ids:
        return None
    source = parse_source(ids[0])
    work = work_dir(cfg.data_dir, source)
    (work / "meta.json").unlink(missing_ok=True)  # always re-read: a live VOD's length keeps changing
    meta = fetch_meta(source, work, cfg)
    if not meta.get("is_live"):
        return None
    return source, meta


def channel_is_live(channel: str, cfg: Config) -> bool:
    """True when the channel is broadcasting right now, whether or not Twitch is recording it."""
    from .fetch import _ytdlp

    try:
        proc = run(
            _ytdlp(cfg) + ["--skip-download", "--print", "%(is_live)s", f"https://www.twitch.tv/{channel}"],
            timeout_s=60, retries=0, retry_wait_s=3, what=f"{channel} live check",
        )
    except ToolError as e:
        if "not currently live" not in str(e):  # that message is the normal answer for an offline channel
            log.warning("%s: could not check whether it is live (%s)", channel, e)
        return False
    return proc.stdout.strip().splitlines()[-1:] == ["True"]


NO_RECORDING = (
    "%s: LIVE, but Twitch is not recording this broadcast and watch.buffer_when_no_vod is off in the settings, "
    "so there is nothing to cut clips from; will check again"
)


class ChatReader(threading.Thread):
    """Reads one channel's chat live and appends it to <work>/chat.json as JSON lines."""

    def __init__(self, channel: str, out: Path, stream_start: float) -> None:
        super().__init__(daemon=True, name=f"chat-{channel}")
        self.channel = channel
        self.out = out
        self.stream_start = stream_start
        self.stop_event = threading.Event()
        self.messages = 0
        self.connected_at: float | None = None
        self.gaps: list[tuple[float, float]] = []  # stretches (stream seconds) in which chat could not be read
        self._lost_at: float | None = None

    def run(self) -> None:
        from websockets.sync.client import connect

        wait = 2.0
        while not self.stop_event.is_set():
            try:
                with connect(IRC_URL, open_timeout=20, close_timeout=5) as ws:
                    ws.send("NICK justinfan73512")  # Twitch's anonymous read-only login
                    ws.send(f"JOIN #{self.channel}")
                    self.connected_at = time.time()
                    if self._lost_at is not None and self.connected_at - self._lost_at >= 20:
                        self.gaps.append((self._lost_at - self.stream_start, self.connected_at - self.stream_start))
                    self._lost_at = None
                    wait = 2.0
                    with self.out.open("a", encoding="utf-8") as fh:
                        while not self.stop_event.is_set():
                            try:
                                data = ws.recv(timeout=30)
                            except TimeoutError:
                                continue
                            now = time.time()
                            for line in str(data).split("\r\n"):
                                if line.startswith("PING"):
                                    ws.send("PONG :tmi.twitch.tv")
                                    continue
                                parsed = parse_irc_line(line)
                                if parsed:
                                    fh.write(json.dumps({"t": round(now - self.stream_start, 2), "author": parsed[0], "text": parsed[1]}, ensure_ascii=False) + "\n")
                                    self.messages += 1
                            fh.flush()
            except Exception as e:  # a dropped connection is normal here; reconnect and carry on
                if self.stop_event.is_set():
                    break
                if self._lost_at is None:
                    self._lost_at = time.time()
                log.warning("%s: chat connection lost (%s); reconnecting in %.0fs", self.channel, e, wait)
                self.stop_event.wait(wait)
                wait = min(wait * 2, 60.0)

    def stop(self) -> None:
        self.stop_event.set()


class Watched:
    def __init__(self, channel: str, source: Source, meta: dict, work: Path, reader: ChatReader, watch_from: float, feed=None, buffer=None) -> None:
        self.channel, self.source, self.meta, self.work, self.reader, self.watch_from = channel, source, meta, work, reader, watch_from
        self.feed = feed  # SignalReader when audio or video is being analysed, else None
        self.buffer = buffer  # LiveBuffer when Twitch is not recording this broadcast, else None
        self.last_live_check = time.time()
        self.not_live = 0  # how many checks in a row said the broadcast is over
        self.ending = 0.0  # set when the broadcast is over and only its drafts in progress are waited for: until when at most
        self.asked: list[dict] = []  # "clip the last N seconds" requests, waiting for the recording to reach them
        self.samples: list[tuple[float, int]] = []  # (time, chat messages read so far), for the hype meter

    def stop(self) -> None:
        self.reader.stop()
        if self.feed:
            self.feed.stop()
        if self.buffer:
            self.buffer.stop()
            self.buffer.join(timeout=10)  # it empties its folder on the way out


def find_unrecorded(channel: str, cfg: Config) -> tuple[Source, dict] | None:
    """A live broadcast Twitch is not recording, as a source with its own work folder; None when offline."""
    from .buffer import live_info

    info = live_info(channel, cfg)
    if not info:
        return None
    url = f"https://www.twitch.tv/{channel}"
    source = Source("twitch", f"live{info['id']}", url)
    meta = {
        "url": url, "platform": "twitch", "video_id": source.video_id, "title": info["title"],
        "channel": info["channel"], "channel_id": channel, "channel_url": url, "duration_s": None,
        "is_live": True, "started_at": info["started_at"], "no_vod": True,
    }
    write_json(work_dir(cfg.data_dir, source) / "meta.json", meta)
    return source, meta


def start_youtube(channel: str, cfg: Config) -> Watched | None:
    """Start watching a YouTube channel's live stream: its chat, and a way to cut clips from the live feed."""
    from .signals import MODES
    from .youtube import YouTubeChatReader, YouTubeWindow, find_live

    found = find_live(channel, cfg)
    if not found:
        return None
    source, meta = found
    meta["title"] = re.sub(r"\s*\d{4}-\d\d-\d\d \d\d:\d\d$", "", meta["title"])  # yt-dlp adds the time it looked
    work = work_dir(cfg.data_dir, source)
    write_json(work / "meta.json", meta)
    start = float(meta["started_at"])
    chat = work / "chat.json"
    if chat.exists() and not chat.read_text(encoding="utf-8", errors="replace").lstrip().startswith('{"t"'):
        chat.replace(work / "chat.downloaded.json")
    reader = YouTubeChatReader(source.url, chat, start, cfg, meta["channel"])
    reader.start()
    watch_from = time.time() - start
    if any(MODES[cfg.watch.mode]):
        log.warning("%s: sound and picture detection is not available for YouTube yet; moments come from chat only", channel)
    log.info("%s: live on YouTube (%s), %s in; watching chat from now. Clips are cut from the live stream itself, which keeps "
             "its last quarter of an hour available", channel, meta["channel"], format_time(watch_from))
    return Watched(channel, source, meta, work, reader, watch_from, None, YouTubeWindow(source.url, start, cfg, meta["channel"]))


KICK_RECORDING_WAIT_S = 180  # how long after a Kick broadcast begins its recording may still be missing from Kick's list


def start_kick(channel: str, cfg: Config) -> Watched | None:
    """Start watching a Kick channel's live stream: its chat, and the recording (or the live feed) to cut clips from."""
    from . import kick
    from .signals import MODES, SignalReader

    slug = channel[len(kick.NAME):]
    found = kick.channel(slug)
    if found is None:
        log.warning("%s: Kick has no channel with that name; check the spelling in Settings", channel)
        return None
    live = found["live"]
    if not live:
        log.info("%s: not live; will check again", channel)
        return None
    address = f"https://kick.com/{slug}"
    recording = kick.live_recording(slug) if found["records"] else None
    start = None
    if found["records"] and not recording and live["started_at"] and time.time() - live["started_at"] < KICK_RECORDING_WAIT_S:
        # Kick lists the recording a little after the broadcast begins. Starting now would lock the whole stream into
        # the costly live-feed copy, so the first minutes are waited out.
        log.info("%s: live on Kick for under %d minutes and its recording is not listed yet; checking again shortly", channel, KICK_RECORDING_WAIT_S // 60)
        return None
    if recording:
        source = Source("kick", recording["uuid"], f"https://kick.com/video/{recording['uuid']}")
        start, _length = kick.playlist_times(recording["playlist"])  # when second 0 of the recording was broadcast
    elif cfg.watch.buffer_when_no_vod:
        source = Source("kick", f"live{live['id']}", address)
    else:
        log.warning("%s: LIVE, but Kick is not recording this broadcast and watch.buffer_when_no_vod is off in the settings, "
                    "so there is nothing to cut clips from; will check again", channel)
        return None
    start = start or live["started_at"]
    if not start:
        log.warning("%s: Kick did not say when this broadcast began; cannot line chat up with the video", channel)
        return None
    meta = {
        "url": source.url, "platform": "kick", "video_id": source.video_id, "title": live["title"], "channel": found["name"],
        "channel_id": slug, "channel_url": address, "duration_s": None, "is_live": True, "started_at": float(start),
        "chat_id": found["id"], "live_id": live["id"], "width": None, "height": None,
    }
    if recording:
        meta["playlist"] = recording["playlist"]
    else:
        meta["no_vod"] = True
    work = work_dir(cfg.data_dir, source)
    write_json(work / "meta.json", meta)
    chat = work / "chat.json"
    if chat.exists() and not chat.read_text(encoding="utf-8", errors="replace").lstrip().startswith('{"t"'):
        chat.replace(work / "chat.downloaded.json")
    reader = kick.KickChatReader(slug, found["id"], chat, float(start))
    reader.start()
    watch_from = time.time() - float(start)
    audio, video = MODES[cfg.watch.mode]
    feed = None
    if audio or video:
        feed = SignalReader(channel, work / "signals.jsonl", float(start), cfg, audio, video)
        feed.start()
    using = " + ".join(["chat"] + ["audio"] * audio + ["video"] * video)
    buffer = None
    if recording:
        log.info("%s: live on Kick, %s in; watching %s from now (recording %s)", channel, format_time(watch_from), using, source.video_id)
    else:
        from .buffer import LiveBuffer

        buffer = LiveBuffer(channel, work / "buffer", float(start), cfg)
        buffer.start()
        log.warning("%s: live on Kick, %s in, but Kick lists no recording of this broadcast. Keeping the last %g minutes of the live feed "
                    "to cut clips from (uses about 3 GB of internet an hour). Watching %s from now", channel, format_time(watch_from),
                    cfg.watch.buffer_minutes, using)
    return Watched(channel, source, meta, work, reader, watch_from, feed, buffer)


def still_live(w: Watched, cfg: Config) -> bool:
    """Is the broadcast this channel is being watched on still running?"""
    if w.source.platform == "kick":
        from . import kick

        found = kick.channel(w.channel[len(kick.NAME):])
        return bool(found and found["live"] and found["live"]["id"] == w.meta.get("live_id"))
    if w.source.platform == "youtube":
        from .youtube import find_live

        found = find_live(w.channel, cfg)
        return bool(found) and found[0].video_id == w.source.video_id
    if w.buffer:
        return bool(channel_is_live(w.channel, cfg))
    found = find_live_vod(w.channel, cfg)
    return bool(found) and found[0].video_id == w.source.video_id  # a new broadcast has a new recording: not this one


def start_channel(channel: str, cfg: Config) -> Watched | None:
    from .youtube import is_youtube

    if channel.startswith("kick.com/"):
        return start_kick(channel, cfg)
    if is_youtube(channel):
        return start_youtube(channel, cfg)
    found = find_live_vod(channel, cfg)
    unrecorded = False
    if not found and cfg.watch.buffer_when_no_vod:
        found = find_unrecorded(channel, cfg)
        unrecorded = bool(found)
    if not found:
        return None
    source, meta = found
    work = work_dir(cfg.data_dir, source)
    start = float(meta.get("started_at") or 0)
    if not start:
        log.warning("%s: no start time for the live VOD; cannot line chat up with the video", channel)
        return None
    chat = work / "chat.json"
    # A chat file downloaded earlier for this VOD (not JSON lines) cannot be appended to; keep it aside.
    if chat.exists() and not chat.read_text(encoding="utf-8", errors="replace").lstrip().startswith('{"t"'):
        chat.replace(work / "chat.downloaded.json")
    reader = ChatReader(channel, chat, start)
    reader.start()
    watch_from = time.time() - start
    from .signals import MODES, SignalReader

    audio, video = MODES[cfg.watch.mode]
    feed = None
    if audio or video:
        feed = SignalReader(channel, work / "signals.jsonl", start, cfg, audio, video)
        feed.start()
    using = " + ".join(["chat"] + ["audio"] * audio + ["video"] * video)
    buffer = None
    if unrecorded:
        from .buffer import LiveBuffer

        buffer = LiveBuffer(channel, work / "buffer", start, cfg)
        buffer.start()
        log.warning(
            "%s: live, %s in, but Twitch is not recording this broadcast. Keeping the last %g minutes of the live feed "
            "to cut clips from (uses about 3 GB of internet an hour; moments during advert breaks are lost). Watching %s from now",
            channel, format_time(watch_from), cfg.watch.buffer_minutes, using,
        )
    else:
        log.info("%s: live, %s in; watching %s from now (VOD %s)", channel, format_time(watch_from), using, source.video_id)
    return Watched(channel, source, meta, work, reader, watch_from, feed, buffer)


def new_moments(w: Watched, cfg: Config) -> list[dict]:
    """Moments not yet turned into clip folders, found using only what was received since the watch began."""
    from .signals import merge_moments, signal_peaks

    chat = w.work / "chat.json"
    messages = [m for m in load_chat(chat) if m.t >= w.watch_from] if done(chat) else []
    s = replace(cfg.moments, mode="trailing", top=1000, skip_start_s=0.0, min_score=cfg.watch.min_score)
    buckets = bucket_chat(messages, s.bucket_s, hype_words=s.hype_words)
    # Bucket indexes start at stream time 0. Drop everything before the watch began,
    # or that empty stretch would count as a silent baseline and make ordinary chat look like a spike.
    buckets = buckets[int(w.watch_from // s.bucket_s) + 1 :]
    # While chat could not be read its buckets are empty, which is not silence. Those buckets are taken out before
    # anything is scored: chat after the break is then measured against the real chat before it, so ordinary chat
    # coming back is not a spike, and a real spike soon after the break is still found.
    gaps = list(getattr(getattr(w, "reader", None), "gaps", []) or [])
    if gaps:
        buckets = [b for b in buckets if not any(b.start_s < end and b.start_s + s.bucket_s > start for start, end in gaps)]
    scores = score_buckets(buckets, s)
    live_now = time.time() - float(w.meta["started_at"])
    found = [m.to_dict() for m in pick_moments(buckets, scores, s)] if messages else []
    if getattr(w, "feed", None):
        found = merge_moments(found, signal_peaks(w.work / "signals.jsonl", cfg, since_s=w.watch_from))
    out = []
    # Every clip of this stream that already exists, whatever was decided about it: as more chat arrives a moment's
    # peak can shift by a few seconds and get a new name, and the same moment must not come back as a "new" clip.
    taken = []
    for other in (w.work / "clips").glob("*/clip.json"):
        st = read_json(other, {})
        real = st.get("status") in ("approved", "rejected", "deleted") or st.get("draft_done") or st.get("exported") or (other.parent / "final.mp4").exists()
        if real and st.get("start_s") is not None and st.get("end_s") is not None:
            taken.append((other.parent.name, float(st["start_s"]), float(st["end_s"]), st.get("status", "candidate")))
    for m in found:
        d = w.work / "clips" / clip_id(m["peak_s"])
        state = read_json(d / "clip.json", {})
        clash = next((t for t in taken if t[0] != d.name and min(m["end_s"], t[2]) - max(m["start_s"], t[1]) > 0.5 * (m["end_s"] - m["start_s"])), None)
        if clash and not state:
            continue  # more than half of it is already a clip (reviewed or not): not a new moment
        # A draft is made once. A clip that was finished, or that a person has decided about, is never made again:
        # its draft video can be missing for good reasons (an edit, a re-render) and remaking it would wipe their work.
        if (done(d / "final.mp4") or state.get("draft_done") or state.get("status") in ("approved", "rejected", "deleted")
                or state.get("exported") or state.get("attempts", 0) >= 3):
            continue
        if m["end_s"] + cfg.fetch.pad_s + cfg.watch.vod_delay_s > live_now:
            continue  # the VOD has not caught up with this moment yet; next poll
        out.append(m)
    return out


def prepare_clip(d: Path, state: dict, cfg: Config) -> dict:
    """Everything between a fetched source.mp4 and a render: measure it, frame it, transcribe it, write its text.

    Also used by ``finish`` to complete a draft that stopped part-way (a crash, a failed download of the speech model).
    """
    from .cuts import apply_snap
    from .layout import auto_layout
    from .pipeline import write_clip_copy
    from .transcribe import transcribe_file

    info = probe(d / "source.mp4", cfg)
    state.update({"source_width": info["width"], "source_height": info["height"], "source_duration_s": round(info["duration_s"], 2)})
    state = auto_layout(d, state, cfg)
    from .fetch import ensure_sound

    ensure_sound(d / "source.mp4", cfg)  # a video with no sound track cannot be listened to at all
    words = transcribe_file(d / "source.mp4", cfg)
    write_json(d / "words.json", words)
    state["transcript"] = " ".join(x["word"] for x in words)
    from . import said

    heard = said.strength([{**x, "word": x.get("native") or x["word"]} for x in words], float(state.get("source_duration_s") or 0) or 60.0)
    if heard:
        state.setdefault("signals", {})["said"] = heard
    state = apply_snap(state, words, max_shift=cfg.fetch.pad_s)
    save_clip(d / "clip.json", state)
    state = write_clip_copy(d, state, cfg)  # hook, title, hashtags: by Claude when a key is set, plain text otherwise
    save_clip(d / "clip.json", state)
    return state


def note_error(d: Path, message: str | None) -> None:
    """Keep the reason a clip could not be made in its clip.json, so the review page can show it; None clears it."""
    from .work import update_clip

    path = d / "clip.json"
    state = read_json(path, {})
    if not state or (message is None and "error" not in state):
        return
    # Only this one key is written, on the clip as it is now: an edit made on the page a moment ago is not undone.
    if message is None:
        update_clip(path, {}, drop=("error",))
    else:
        update_clip(path, {"error": f"{time.strftime('%H:%M:%S')} {message}"})


LIVE_FILE = "live.json"  # what the watcher is doing right now, for the page: who is live, and how hot each chat is
HYPE_LABELS = ((85, "peak hype"), (60, "hot"), (35, "building"), (0, "quiet"))


def hype(samples: list[tuple[float, int]], now: float) -> dict:
    """How busy chat is right now against this stream's own usual pace, 0 to 100.

    ``samples`` are (time, messages read so far). The last 30 seconds are compared with the average 30 seconds of the
    last ten minutes; four times the usual pace is 100. In the first minutes there is no "usual" yet, so a fixed
    scale is used (two messages a second is 100).
    """
    recent = [(t, n) for t, n in samples if now - t <= 600]
    if len(recent) < 2:
        return {"percent": 0, "label": "quiet", "per_minute": 0}
    last = [(t, n) for t, n in recent if now - t <= 30]
    if len(last) < 2:
        last = recent[-2:]  # nothing was sampled for a while (the watcher was busy): use the last two readings there are
    span = max(5.0, recent[-1][0] - last[0][0])
    rate = (recent[-1][1] - last[0][1]) / span  # messages a second, lately
    whole = max(1.0, recent[-1][0] - recent[0][0])
    usual = (recent[-1][1] - recent[0][1]) / whole
    if whole < 120 or usual <= 0:
        percent = 100 * rate / 2.0
    else:
        percent = 100 * rate / (4 * usual)
    percent = int(max(0, min(100, percent)))
    return {"percent": percent, "label": next(text for floor, text in HYPE_LABELS if percent >= floor), "per_minute": round(rate * 60)}


def write_live(data_dir: Path, names: list[str], watched: dict) -> None:
    """Tell the page who is live and how hot each chat is. Never allowed to disturb the watching itself."""
    now = time.time()
    out = {"at": now, "channels": {}}
    for name in names:
        w = watched.get(name)
        if w is None:
            out["channels"][name] = {"live": False}
            continue
        w.samples.append((now, int(getattr(w.reader, "messages", 0))))
        w.samples = [s for s in w.samples if now - s[0] <= 660]
        out["channels"][name] = {"live": True, "title": str(w.meta.get("title") or "")[:80], "waiting": len(w.asked), **hype(w.samples, now)}
    try:
        from .work import write_json as _write

        _write(data_dir / LIVE_FILE, out)
    except OSError as e:
        log.debug("the live status could not be written this time: %s", e)


ASKED_FILE = "clip_now.jsonl"  # the review page writes a line here for each "clip the last N seconds" press


def ask_clip_now(data_dir: Path, seconds: float) -> None:
    """Called by the review page: note that a person wants the last ``seconds`` of every watched stream as a clip."""
    if not 5 <= float(seconds) <= 180:
        raise ValueError("the length must be between 5 and 180 seconds")
    with (data_dir / ASKED_FILE).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": time.time(), "seconds": float(seconds)}) + "\n")


def take_asked(data_dir: Path) -> list[dict]:
    """The requests written since the last look (the file is emptied). Old ones, from before a restart, are dropped."""
    path = data_dir / ASKED_FILE
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        path.unlink()
    except OSError:
        return []  # being written this instant; the next look gets it
    out = []
    for line in lines:
        try:
            r = json.loads(line)
            if time.time() - float(r["at"]) < 300:
                out.append(r)
        except (ValueError, KeyError, TypeError):
            continue
    return out


def asked_moment(w: Watched, request: dict, cfg: Config) -> dict:
    """The stretch of the stream a person asked for: the ``seconds`` before they pressed the button.

    What they were watching runs a few seconds behind the broadcast, so the stretch ends that much earlier and
    starts a little earlier still; the trimmer on the review page takes off what is not wanted.
    """
    pressed = float(request["at"]) - float(w.meta["started_at"])
    end = max(10.0, pressed - cfg.watch.feed_delay_s)
    start = max(0.0, end - float(request["seconds"]) - 5.0)
    held = None
    try:
        from .buffer import LiveBuffer

        if isinstance(getattr(w, "buffer", None), LiveBuffer):  # only this kind is held from the moment the watch began
            held = w.buffer.span()
    except Exception as e:  # asking must never lose the request itself
        log.debug("%s: the copy of the live feed could not be asked what it holds: %s", w.channel, e)
    if held and start < held[0] < end - 3:
        # An unrecorded stream is only held from the moment the watch began: the clip is what is held of the stretch
        # asked for, not a refusal that says it is "no longer" there.
        log.info("%s: the watch began after the start of the stretch you asked for; the clip starts where the copy of the live feed starts", w.channel)
        start = held[0] + 0.1
    return {"score": 0, "activity": 0, "messages": 0, "trigger": "manual", "signals": {}, "peak_s": round(start, 1),
            "start_s": round(start, 1), "end_s": round(end, 1), "asked": True}


def due_asked(w: Watched, cfg: Config) -> list[dict]:
    """Asked-for clips the recording has now caught up with."""
    live_now = time.time() - float(w.meta["started_at"])
    ready = [m for m in w.asked if m["end_s"] + cfg.fetch.pad_s + (0 if getattr(w, "buffer", None) else cfg.watch.vod_delay_s) <= live_now
             and time.time() >= m.get("not_before", 0)]
    w.asked = [m for m in w.asked if m not in ready]
    return ready


def make_draft(w: Watched, moment: dict, cfg: Config) -> Path:
    """Fetch, transcribe and render one moment from the live VOD."""
    from .render import render_clip

    if free_gb(cfg.data_dir) < 2.0:
        raise ToolError(f"less than 2 GB free on {cfg.data_dir.anchor}; delete old work folders")
    d = clip_dir(w.work, clip_id(moment["peak_s"]))
    earlier = read_json(d / "clip.json", {})
    attempts = earlier.get("attempts", 0) + 1
    state = {
        "attempts": attempts,  # a moment whose download keeps failing is given up after three tries
        "id": d.name, "rank": 0, "status": earlier.get("status", "candidate"), "live": True,
        "score": moment["score"], "activity": moment["activity"], "messages": moment["messages"],
        "trigger": moment.get("trigger", "chat"), "signals": moment.get("signals", {}),
        "peak_s": moment["peak_s"], "start_s": moment["start_s"], "end_s": moment["end_s"],
        "vod_url": w.source.url, "platform": w.source.platform, "channel": w.meta.get("channel", w.channel),
        "channel_handle": w.meta.get("channel_id", ""),
        "source_url": w.meta.get("channel_url") or f"https://www.twitch.tv/{w.channel}",
        "stream_title": w.meta.get("title", ""), "found_at": earlier.get("found_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
        "made_at": earlier.get("made_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    state["fetch_start_s"] = max(0.0, state["start_s"] - cfg.fetch.pad_s)
    if moment.get("asked"):
        # A clip asked for right after the watch began starts where the copy of the live feed starts: there is nothing
        # before it to take the usual few seconds of lead from.
        from .buffer import LiveBuffer

        first = getattr(w.buffer, "first_t", None) if isinstance(getattr(w, "buffer", None), LiveBuffer) else None
        if isinstance(getattr(w, "buffer", None), LiveBuffer) and first is None:
            raise ToolError("the watch has only just begun and nothing of the live feed is kept yet; this is tried again in a moment")
        if first is not None and state["fetch_start_s"] < first:
            # Asked for in the first moments of the watch: part (or all) of the stretch was before anything was kept.
            if first >= state["end_s"] - 3:
                raise ToolError("the watch began after the stretch you asked for, so nothing of it was kept and there is nothing to cut. "
                                "Clips can be asked for from a few seconds after the watch begins")
            state["fetch_start_s"] = round(first, 2)
            state["start_s"] = round(max(state["start_s"], first + 0.05), 2)
            state["peak_s"] = max(state["peak_s"], state["start_s"])
    state["fetch_end_s"] = state["end_s"] + cfg.fetch.pad_s
    if earlier.get("from_live_buffer") and done(d / "source.mp4"):
        # Cut from the live buffer on an earlier try: keep where that file really starts.
        state.update(fetch_start_s=earlier["fetch_start_s"], from_live_buffer=True)
    save_clip(d / "clip.json", state)
    t0 = time.monotonic()
    if getattr(w, "buffer", None):
        if not done(d / "source.mp4"):
            # Whole pieces are joined, so the file starts a second or so before the asked start.
            state["fetch_start_s"] = w.buffer.cut(state["fetch_start_s"], state["fetch_end_s"], d / "source.mp4")
            state["from_live_buffer"] = True
            save_clip(d / "clip.json", state)
    else:
        fetch_range(w.source, state["fetch_start_s"], state["fetch_end_s"], d / "source.mp4", cfg)
    prepare_clip(d, state, cfg)
    render_clip(d, cfg)
    state = read_json(d / "clip.json")
    state["ready_seconds_after_moment"] = round(time.time() - float(w.meta["started_at"]) - state["peak_s"])
    state["draft_done"] = True  # from here on the watcher leaves this clip alone
    state["draft_seconds"] = round(time.monotonic() - t0, 1)
    save_clip(d / "clip.json", state)
    return d


def draft_job(w: Watched, moment: dict, cfg: Config) -> None:
    """Make one draft and report it; every failure is logged and kept on the clip, never raised (it runs in a worker)."""
    try:
        try:
            d = make_draft(w, moment, cfg)
        except Exception as e:
            note_error(clip_dir(w.work, clip_id(moment["peak_s"])), f"draft failed: {e}")
            if moment.get("asked") and moment.get("tries", 0) < 2:
                # Asked for by a person: tried again in half a minute (the recording may not have reached it yet), three times in all.
                w.asked.append({**moment, "tries": moment.get("tries", 0) + 1, "not_before": time.time() + 30})
            raise
        note_error(d, None)
        s = read_json(d / "clip.json")
        print(f"DRAFT {w.channel} {d.name}  {s.get('trigger', 'chat')} {s['score']}  ready {s['ready_seconds_after_moment']}s after the moment  {d / 'final.mp4'}", flush=True)
    except Exception as e:
        # A ToolError's message is the whole story; anything else is a bug and gets its full traceback.
        log.error("%s: %s", w.channel, e, exc_info=not isinstance(e, ToolError))


def watch(channels: list[str], cfg: Config, max_minutes: float | None = None) -> None:
    from concurrent.futures import ThreadPoolExecutor

    names = [channel_name(c) for c in channels]
    watched: dict[str, Watched] = {}
    pool = ThreadPoolExecutor(max_workers=max(1, int(cfg.watch.draft_workers)), thread_name_prefix="draft")
    windows: dict[tuple[str, str], tuple[float, float]] = {}  # the stretch each draft in progress covers
    making: dict[tuple[str, str], object] = {}  # (channel, clip id) -> the draft being made, so a moment is not started twice
    deadline = time.time() + max_minutes * 60 if max_minutes else None
    print(f"Watching {', '.join(names)} using {cfg.watch.mode}. Drafts appear under {cfg.data_dir / 'work'}. Ctrl+C to stop.")
    try:
        while deadline is None or time.time() < deadline:
            asked = take_asked(cfg.data_dir)
            if asked and not watched:
                log.warning("a clip of the last seconds was asked for, but no stream is being watched right now")
            for name in names:
                try:
                    w = watched.get(name)
                    if w is not None and getattr(w, "ending", 0):
                        # Its broadcast is over; only its drafts that are being made still need the copy of the live feed.
                        if time.time() > w.ending or not any(k[0] == name and not f.done() for k, f in making.items()):
                            log.info("%s: stream ended", name)
                            w.stop()
                            watched.pop(name, None)
                        continue
                    if w is None:
                        w = start_channel(name, cfg)
                        if w is None:
                            if name.startswith("kick.com/"):
                                pass  # start_kick has already said why (not live, no such channel, nothing to cut from)
                            elif not name.startswith(("@", "http")) and channel_is_live(name, cfg):
                                log.warning(NO_RECORDING, name)
                            else:
                                log.info("%s: not live; will check again", name)
                            continue
                        watched[name] = w
                    for key in [k for k, f in making.items() if f.done()]:
                        del making[key]
                    for request in asked:
                        w.asked.append(asked_moment(w, request, cfg))
                        log.info("%s: you asked for the last %.0f seconds; the clip is made as soon as the recording reaches it", name, request["seconds"])
                    fresh = new_moments(w, cfg)  # first: if this fails, the asked-for clips are still waiting, not lost
                    for moment in due_asked(w, cfg) + fresh:
                        key = (name, clip_id(moment["peak_s"]))
                        if key in making:
                            continue  # already being made
                        if not moment.get("asked") and any(k[0] == name and min(moment["end_s"], b) - max(moment["start_s"], a) > 0.5 * (moment["end_s"] - moment["start_s"])
                                                           for k, (a, b) in windows.items() if k in making):
                            continue  # the same moment under a shifted name is still being made
                        log.info("%s: moment at %s (%s, %.1f), making a draft%s", name, format_time(moment["peak_s"]), moment.get("trigger", "chat"),
                                 moment["score"], f" ({len(making)} other draft(s) in progress)" if making else "")
                        windows[key] = (moment["start_s"], moment["end_s"])
                        making[key] = pool.submit(draft_job, w, moment, cfg)
                    if time.time() - w.last_live_check > cfg.watch.live_check_s:
                        w.last_live_check = time.time()
                        if still_live(w, cfg):
                            w.not_live = 0
                        elif w.not_live < 1:
                            # One "no" is not proof: the question itself can fail (a timeout, the site refusing for a
                            # moment), and ending the watch throws away the live copy and the clips waiting for it.
                            w.not_live += 1
                            w.last_live_check = time.time() - cfg.watch.live_check_s + 60
                            log.info("%s: the site says this broadcast is not live; asking once more in a minute before stopping", name)
                        else:
                            mine = [f for k, f in making.items() if k[0] == name and not f.done()]
                            if w.asked:
                                log.warning("%s: the stream ended before %d clip(s) you asked for could be cut; they are not made", name, len(w.asked))
                                w.asked.clear()
                            if mine and w.buffer:
                                # Their video comes out of the live copy, which is removed when the watch stops: the watch
                                # is kept for up to five minutes, without holding up the other channels meanwhile.
                                log.info("%s: stream ended; finishing %d draft(s) that are being made first", name, len(mine))
                                w.ending = time.time() + 300
                            else:
                                log.info("%s: stream ended", name)
                                w.stop()
                                watched.pop(name, None)
                except Exception as e:  # one channel's failure must not stop the others
                    # A ToolError's message is the whole story; anything else is a bug and gets its full traceback.
                    log.error("%s: %s", name, e, exc_info=not isinstance(e, ToolError))
            # Wait for the next look, but answer a "clip now" press within a couple of seconds.
            wake = time.time() + cfg.watch.poll_s
            while time.time() < wake:
                if int(time.time()) % 6 < 2:  # every six seconds or so
                    write_live(cfg.data_dir, names, watched)
                waiting = any(w.asked for w in watched.values())
                if (cfg.data_dir / ASKED_FILE).exists() or (waiting and time.time() > wake - cfg.watch.poll_s + 5):
                    break
                time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        (cfg.data_dir / LIVE_FILE).unlink(missing_ok=True)
        pool.shutdown(wait=False, cancel_futures=True)
        for w in watched.values():
            w.stop()
        print("Stopped. Review drafts with: clipper review <vod url>")
        for w in watched.values():
            print(f"  {w.channel}: {w.source.url}  ({w.reader.messages} chat messages read)")
