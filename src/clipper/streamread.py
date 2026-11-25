"""Let AI read a whole finished stream: transcribe all of its speech, then ask Claude which moments are worth clipping.

Chat only shows when viewers reacted, and loudness only shows when it got loud. Neither knows what was
said. This does: the stream's sound-only version is streamed through FFmpeg straight into the speech
model, ten minutes at a time, and only the text is kept (<work>/stream_text.jsonl). Nothing of the
stream itself is saved. Claude then reads the timed text and names the best moments.

It is slow on a processor (a smaller speech model is used there) and costs a little per stream, so it is
off unless asked for. Progress is saved after every ten minutes of stream, so a stopped run carries on.
"""

from __future__ import annotations

import json
import re
import logging
import subprocess
import time
from pathlib import Path

from .config import Config
from .moments import format_time
from .tools import ToolError, find_tool

log = logging.getLogger(__name__)

CHUNK_S = 600  # seconds of stream handed to the speech model at a time
RATE = 16000
MIN_CLIP_S, MAX_CLIP_S = 15.0, 60.0

SYSTEM = """You choose the moments worth clipping from a gaming livestream, for short vertical videos \n(YouTube Shorts, TikTok, Reels) of 20 to 60 seconds.

You are given the stream's speech as text with a time in seconds at the start of each line. It comes from \nspeech-to-text, so it has mistakes, little punctuation, and it does not show who is speaking or what is on screen.

A good moment works for a viewer who has not seen the stream: a funny line or exchange, a strong reaction \n(shock, rage, panic, celebration), a fail or a clutch the streamer talks through, a surprising event, a story \nwith a punchline, or a bold claim. It has a setup and a payoff inside the clip.

Not good: greetings, reading chat or donations aloud, thanking subscribers, giveaways, technical problems, \nmenus, waiting, music, and ordinary play-by-play with no payoff. Most of a stream is not worth clipping; \nreturn fewer moments rather than weak ones.

For each moment give:
- start_s and end_s: whole seconds from the times shown. Start a few seconds before the setup and end just \nafter the payoff. Between 20 and 60 seconds long. Moments must not overlap.
- strength: 1 to 10, how confident you are that it works as a clip.
- reason: one plain sentence saying what happens and why it works. Only what the text of that moment itself 
supports: quote or paraphrase its actual words. You cannot see the chat or the screen, so do not say how chat 
reacted or what was shown. Call the streamer "the streamer" or by name; do not guess he or she.

The speech may be in Hindi or Urdu, often mixed with English. Judge it as a viewer who speaks that language would, \nand write every reason in English.

Order them best first."""

SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_s": {"type": "integer"},
                    "end_s": {"type": "integer"},
                    "strength": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["start_s", "end_s", "strength", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["moments"],
    "additionalProperties": False,
}


def stream_model_name(cfg: Config) -> str:
    """Speech model for the whole-stream pass: the usual one on a graphics card, a smaller one on a processor."""
    from . import hw

    if cfg.ai.speech_model != "auto":
        return cfg.ai.speech_model
    return cfg.transcribe.model if hw.whisper_device(cfg)[0] == "cuda" else "small"


def _speech_language(cfg: Config) -> str | None:
    from .transcribe import speech_language

    return speech_language(cfg)


