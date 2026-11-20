"""Listen to and look at a live stream's lowest-quality feed, to find moments chat alone misses.

This is the optional second and third signal beside chat (``watch.mode``):

  audio   how loud each second is. A shout, a laugh or a sudden burst stands out
          against the last few minutes.
  video   how much the picture changes twice a second. Fast action and hard cuts
          stand out the same way.

The feed is Twitch's ``audio_only`` (about 70 MB an hour) or ``160p`` (about
100 MB an hour) rendition. It is piped through FFmpeg, measured and thrown
away: nothing of it is saved. Clips are still fetched as short ranges, at full
quality, exactly as for chat moments.

These are measurements, not a neural model: they say "something loud or fast
happened here", not what happened. Whether the moment is any good is still
judged afterwards from its transcript, and by the person reviewing.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from statistics import median
from typing import Sequence

from .config import Config
from .tools import ToolError, find_tool, run

log = logging.getLogger(__name__)

MODES = {
    "chat": (False, False),
    "audio": (True, False),
    "video": (False, True),
    "all": (True, True),
}
MODE_LETTERS = {"a": "chat", "b": "audio", "c": "video", "d": "all"}
MODE_TEXT = {
    "chat": "chat only (uses almost no internet)",
    "audio": "chat + audio (about 70 MB an hour for each channel)",
    "video": "chat + video (about 100 MB an hour for each channel)",
    "all": "chat + audio + video (about 100 MB an hour for each channel)",
}
SIGNAL_BEFORE_S = 15.0  # a clip made from an audio or video moment starts this long before the peak
SIGNAL_AFTER_S = 15.0
SILENCE_DB = -60.0  # quieter than this is a muted stream, not a baseline to compare with
AUDIO_OVER_USUAL_DB = 6.0  # a loud moment is at least this much louder than the stream's usual level
CLOCK_JUMP_S = 30.0  # a change this big in a sample's lag means the feed's clock jumped (ad break)
MIN_HISTORY = 12  # buckets of history needed before anything can count as unusual (two minutes)


def parse_mode(text: str, default: str = "chat") -> str:
    """'B', 'b', 'audio' -> 'audio'. Empty text gives the default."""
    t = text.strip().lower()
    if not t:
        return default
    mode = MODE_LETTERS.get(t, t)
    if mode not in MODES:
        raise ValueError(f"{text!r} is not one of A, B, C, D (or chat, audio, video, all)")
    return mode


def ask_mode(default: str = "chat") -> str:
    """Ask in the terminal what to watch. Enter keeps the default."""
    letter = {v: k.upper() for k, v in MODE_LETTERS.items()}
    print("What should the watcher use to find moments?")
    for key, mode in MODE_LETTERS.items():
        print(f"  {key.upper()}  {MODE_TEXT[mode]}")
    while True:
        try:
            return parse_mode(input(f"Choice [{letter[default]}]: "), default)
        except ValueError as e:
            print(e)
        except EOFError:
            return default


# ---- reading the feed ----


def live_url(channel: str, cfg: Config, video: bool) -> str:
    """Address of the channel's lowest-quality live feed (with or without picture)."""
    from .fetch import _ytdlp

    proc = run(
        _ytdlp(cfg) + ["--no-warnings", "-g", "-f", "160p/worst" if video else "audio_only/worst", f"https://www.twitch.tv/{channel}"],
        timeout_s=90, retries=1, retry_wait_s=5, what=f"{channel} live feed address",
    )
    url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    if not url.startswith("http"):
        raise ToolError(f"{channel}: no live feed address found")
    return url


def feed_command(ffmpeg: str, url: str, audio: bool, video: bool) -> list[str]:
    """FFmpeg command that prints one loudness value a second on stdout and two motion values a second on stderr."""
    parts, maps = [], []
    if audio:
        parts.append(
            # 300 to 3400 Hz is where voices are. Measuring loudness there follows shouts and laughter,
            # and is far less moved by game explosions, engines and bass-heavy music.
            "[0:a]aresample=16000,highpass=f=300,lowpass=f=3400,asetnsamples=n=16000,astats=metadata=1:reset=1,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-:direct=1[a]"
        )
        maps += ["-map", "[a]", "-f", "null", "-"]
    if video:
        # Two frames a second, picked with select: the fps filter would invent thousands of frames
        # to fill the jump in the feed's clock when an ad break ends.
        parts.append(
            "[0:v]select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,0.5)+lt(t\\,prev_selected_t)',scale=160:90,"
            "select='gte(scene\\,0)',metadata=print:key=lavfi.scene_score:file='pipe\\:2':direct=1[v]"
        )
        maps += ["-map", "[v]", "-f", "null", "-"]
    # -copyts keeps the feed's own clock, which counts seconds of the broadcast.
    return [ffmpeg, "-hide_banner", "-nostats", "-v", "quiet", "-copyts", "-i", url, "-filter_complex", ";".join(parts)] + maps


