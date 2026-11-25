"""Working with a video's words directly: search them, hand them to a free chatbot in parts, and subtitle files.

  - ``video_lines``: a video's whole transcript as short timed lines, for the Transcript view (search a phrase,
    mark where a clip should start and end).
  - ``chatbot_parts`` / ``parse_answers``: for people with no AI set up. A chatbot given a nine-hour transcript only
    really reads the start, so the transcript is cut into parts of about 25 minutes, each with a ready prompt to
    paste into any free chatbot by hand; the answers are pasted back and read as moments. Nothing is sent anywhere
    by this program.
  - ``to_srt`` / ``to_vtt`` / ``parse_subtitles``: a clip's captions as a subtitle file, and a subtitle file as captions.

The transcript search and the chatbot parts are ideas from the open-source clipper NeonClipping; the code is our own.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Config
from .moments import format_time
from .work import read_json

PART_MINUTES = 25
MAX_CLIP_S = 300.0
MIN_CLIP_S = 8.0


def video_lines(url: str, cfg: Config) -> dict:
    """The transcript of a video: {"url", "title", "duration_s", "from", "lines": [{"t", "e", "text"}]}.

    From YouTube's captions when it has them; otherwise from the speech read earlier by "Let AI read the whole
    stream". Raises ValueError with what to do when neither exists.
    """
    from . import ytcaptions
    from .fetch import fetch_meta
    from .pipeline import open_work

    source, work = open_work(url, cfg)
    meta = fetch_meta(source, work, cfg)
    lines, origin = [], ""
    if source.platform == "youtube":
        spoken = ytcaptions.video_words(source.url, work, cfg)
        if spoken:
            lines, origin = ytcaptions.as_lines(spoken), "YouTube's captions"
    if not lines and (work / "stream_text.jsonl").exists():
        from .streamread import read_rows

        lines, origin = read_rows(work / "stream_text.jsonl"), "the speech read on this PC"
    if not lines:
        raise ValueError("there is no transcript for this video yet. YouTube has no captions for it (or it is not a YouTube video). "
                         "Run it once in the Finished stream tab with \"Let AI read the whole stream\" ticked; its words are kept and can be searched here afterwards")
    return {"url": source.url, "title": meta.get("title", ""), "channel": meta.get("channel", ""), "duration_s": float(meta.get("duration_s") or lines[-1]["e"]),
            "from": origin, "lines": lines}


PROMPT = """You are helping pick the best short clips from part of a livestream or video.
Below is PART {n} of {total} of the transcript of "{title}". Each line starts with the time it is said, as [hours:minutes:seconds].

Find up to {count} moments in THIS part that would work as a short vertical clip of 20 to 60 seconds for viewers who never saw the video:
a funny line or exchange, a strong reaction, a fail or a win, a surprising statement, a short story with a payoff. Skip greetings, reading
chat, breaks and setup talk. A moment must make sense on its own. If nothing in this part is good, say so; do not invent moments.

Answer with one line per moment and nothing else, exactly like this:
start-end | a short title
for example:
1:02:30-1:03:15 | He drives the stolen car into the lake