def read_rows(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a line cut off when the program was stopped; that chunk is done again
    return rows


def transcribe_stream(url: str, work: Path, cfg: Config, duration_s: float | None = None) -> Path:
    """All of a finished stream's speech as timed text in <work>/stream_text.jsonl ({"t", "e", "text"} per line)."""
    import numpy as np

    from .signals import vod_feed_url
    from .transcribe import load_model

    out, mark = work / "stream_text.jsonl", work / "stream_text.done"
    limit = cfg.ai.max_hours * 3600 if cfg.ai.max_hours > 0 else None
    target = min(x for x in (duration_s, limit) if x) if (duration_s or limit) else None
    done_s = float(mark.read_text(encoding="utf-8").strip() or 0) if mark.exists() else 0.0
    if not out.exists():
        done_s = 0.0
    if target and done_s >= target - 1:
        return out
    if re.match(r"https?://", url) and "youtu" in url.split("/")[2]:
        # YouTube has usually written the whole stream down already; reading that takes seconds, listening takes up to an hour.
        from . import progress, ytcaptions

        words = ytcaptions.video_words(url, work, cfg)
        if words:
            rows = [r for r in ytcaptions.as_lines(words) if not target or r["t"] < target]
            out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
            mark.write_text(f"{target or (rows[-1]['e'] if rows else 0):.1f}", encoding="utf-8")
            log.info("the stream's speech was taken from YouTube's own captions: %d lines, no listening needed", len(rows))
            progress.sub(0.95, "speech taken from YouTube's captions")
            return out
    name = stream_model_name(cfg)
    model = load_model(cfg, name)
    feed = vod_feed_url(url, cfg, video=False)
    ffmpeg = find_tool("ffmpeg", cfg.data_dir)
    cmd = [ffmpeg, "-hide_banner", "-v", "error", "-ss", f"{done_s:.0f}", "-i", feed]
    if target:
        cmd += ["-t", f"{target - done_s:.0f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    log.info("reading the stream's speech with the %s model from %s%s (only the text is kept)", name, format_time(done_s),
             f" to {format_time(target)}" if target else "")
    import tempfile

    complaints = tempfile.TemporaryFile()  # not a pipe: one nobody reads fills up and FFmpeg then stands still
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=complaints, stdin=subprocess.DEVNULL)
    t0, at, want = time.time(), done_s, CHUNK_S * RATE * 2
    try:
        while True:
            raw = proc.stdout.read(want)
            if not raw:
                break
            audio = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
            segments, _info = model.transcribe(audio, language=_speech_language(cfg), beam_size=1, vad_filter=False,
                                               condition_on_previous_text=False, word_timestamps=False)
            with out.open("a", encoding="utf-8") as fh:
                for seg in segments:
                    text = seg.text.strip()
                    if text and seg.no_speech_prob < 0.8:
                        fh.write(json.dumps({"t": round(at + seg.start, 1), "e": round(at + seg.end, 1), "text": text}, ensure_ascii=False) + "\n")
            at += len(audio) / RATE
            mark.write_text(f"{at:.1f}", encoding="utf-8")
            speed = (at - done_s) / max(time.time() - t0, 1)
            left = f", about {max(0, (target - at) / speed / 60):.0f} min left" if target else ""
            log.info("  speech read up to %s (%.1f times faster than the stream%s)", format_time(at), speed, left)
            if target:
                from . import progress

                progress.sub(0.95 * at / target, f"speech read up to {format_time(at)}")
            if len(raw) < want:
                break
    finally:
        if proc.poll() is None:
            proc.kill()
    complaints.seek(0)
    err = complaints.read().decode(errors="replace").strip()
    complaints.close()
    if at <= done_s and not (target and done_s >= target - 1):
        raise ToolError(f"none of the stream's sound could be read: {err[-300:] or 'FFmpeg gave no sound'}")
    # (FFmpeg ending cleanly with nothing to say means the video simply ended there: its listed length was too long,
    # or only a limit of hours was known. That is not a fault.)
    if target and at < target - max(120.0, 0.02 * target) and (err or proc.returncode not in (0, None)):
        # The reading broke off (the connection dropped). Picking moments from the part that was read, without a word,
        # would look like a finished job that simply found little.
        raise ToolError(f"the stream's speech could only be read up to {format_time(at)} of {format_time(target)}: "
                        f"{err[-300:] or 'the connection to the video ended early'}. Start it again: it carries on from where it stopped")
    return out


def transcript_lines(rows: list[dict], line_s: float = 30.0) -> list[str]:
    """Timed text for the prompt: speech grouped into lines of about ``line_s`` seconds, "[seconds] words"."""
    lines, start, words = [], None, []
    for r in sorted(rows, key=lambda r: r["t"]):
        if start is not None and r["t"] - start >= line_s:
            lines.append(f"[{int(start)}] {' '.join(words)}")
            start, words = None, []
        if start is None:
            start = r["t"]
        words.append(r["text"])
    if words:
        lines.append(f"[{int(start)}] {' '.join(words)}")
    return lines


