"""Find clip-worthy moments from chat activity.

The core idea is ported from the "Chat Clip Finder" gist (chat-clips.mjs):
count chat per 10-second bucket and score each bucket against the *local*
baseline around it, so a moment is "9x louder than this part of the stream",
not just "in the busiest hour".

Differences from the gist, each fixing a weakness found in review:
  * ``trailing`` mode scores against the past only, so it works on a live stream.
  * Activity defaults to unique chatters per bucket, so one account spamming
    does not look like a moment.
  * Hype words and (optional) audio loudness boost the score.
  * Hype-word spikes are scored on their own, for saturated chats where volume is flat.
  * The greeting flood at the start of a stream is skipped.
  * No spread of a huge list into max(), which crashes on very large chats.
  * Results are ranked best-first; ``rank`` 1 is the strongest moment.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from statistics import median
from typing import Sequence

from .chat import ChatMessage
from .config import MomentSettings


@dataclass(frozen=True)
class Bucket:
    index: int
    start_s: float
    messages: int
    chatters: int
    hype: int


@dataclass(frozen=True)
class Moment:
    rank: int
    peak_s: float  # start of the spike bucket
    start_s: float  # where the clip should start
    end_s: float  # where the clip should end
    score: float  # multiple of the local baseline, after boosts
    activity: int  # chatters or messages in the spike bucket (per count_mode)
    messages: int
    hype: int

    def to_dict(self) -> dict:
        return asdict(self)


def compile_hype(words: Sequence[str]) -> re.Pattern[str] | None:
    cleaned = sorted({w.strip() for w in words if w and w.strip()}, key=len, reverse=True)
    if not cleaned:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(w) for w in cleaned) + r")(?!\w)", re.IGNORECASE)


def bucket_chat(
    messages: Sequence[ChatMessage],
    bucket_s: float = 10.0,
    duration_s: float | None = None,
    hype_words: Sequence[str] = (),
) -> list[Bucket]:
    """Count messages, unique chatters and hype messages per fixed-size bucket.

    Messages before t=0 (pre-stream chat) are ignored. ``duration_s`` extends the
    timeline past the last message so a quiet ending still produces buckets.
    """
    if bucket_s <= 0:
        raise ValueError("bucket_s must be greater than 0")
    last_t = 0.0
    for m in messages:
        if m.t > last_t:
            last_t = m.t
    n = int(last_t // bucket_s) + 1 if messages else 0
    if duration_s:
        n = max(n, math.ceil(duration_s / bucket_s))
    counts = [0] * n
    hype = [0] * n
    authors: list[set[str]] = [set() for _ in range(n)]
    anonymous = [0] * n
    pattern = compile_hype(hype_words)
    for m in messages:
        if m.t < 0:
            continue
        i = int(m.t // bucket_s)
        counts[i] += 1
        if m.author:
            authors[i].add(m.author.lower())
        else:
            anonymous[i] += 1  # no author known: each message counts as its own chatter
        if pattern is not None and pattern.search(m.text):
            hype[i] += 1
    return [
        Bucket(index=i, start_s=i * bucket_s, messages=counts[i], chatters=len(authors[i]) + anonymous[i], hype=hype[i])
        for i in range(n)
    ]


def local_baseline_scores(
    values: Sequence[float],
    bucket_s: float = 10.0,
    window_s: float = 300.0,
    mode: str = "symmetric",
    min_neighbors: int = 6,
) -> list[float]:
    """Score each bucket as value / (median of its neighbours + 1).

    symmetric: neighbours are window_s before and after (VOD).
    trailing:  neighbours are window_s before only (live; never uses future data).
    The bucket itself is excluded. Buckets with fewer than ``min_neighbors``
    neighbours score 0, because a baseline from two or three buckets is noise.
    """
    if mode not in ("symmetric", "trailing"):
        raise ValueError(f"mode must be 'symmetric' or 'trailing', got {mode!r}")
    half = max(1, round(window_s / bucket_s))
    n = len(values)
    scores: list[float] = []
    for i in range(n):
        lo = max(0, i - half)
        hi = i if mode == "trailing" else min(n, i + half + 1)
        neighbours = list(values[lo:i]) + list(values[i + 1 : hi])
        if len(neighbours) < min_neighbors:
            scores.append(0.0)
            continue
        scores.append(values[i] / (median(neighbours) + 1.0))
    return scores


def score_buckets(
    buckets: Sequence[Bucket],
    settings: MomentSettings,
    audio: Sequence[float] | None = None,
) -> list[float]:
    """Chat baseline score with hype and audio boosts.

    ``audio`` is an optional per-bucket loudness z-score (0 = typical loudness
    for this stream). Only loud buckets boost; quiet ones are never penalised,
    because a silent shocked pause can still be a moment.
    """
    activity = [b.chatters if settings.count_mode == "chatters" else b.messages for b in buckets]
    scores = local_baseline_scores(activity, settings.bucket_s, settings.window_s, settings.mode, settings.min_neighbors)
    for i, b in enumerate(buckets):
        if scores[i] <= 0:
            continue
        if settings.hype_weight > 0 and b.messages:
            scores[i] *= 1.0 + settings.hype_weight * (b.hype / b.messages)
        if audio is not None and settings.audio_weight > 0 and i < len(audio):
            z = min(max(float(audio[i]), 0.0), 3.0)
            scores[i] *= 1.0 + settings.audio_weight * (z / 3.0)
    if settings.hype_spikes:
        # On a huge stream chat is saturated: everyone is always typing, so volume
        # barely moves when something happens. What does move is WHAT they type.
        # Score the count of hype messages against its own local baseline and
        # keep whichever signal is stronger.
        hype_scores = local_baseline_scores(
            [b.hype for b in buckets], settings.bucket_s, settings.window_s, settings.mode, settings.min_neighbors
        )
        for i, b in enumerate(buckets):
            if b.hype >= settings.min_activity and b.messages and b.hype / b.messages >= settings.hype_spike_min_ratio:
                scores[i] = max(scores[i], hype_scores[i])
    if settings.skip_start_s > 0:
        # The first minutes are a flood of greetings, not a moment.
        for i, b in enumerate(buckets):
            if b.start_s < settings.skip_start_s:
                scores[i] = 0.0
    return scores


def pick_moments(
    buckets: Sequence[Bucket],
    scores: Sequence[float],
    settings: MomentSettings,
    duration_s: float | None = None,
) -> list[Moment]:
    """Greedy pick: best score first, skipping anything within gap_s of a picked moment."""
    order = sorted(range(len(buckets)), key=lambda i: (-scores[i], i))
    chosen: list[int] = []
    for i in order:
        if len(chosen) >= settings.top:
            break
        b = buckets[i]
        activity = b.chatters if settings.count_mode == "chatters" else b.messages
        if scores[i] < settings.min_score or activity < settings.min_activity:
            continue
        if any(abs(b.start_s - buckets[j].start_s) < settings.gap_s for j in chosen):
            continue
        chosen.append(i)
    moments: list[Moment] = []
    for rank, i in enumerate(chosen, 1):
        b = buckets[i]
        start = max(0.0, b.start_s - settings.before_s)
        end = b.start_s + settings.after_s
        if duration_s:
            end = min(end, duration_s)
        moments.append(
            Moment(
                rank=rank,
                peak_s=b.start_s,
                start_s=start,
                end_s=end,
                score=round(scores[i], 2),
                activity=b.chatters if settings.count_mode == "chatters" else b.messages,
                messages=b.messages,
                hype=b.hype,
            )
        )
    return moments


def find_moments(
    messages: Sequence[ChatMessage],
    settings: MomentSettings,
    duration_s: float | None = None,
    audio: Sequence[float] | None = None,
) -> list[Moment]:
    buckets = bucket_chat(messages, settings.bucket_s, duration_s, settings.hype_words)
    scores = score_buckets(buckets, settings, audio)
    return pick_moments(buckets, scores, settings, duration_s)


def format_time(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