Transcript:
{text}
"""


def chatbot_parts(lines: list[dict], title: str, minutes: float = PART_MINUTES, per_part: int = 4) -> list[dict]:
    """The transcript in parts of about ``minutes``, each {"n", "start_s", "end_s", "prompt"} ready to paste into a chatbot."""
    if not lines:
        return []
    span = max(60.0, minutes * 60)
    groups: list[list[dict]] = []
    for row in lines:
        if not groups or row["t"] - groups[-1][0]["t"] >= span:
            groups.append([])
        groups[-1].append(row)
    out = []
    for n, rows in enumerate(groups, 1):
        text = "\n".join(f"[{format_time(r['t'])}] {r['text']}" for r in rows)
        out.append({"n": n, "start_s": rows[0]["t"], "end_s": rows[-1]["e"], "lines": len(rows),
                    "prompt": PROMPT.format(n=n, total=len(groups), title=title or "this video", count=per_part, text=text)})
    return out


def _clock(text: str) -> float | None:
    t = str(text).strip().lower().replace(",", ".")
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,3}):(\d{1,2}(?:\.\d+)?)", t)
    if m:
        return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.fullmatch(r"(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?", t)
    if m and any(m.groups()):
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3) or 0)
    return float(t) if re.fullmatch(r"\d+(?:\.\d+)?", t) else None


_RANGE = re.compile(r"\[?\(?\s*((?:\d+:)?\d{1,3}:\d{1,2}(?:\.\d+)?)\s*\]?\)?\s*(?:-|‐|‑|‒|–|—|to|->|→)\s*\[?\(?\s*((?:\d+:)?\d{1,3}:\d{1,2}(?:\.\d+)?)\s*\]?\)?[\s|:\-–—.]*(.*)")


def parse_answers(text: str, duration_s: float = 0.0) -> dict:
    """Moments out of what a chatbot answered: {"moments": [{"start_s", "end_s", "title"}], "skipped": [reason, ...]}.

    Accepts lines like ``1:02:30-1:03:15 | title`` (with or without brackets, bullets or numbering) and JSON lists of
    {"start", "end", "title"}. A moment that is too short, too long or outside the video is left out, and said.
    """
    found: list[tuple[float, float, str]] = []
    skipped: list[str] = []
    text = re.sub(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$", "", str(text).strip())  # an answer wrapped in a code fence
    stripped = text.strip()
    data = None
    if stripped[:1] in "[{":
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
    if isinstance(data, dict):
        data = data.get("moments") or data.get("clips") or []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                a, b = _clock(item.get("start", item.get("start_s", ""))), _clock(item.get("end", item.get("end_s", "")))
                if a is not None and b is not None:
                    found.append((a, b, str(item.get("title") or item.get("hook") or "")))
    else:
        for line in text.splitlines():
            m = _RANGE.search(line)
            if m:
                found.append((_clock(m.group(1)), _clock(m.group(2)), m.group(3).strip(" \"'*|")))
    moments: list[dict] = []
    for a, b, title in sorted(found):
        label = f"{format_time(a)} to {format_time(b)}"
        if b - a < MIN_CLIP_S:
            skipped.append(f"{label}: shorter than {MIN_CLIP_S:g} seconds")
        elif b - a > MAX_CLIP_S:
            skipped.append(f"{label}: longer than {MAX_CLIP_S / 60:g} minutes")
        elif duration_s and b > duration_s + 1:  # the same second of slack as the job that cuts it (pipeline.split_range)
            skipped.append(f"{label}: after the end of the video")
        elif moments and a < moments[-1]["end_s"]:
            skipped.append(f"{label}: overlaps the moment before it")
        else:
            moments.append({"start_s": round(a, 2), "end_s": round(b, 2), "title": title[:120]})
    return {"moments": moments, "skipped": skipped}


# ---- subtitle files ----


def _stamp(t: float, comma: bool) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}{',' if comma else '.'}{ms % 1000:03d}"


def clip_cues(clip_dir: Path, cfg: Config) -> list[tuple[float, float, str]]:
    """A clip's captions as subtitle cues (start, end, text), timed from the start of the finished clip."""
    from .captions import group_words, words_from_json
    from .render import cut_window

    state = read_json(clip_dir / "clip.json")
    offset, duration = cut_window(state)
    spoken = [w for w in words_from_json(read_json(clip_dir / "words.json", []), offset_s=offset) if w.start < duration]
    cues = []
    lines = group_words(spoken, max_words=7, max_chars=42, max_gap_s=0.8)
    for i, line in enumerate(lines):
        end = min(line[-1].end + 0.15, lines[i + 1][0].start if i + 1 < len(lines) else duration)
        cues.append((line[0].start, max(end, line[0].start + 0.3), " ".join(w.text for w in line)))
    return cues


def to_srt(cues: list[tuple[float, float, str]]) -> str:
    return "".join(f"{n}\n{_stamp(a, True)} --> {_stamp(b, True)}\n{text}\n\n" for n, (a, b, text) in enumerate(cues, 1))


def to_vtt(cues: list[tuple[float, float, str]]) -> str:
    return "WEBVTT\n\n" + "".join(f"{_stamp(a, False)} --> {_stamp(b, False)}\n{text}\n\n" for a, b, text in cues)


_CUE = re.compile(r"(\d{1,2}:)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(\d{1,2}:)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def parse_subtitles(text: str) -> list[tuple[float, float, str]]:
    """Cues out of an .srt or .vtt file's text. Raises ValueError when there are none."""
    cues: list[tuple[float, float, str]] = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.replace("﻿", ""))
    for block in blocks:
        rows = [r for r in block.splitlines() if r.strip()]
        for i, row in enumerate(rows):
            m = _CUE.search(row)
            if not m:
                continue
            g = m.groups()
            a = int((g[0] or "0:")[:-1]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3].ljust(3, "0")) / 1000
            b = int((g[4] or "0:")[:-1]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7].ljust(3, "0")) / 1000
            said = re.sub(r"<[^>]+>|\{[^}]*\}", "", " ".join(rows[i + 1:])).strip()
            if said and b > a:
                cues.append((a, b, said))
            break
    if not cues:
        raise ValueError("no subtitles were found in that file. It must be an .srt or .vtt file with lines like 00:00:01,000 --> 00:00:03,500")
    return sorted(cues)


def words_from_cues(cues: list[tuple[float, float, str]], shift_s: float = 0.0) -> list[dict]:
    """Subtitle cues as timed words. A file times a whole line, so the line's words are spread evenly over it."""
    out: list[dict] = []
    for a, b, text in cues:
        parts = text.split()
        step = (b - a) / max(1, len(parts))
        for i, word in enumerate(parts):
            out.append({"start": round(shift_s + a + i * step, 3), "end": round(shift_s + a + (i + 1) * step, 3), "word": word, "p": 1.0})
    return out