def clean_picks(raw: list[dict], top: int, duration_s: float | None = None) -> list[dict]:
    """Model output made safe: sane lengths, inside the stream, no overlaps, best first, at most ``top``."""
    out: list[dict] = []
    for m in sorted(raw, key=lambda m: -int(m.get("strength", 0))):
        try:
            start, end = float(m["start_s"]), float(m["end_s"])
        except (KeyError, TypeError, ValueError):
            continue
        if end - start < MIN_CLIP_S:
            end = start + 30.0
        end = min(end, start + MAX_CLIP_S)
        if start < 0 or (duration_s and end > duration_s):
            continue
        if any(start < o["end_s"] and end > o["start_s"] for o in out):
            continue
        out.append({"start_s": start, "end_s": end, "strength": max(1, min(10, int(m.get("strength", 5)))), "reason": str(m.get("reason", "")).strip()})
        if len(out) >= top:
            break
    return out


MIX_NOTES = {
    "action": "The person wants ACTION this time: prefer chases, fights, crashes, wins, fails and loud reactions over talk.\n",
    "dialogue": "The person wants DIALOGUE this time: prefer stories, jokes, arguments, opinions and surprising statements over action. "
                "A quiet funny exchange beats a loud nothing.\n",
}


def pick_moments(work: Path, meta: dict, cfg: Config, top: int) -> list[dict]:
    """Ask Claude for the best moments in the stream's text. Raises LlmUnavailable with the reason if it cannot."""
    from . import llm

    lines = transcript_lines(read_rows(work / "stream_text.jsonl"))
    if not lines:
        raise ToolError("no speech was found in this stream, so there is nothing for AI to read")
    user = (
        f"Streamer: {meta.get('channel', '')}\nStream title: {meta.get('title', '')}\n"
        f"Return at most {top} moments.\n" + MIX_NOTES.get(cfg.moments.mix, "") +
        "\nSpeech, with the time in seconds at the start of each line:\n" + "\n".join(lines)
    )
    log.info("asking Claude (%s) to pick moments from %d lines of speech", cfg.ai.model, len(lines))
    data = llm.ask(cfg, cfg.ai.model, SYSTEM, user, SCHEMA, f"moments from the speech of {work.name}")
    return clean_picks(data.get("moments") or [], top, meta.get("duration_s") if not cfg.ai.max_hours else None)


def add_ai_moments(found: list[dict], picks: list[dict]) -> list[dict]:
    """Mark the moments AI agrees with, and add the ones only AI found (ranked after the rest)."""
    out = [dict(m) for m in found]
    rank = max((m.get("rank", 0) for m in out), default=0)
    for p in picks:
        match = next((m for m in out if m["start_s"] < p["end_s"] and m["end_s"] > p["start_s"]), None)
        if match:
            if "ai" not in match["trigger"].split("+"):
                match["trigger"] += "+ai"
            match["ai_pick"] = p["reason"]
            match["ai_strength"] = p["strength"]
            continue
        rank += 1
        out.append({
            "rank": rank, "score": float(p["strength"]), "activity": 0, "messages": 0, "hype": 0, "trigger": "ai", "ai_pick": p["reason"], "ai_strength": p["strength"],
            "peak_s": round(p["start_s"] + (p["end_s"] - p["start_s"]) * 0.6, 1), "start_s": p["start_s"], "end_s": p["end_s"],
        })
    return out


def ai_moments(url: str, work: Path, meta: dict, cfg: Config, top: int) -> list[dict]:
    """The whole option: read the speech (or carry on reading), then let Claude choose."""
    from . import llm

    if not llm.available(cfg):
        raise ToolError(f"AI cannot read the stream: {llm.why_unavailable(cfg)}.")
    transcribe_stream(url, work, cfg, meta.get("duration_s"))
    try:
        return pick_moments(work, meta, cfg, top)
    except llm.LlmUnavailable as e:
        raise ToolError(f"AI could not pick moments: {e}") from e
