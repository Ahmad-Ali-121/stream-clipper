"""Move a clip's start and end to natural pauses in the speech.

Moments come from chat timing, so the raw 30-second window usually starts or
ends in the middle of a sentence. With word timestamps the cut can slide to the
nearest pause, within the few seconds of padding that were fetched.
"""

from __future__ import annotations

from typing import Sequence

LEAD_IN_S = 0.15  # start slightly before the first word so it is not clipped
TAIL_S = 0.30  # let the last word ring out


def snap_cut(
    words: Sequence[dict],
    start: float,
    end: float,
    lo: float,
    hi: float,
    max_shift: float = 4.0,
    min_gap: float = 0.35,
) -> tuple[float, float]:
    """Return (start, end) moved to nearby pauses.

    ``words`` are {"start", "end"} in the same time base as start/end/lo/hi.
    A boundary only moves if a pause exists within ``max_shift`` seconds of it
    and inside [lo, hi]; otherwise it stays where it was.
    """
    ws = sorted(({"start": float(w["start"]), "end": float(w["end"])} for w in words), key=lambda w: w["start"])
    if not ws:
        return start, end
    starts = [w["start"] for i, w in enumerate(ws) if i == 0 or w["start"] - ws[i - 1]["end"] >= min_gap]
    ends = [w["end"] for i, w in enumerate(ws) if i == len(ws) - 1 or ws[i + 1]["start"] - w["end"] >= min_gap]

    new_start = start
    options = [max(lo, s - LEAD_IN_S) for s in starts if abs(s - LEAD_IN_S - start) <= max_shift]
    if options:
        new_start = min(options, key=lambda s: abs(s - start))
    new_end = end
    options = [min(hi, e + TAIL_S) for e in ends if abs(e + TAIL_S - end) <= max_shift]
    options = [e for e in options if e - new_start >= 5.0]  # never snap a clip down to almost nothing
    if options:
        new_end = min(options, key=lambda e: abs(e - end))
    return round(new_start, 2), round(new_end, 2)


def apply_snap(state: dict, words: Sequence[dict], max_shift: float = 4.0) -> dict:
    """Set cut_start_s / cut_end_s on a clip from its transcript, unless a person already set them."""
    if ("cut_start_s" in state or "cut_end_s" in state) and not state.get("cut_auto"):
        return state
    fetch_start = float(state.get("fetch_start_s", state["start_s"]))
    hi = float(state.get("source_duration_s") or (state.get("fetch_end_s", state["end_s"]) - fetch_start))
    s, e = snap_cut(words, state["start_s"] - fetch_start, state["end_s"] - fetch_start, 0.0, hi, max_shift)
    state["cut_start_s"], state["cut_end_s"], state["cut_auto"] = round(fetch_start + s, 2), round(fetch_start + e, 2), True
    return state