def parse_feed_lines(lines: Sequence[str]) -> list[tuple[float, float]]:
    """(pts seconds, value) pairs from FFmpeg's metadata printout ("frame:.. pts_time:X" then "key=value")."""
    out, pts = [], None
    for line in lines:
        line = line.strip()
        if line.startswith("frame:") and "pts_time:" in line:
            try:
                pts = float(line.rsplit("pts_time:", 1)[1])
            except ValueError:
                pts = None
        elif pts is not None and "=" in line:
            raw = line.rsplit("=", 1)[1]
            try:
                value = float(raw)
            except ValueError:
                value = float("nan")
            if value != value or value in (float("inf"), float("-inf")):
                value = -90.0  # digital silence prints as -inf
            out.append((pts, value))
            pts = None
    return out


class SignalReader(threading.Thread):
    """Measures one channel's live feed and appends the values to <work>/signals.jsonl."""

    def __init__(self, channel: str, out: Path, stream_start: float, cfg: Config, audio: bool, video: bool) -> None:
        super().__init__(daemon=True, name=f"feed-{channel}")
        self.channel, self.out, self.stream_start, self.cfg = channel, out, stream_start, cfg
        self.audio, self.video = audio, video
        self.stop_event = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.samples = 0
        self._lock = threading.Lock()

    def _pump(self, stream, kind: str) -> None:
        pending: list[str] = []
        for line in stream:
            pending.append(line)
            if len(pending) < 2:
                continue
            pairs = parse_feed_lines(pending)
            if not pairs and not pending[-1].startswith("frame:"):
                pending.clear()
                continue
            if pairs:
                pending.clear()
                age = time.time() - self.stream_start
                with self._lock, self.out.open("a", encoding="utf-8") as fh:
                    for pts, value in pairs:
                        # "lag" is how far the wall clock is ahead of the feed's clock on arrival.
                        fh.write(json.dumps({"k": kind, "pts": round(pts, 2), "lag": round(age - pts, 2), "v": round(value, 4)}) + "\n")
                        self.samples += 1

    def run(self) -> None:
        wait = 5.0
        ffmpeg = find_tool("ffmpeg", self.cfg.data_dir)
        while not self.stop_event.is_set():
            try:
                url = live_url(self.channel, self.cfg, self.video)
                self.proc = subprocess.Popen(
                    feed_command(ffmpeg, url, self.audio, self.video),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True, errors="replace",
                )
                pumps = []
                if self.audio:
                    pumps.append(threading.Thread(target=self._pump, args=(self.proc.stdout, "a"), daemon=True))
                if self.video:
                    pumps.append(threading.Thread(target=self._pump, args=(self.proc.stderr, "v"), daemon=True))
                for p in pumps:
                    p.start()
                started = time.time()
                self.proc.wait()
                if time.time() - started > 120:
                    wait = 5.0  # it ran fine for a while; reconnect quickly
            except Exception as e:  # the feed dropping is normal (ads, hiccups, stream end)
                log.warning("%s: live feed problem (%s)", self.channel, e)
            if self.stop_event.is_set():
                break
            self.stop_event.wait(wait)
            wait = min(wait * 2, 120.0)

    def stop(self) -> None:
        self.stop_event.set()
        if self.proc and self.proc.poll() is None:
            self.proc.kill()


# ---- turning the measurements into moments (pure logic) ----


def vod_feed_url(url: str, cfg: Config, video: bool) -> str:
    """Address of a finished stream's lowest-quality version (sound only, or the smallest picture)."""
    from .fetch import _ytdlp

    if Path(url).is_file():
        return url  # a video file on this computer is read directly

    proc = run(
        _ytdlp(cfg) + ["-g", "-f", "160p/worst[vcodec!=none]/worst" if video else "Audio_Only/audio_only/worstaudio/worst", url],
        timeout_s=120, retries=2, retry_wait_s=5, what="finished stream's low-quality address",
    )
    found = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    if not found.startswith("http"):
        raise ToolError("no low-quality version of this finished stream was found to measure")
    return found


