"""Watch live Twitch streams and make draft clips while they are still running.

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
    """
    from .youtube import channel_spec, is_youtube

    if is_youtube(value):
        name, url = channel_spec(value)
        return name if name.startswith("@") else url
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", value)
    name = (m.group(1) if m else value).strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{2,25}", name) or name == "videos":
        raise ValueError(f"{value!r} is not a channel. Type a Twitch name (like lacy), or a YouTube handle (like @channelname).")
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

    def run(self) -> None:
        from websockets.sync.client import connect

        wait = 2.0
        while not self.stop_event.is_set():
            try:
                with connect(IRC_URL, open_timeout=20, close_timeout=5) as ws:
                    ws.send("NICK justinfan73512")  # Twitch's anonymous read-only login
                    ws.send(f"JOIN #{self.channel}")
                    self.connected_at = time.time()
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
        self.asked: list[dict] = []  # "clip the last N seconds" requests, waiting for the recording to reach them

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


def still_live(w: Watched, cfg: Config) -> bool:
    """Is the broadcast this channel is being watched on still running?"""
    if w.source.platform == "youtube":
        from .youtube import find_live

        found = find_live(w.channel, cfg)
        return bool(found) and found[0].video_id == w.source.video_id
    return bool(channel_is_live(w.channel, cfg) if w.buffer else find_live_vod(w.channel, cfg))


def start_channel(channel: str, cfg: Config) -> Watched | None:
    from .youtube import is_youtube

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
    scores = score_buckets(buckets, s)
    live_now = time.time() - float(w.meta["started_at"])
    found = [m.to_dict() for m in pick_moments(buckets, scores, s)] if messages else []
    if getattr(w, "feed", None):
        found = merge_moments(found, signal_peaks(w.work / "signals.jsonl", cfg, since_s=w.watch_from))
    out = []
    for m in found:
        d = w.work / "clips" / clip_id(m["peak_s"])
        state = read_json(d / "clip.json", {})
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
    path = d / "clip.json"
    state = read_json(path, {})
    if not state or (message is None and "error" not in state):
        return
    if message is None:
        state.pop("error", None)
    else:
        state["error"] = f"{time.strftime('%H:%M:%S')} {message}"
    write_json(path, state)


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
    return {"score": 0, "activity": 0, "messages": 0, "trigger": "manual", "signals": {}, "peak_s": round(start, 1),
            "start_s": round(start, 1), "end_s": round(end, 1), "asked": True}


def due_asked(w: Watched, cfg: Config) -> list[dict]:
    """Asked-for clips the recording has now caught up with."""
    live_now = time.time() - float(w.meta["started_at"])
    ready = [m for m in w.asked if m["end_s"] + cfg.fetch.pad_s + (0 if getattr(w, "buffer", None) else cfg.watch.vod_delay_s) <= live_now]
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
        "stream_title": w.meta.get("title", ""), "found_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    state["fetch_start_s"] = max(0.0, state["start_s"] - cfg.fetch.pad_s)
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
                    if w is None:
                        w = start_channel(name, cfg)
                        if w is None:
                            if not name.startswith(("@", "http")) and channel_is_live(name, cfg):
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
                    for moment in due_asked(w, cfg) + new_moments(w, cfg):
                        key = (name, clip_id(moment["peak_s"]))
                        if key in making:
                            continue  # already being made
                        log.info("%s: moment at %s (%s, %.1f), making a draft%s", name, format_time(moment["peak_s"]), moment.get("trigger", "chat"),
                                 moment["score"], f" ({len(making)} other draft(s) in progress)" if making else "")
                        making[key] = pool.submit(draft_job, w, moment, cfg)
                    if time.time() - w.last_live_check > cfg.watch.live_check_s:
                        w.last_live_check = time.time()
                        if not still_live(w, cfg):
                            log.info("%s: stream ended", name)
                            w.stop()
                            watched.pop(name, None)
                except Exception as e:  # one channel's failure must not stop the others
                    # A ToolError's message is the whole story; anything else is a bug and gets its full traceback.
                    log.error("%s: %s", name, e, exc_info=not isinstance(e, ToolError))
            # Wait for the next look, but answer a "clip now" press within a couple of seconds.
            wake = time.time() + cfg.watch.poll_s
            while time.time() < wake:
                waiting = any(w.asked for w in watched.values())
                if (cfg.data_dir / ASKED_FILE).exists() or (waiting and time.time() > wake - cfg.watch.poll_s + 5):
                    break
                time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        for w in watched.values():
            w.stop()
        print("Stopped. Review drafts with: clipper review <vod url>")
        for w in watched.values():
            print(f"  {w.channel}: {w.source.url}  ({w.reader.messages} chat messages read)")
