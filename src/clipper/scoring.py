"""One score per clip, 0 to 100, from everything known about it, so the best clip is first in the list.

Each signal is first squeezed to 0..1:
  chat      how many times the normal chat rate (2x is weak, 8x is as strong as it gets)
  audio     how unusual the loudness was
  video     how unusual the motion was
  said      how many excited words are in it ("oh my god", "arre yaar", "bhaag")
  ai_pick   how sure AI was when it picked the moment from the stream's speech (1 to 10)

They are combined so that agreement counts: two medium signals beat one medium signal, and nothing
is lost when a signal is absent ("noisy or"). When AI has rated the finished draft from its words
and frames (0 to 100), that rating carries most of the weight, because it is the only part that
has actually looked at the clip.

Weights start from fixed values and are nudged by what this user approved and rejected before
(see ``learn``): a signal whose clips keep getting rejected counts for less.
"""

from __future__ import annotations

from pathlib import Path

from .work import read_json, write_json

BASE_WEIGHTS = {"chat": 0.80, "audio": 0.50, "video": 0.40, "said": 0.45, "ai_pick": 0.70}
RATING_SHARE = 0.60  # how much of the score the AI rating of the finished draft gets, when there is one
MIN_HISTORY = 8  # reviewed clips needed before a signal's weight is adjusted


# What kind of moments are wanted leans each signal's weight: action trusts loudness and motion more,
# dialogue trusts what AI read in the words more. Balanced leaves the weights as they are.
MIXES = {
    "balanced": {},
    "action": {"audio": 1.3, "video": 1.4, "said": 1.2, "ai_pick": 0.8},
    "dialogue": {"audio": 0.7, "video": 0.5, "said": 0.9, "ai_pick": 1.3},
}


def mixed(weights: dict[str, float] | None, mix: str) -> dict[str, float]:
    """The weights leaned towards the chosen kind of moment (never above 0.95: no signal is ever a certainty)."""
    base = dict(weights or BASE_WEIGHTS)
    for name, factor in MIXES.get(mix, {}).items():
        base[name] = min(0.95, base.get(name, 0.5) * factor)
    return base


def _unit(x: float) -> float:
    return max(0.0, min(1.0, x))


def signal_strengths(state: dict) -> dict[str, float]:
    """Each signal present on this clip, squeezed to 0..1."""
    trigger = str(state.get("trigger") or "chat").split("+")
    signals = state.get("signals") or {}
    out: dict[str, float] = {}
    score = float(state.get("score") or 0)
    if "chat" in trigger:
        out["chat"] = _unit((score - 1.0) / 7.0)
    if "audio" in trigger or "audio" in signals:
        out["audio"] = _unit(float(signals.get("audio", score if trigger == ["audio"] else 0)) / 10.0)
    if "video" in trigger or "video" in signals:
        out["video"] = _unit(float(signals.get("video", score if trigger == ["video"] else 0)) / 12.0)
    if "said" in trigger or "said" in signals:
        out["said"] = _unit(float(signals.get("said", score if trigger == ["said"] else 0)) / 10.0)
    if state.get("ai_pick"):
        out["ai_pick"] = _unit(float(state.get("ai_strength") or (score if trigger == ["ai"] else 6)) / 10.0)
    return out


def combined(state: dict, weights: dict[str, float] | None = None) -> int:
    """The clip's score, 0 to 100."""
    weights = weights or BASE_WEIGHTS
    miss = 1.0
    for name, x in signal_strengths(state).items():
        miss *= 1.0 - weights.get(name, 0.5) * x
    signal_part = 1.0 - miss
    rating = state.get("ai_rating")
    if isinstance(rating, (int, float)):
        return round(100 * (RATING_SHARE * _unit(rating / 100.0) + (1 - RATING_SHARE) * signal_part))
    return round(100 * signal_part)


def learn(clip_states: list[dict]) -> dict:
    """Weights adjusted by this user's own approvals and rejections, with the numbers behind them.

    For each signal: of the reviewed clips that had it, what share was approved? A signal at the
    overall approval rate keeps its base weight; above it gains, below it loses, within 0.5x to 1.5x.
    """
    reviewed = [s for s in clip_states if s.get("status") in ("approved", "rejected", "deleted")]
    approved = [s for s in reviewed if s.get("status") == "approved"]
    overall = len(approved) / len(reviewed) if reviewed else 0.0
    weights, detail = dict(BASE_WEIGHTS), {}
    for name, base in BASE_WEIGHTS.items():
        had = [s for s in reviewed if name in signal_strengths(s)]
        ok = sum(1 for s in had if s.get("status") == "approved")
        rate = ok / len(had) if had else 0.0
        factor = 1.0
        if len(had) >= MIN_HISTORY and overall > 0:
            factor = max(0.5, min(1.5, rate / overall))
        weights[name] = round(min(0.95, base * factor), 3)
        detail[name] = {"reviewed": len(had), "approved": ok, "rate": round(rate, 2), "weight": weights[name], "base": base}
    return {"weights": weights, "detail": detail, "reviewed": len(reviewed), "approved": len(approved), "overall": round(overall, 2)}


def load_weights(data_dir: Path) -> dict[str, float]:
    saved = read_json(data_dir / "learning.json", {}) or {}
    return {**BASE_WEIGHTS, **(saved.get("weights") or {})}


def relearn(data_dir: Path, clip_states: list[dict]) -> dict:
    result = learn(clip_states)
    write_json(data_dir / "learning.json", result)
    return result