def measure_vod(url: str, work: Path, cfg: Config, audio: bool, video: bool) -> Path:
    """Measure loudness and motion over a whole finished stream and write them to <work>/signals_vod.jsonl.

    The lowest-quality version is read once, measured and thrown away; nothing of it is saved.
    Rows carry the stream second directly ({"k", "t", "v"}), since a finished stream's clock needs no aligning.
    """
    out = work / "signals_vod.jsonl"
    want = "".join(["a"] * audio + ["v"] * video)
    mark = work / "signals_vod.done"
    if out.exists() and mark.exists() and set(want) <= set(mark.read_text(encoding="utf-8").strip()):
        return out
    ffmpeg = find_tool("ffmpeg", cfg.data_dir)
    feed = vod_feed_url(url, cfg, video)
    log.info("measuring the finished stream's %s (reads its lowest-quality version once; about %d MB per stream hour)",
             " and ".join(["sound"] * audio + ["picture"] * video), 130 if video else 100)
    proc = subprocess.Popen(feed_command(ffmpeg, feed, audio, video), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, text=True, errors="replace")
    lines: dict[str, list[str]] = {"a": [], "v": []}

    def pump(stream, kind: str) -> None:
        for line in stream:
            lines[kind].append(line)

    pumps = [threading.Thread(target=pump, args=(proc.stdout, "a" if audio else "v"), daemon=True),
             threading.Thread(target=pump, args=(proc.stderr, "v"), daemon=True)]
    for p in pumps:
        p.start()
    from . import progress
    from .work import read_json as _read

    length = float((_read(work / "meta.json", {}) or {}).get("duration_s") or 0)
    t0 = last_note = time.time()
    while proc.poll() is None:
        time.sleep(2)
        if length:
            at = max((parse_feed_lines(v[-6:]) or [(0.0, 0.0)])[-1][0] for v in lines.values())
            progress.sub(at / length, f"up to {int(at) // 3600}:{int(at) % 3600 // 60:02d}:{int(at) % 60:02d} of the stream")
        if time.time() - last_note > 60:
            last_note = time.time()
            reached = max((parse_feed_lines(v[-6:]) or [(0.0, 0.0)])[-1][0] for v in lines.values())
            log.info("  measured up to %d:%02d:%02d of the stream", reached // 3600, reached % 3600 // 60, reached % 60)
    for p in pumps:
        p.join(timeout=30)
    rows = []
    for kind, wanted in (("a", audio), ("v", video)):
        if wanted:
            rows += [{"k": kind, "t": round(t, 2), "v": round(v, 4)} for t, v in parse_feed_lines(lines[kind])]
    if not rows:
        raise ToolError(f"the finished stream could not be measured (FFmpeg ended with code {proc.returncode} and no values). "
                        f"Its last words: {' '.join(x.strip() for x in lines['v'][-3:])[:300]}")
    part = out.with_suffix(".part")
    with part.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    part.replace(out)
    mark.write_text(want, encoding="utf-8")
    log.info("measured %d values in %.0fs", len(rows), time.time() - t0)
    return out


def load_signals(path: Path, feed_delay_s: float) -> dict[str, list[tuple[float, float]]]:
    """{"a": [(stream seconds, dB)], "v": [(stream seconds, motion)]} from a signals.jsonl file.

    The feed's clock runs at the right speed but from its own zero. The sample that
    arrived with the smallest lag was delivered fastest; the feed is taken to be
    ``feed_delay_s`` behind the broadcast at that point, which fixes the offset.
    The clock jumps when an ad break starts or ends, so each stretch between jumps
    gets its own offset.
    """
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a half-written last line
    if not rows:
        return {"a": [], "v": []}
    out: dict[str, list[tuple[float, float]]] = {"a": [], "v": []}
    for r in rows:  # a finished stream's rows already carry the stream second
        if r.get("k") in out and "t" in r:
            out[r["k"]].append((float(r["t"]), float(r["v"])))
    rows = [r for r in rows if "t" not in r]
    stretch: list[dict] = []
    for r in [r for r in rows if r.get("k") in out] + [None]:
        if r is None or (stretch and abs(r["lag"] - stretch[-1]["lag"]) > CLOCK_JUMP_S):
            if stretch:
                offset = min(x["lag"] for x in stretch) - feed_delay_s
                for x in stretch:
                    out[x["k"]].append((x["pts"] + offset, float(x["v"])))
            stretch = []
        if r is not None:
            stretch.append(r)
    for values in out.values():
        values.sort()
    return out


def bucket_values(samples: Sequence[tuple[float, float]], bucket_s: float, top: int = 0) -> dict[int, float]:
    """Average per time bucket. With ``top`` > 0, the average of each bucket's ``top`` highest values (bursts count)."""
    groups: dict[int, list[float]] = {}
    for t, v in samples:
        if t >= 0:
            groups.setdefault(int(t // bucket_s), []).append(v)
    out = {}
    for i, vals in groups.items():
        if top:
            vals = sorted(vals, reverse=True)[:top]
        out[i] = sum(vals) / len(vals)
    return out


def unusual(values: dict[int, float], window: int, floor: float) -> dict[int, float]:
    """How far each bucket stands above the buckets before it, in robust standard deviations.

    Uses only the past (``window`` buckets back), so it can run live. ``floor`` is the
    smallest spread assumed, so a dead-quiet stretch does not make a whisper look huge.
    """
    out: dict[int, float] = {}
    for i in sorted(values):
        past = [values[j] for j in range(i - window, i) if j in values]
        if len(past) < MIN_HISTORY:
            continue
        mid = median(past)
        spread = max(1.4826 * median(abs(p - mid) for p in past), floor)
        out[i] = (values[i] - mid) / spread
    return out


def pick_peaks(scores: dict[int, float], threshold: float, gap_buckets: int) -> list[tuple[int, float]]:
    """Buckets over the threshold, strongest first, none closer than ``gap_buckets`` to a stronger one."""
    chosen: list[tuple[int, float]] = []
    for i, z in sorted(scores.items(), key=lambda x: -x[1]):
        if z < threshold:
            break
        if all(abs(i - j) >= gap_buckets for j, _ in chosen):
            chosen.append((i, z))
    return sorted(chosen)


def merge_moments(chat: list[dict], peaks: dict[str, list[tuple[float, float]]], near_s: float = 45.0) -> list[dict]:
    """Chat moments, each marked with the signals that agree with it, plus moments only a signal found.

    ``peaks``: {"audio": [(peak second, strength)], "video": [...]}.
    """
    out = [dict(m, trigger="chat") for m in chat]
    for kind in ("audio", "video", "said"):
        for peak_s, z in peaks.get(kind, []):
            # Chat reacts a few seconds after the moment, so its peak sits later than the sound's.
            match = next((m for m in out if -near_s <= m["peak_s"] - peak_s <= near_s), None)
            if match:
                if kind not in match["trigger"]:
                    match["trigger"] += "+" + kind
                match.setdefault("signals", {})[kind] = round(z, 1)
            else:
                out.append({
                    "score": round(z, 1), "activity": 0, "messages": 0, "hype": 0, "trigger": kind, "signals": {kind: round(z, 1)},
                    "peak_s": float(peak_s), "start_s": max(0.0, peak_s - SIGNAL_BEFORE_S), "end_s": peak_s + SIGNAL_AFTER_S,
                })
    return sorted(out, key=lambda m: m["peak_s"])


def add_signal_moments(chat: list[dict], peaks: dict[str, list[tuple[float, float]]], extra: int) -> list[dict]:
    """For a finished stream: the chat moments, marked where sound or picture agree, plus the ``extra`` strongest
    moments that only sound or picture found. Ranks follow on from the chat moments'."""
    merged = merge_moments(chat, peaks)
    kept = [m for m in merged if m["trigger"].startswith("chat")]
    only = sorted((m for m in merged if not m["trigger"].startswith("chat")), key=lambda m: -m["score"])[:max(0, extra)]
    rank = max((m.get("rank", 0) for m in kept), default=0)
    for m in only:
        rank += 1
        m["rank"] = rank
    return kept + only


def signal_peaks(path: Path, cfg: Config, since_s: float) -> dict[str, list[tuple[float, float]]]:
    """Unusual audio and video moments in a signals file, as {"audio": [(second, strength)], "video": [...]}."""
    w = cfg.watch
    bucket = cfg.moments.bucket_s
    data = load_signals(path, w.feed_delay_s)
    window = int(300 // bucket)
    gap = max(int(w.signal_gap_s // bucket), 1)
    out: dict[str, list[tuple[float, float]]] = {}
    for kind, key, top, floor, threshold in (("audio", "a", 3, 1.5, w.audio_level), ("video", "v", 0, 0.004, w.video_level)):
        values = {i: v for i, v in bucket_values(data[key], bucket, top=top).items() if i * bucket >= since_s}
        scores = unusual(values, window, floor)
        if kind == "audio" and values:
            # Unusual is not enough: sound coming back after a muted intro or a quiet spell is unusual too.
            # A loud moment must also be clearly louder than the stream usually is.
            usual = median(values.values())
            scores = {
                i: z for i, z in scores.items()
                if values[i] >= usual + AUDIO_OVER_USUAL_DB
                and median([values[j] for j in range(i - window, i) if j in values] or [SILENCE_DB]) > SILENCE_DB
            }
        out[kind] = [(i * bucket + bucket / 2, z) for i, z in pick_peaks(scores, threshold, gap)]
    return out
