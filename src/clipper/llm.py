"""Claude writes the words: hook, title, description, hashtags and narration lines.

Needs ANTHROPIC_API_KEY in .env. Without a key every caller falls back to
plain text built from the transcript, so the pipeline never depends on it.
Each call is logged to <data_dir>/usage.jsonl with its token counts and cost.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from .config import Config, load_env

log = logging.getLogger(__name__)

# USD per million tokens (input, output), for the usage log only.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50), "claude-fable-5-2": (8.0, 40.0)}
# Models that accept the server-side refusal fallback.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1"}

SYSTEM = """You write the on-screen and caption text for short vertical clips cut from gaming livestreams, \nand short narration lines for commentary videos built from those clips.

You are given one clip: the streamer, the stream title, a transcript of what is said in the clip \n(speech-to-text, so it has mistakes and no punctuation), and a list of hashtags that currently \nwell-viewed YouTube Shorts on the same topic are using.

Decide first whether the clip is worth posting. It is not worth posting when the transcript is filler: \ngreetings, reading chat, giveaways or sub talk, technical problems, silence, a menu, or talk with no \npayoff. Be honest; most automatic clips are filler. If you cannot tell from the transcript, keep it \nand say so in the reason.

Then write:
- hook: on-screen text for the first three seconds. At most 8 words. It states what happens or why to \nkeep watching, in plain words a viewer would use. It must be true of this clip; never promise something \nthe transcript does not show.
- hook_b: a second hook for an A/B test of the same clip. Same rules, but a clearly different angle from hook: \nif hook states what happens, hook_b asks a question or names the stakes, or the other way round.
- title: for YouTube. Under 90 characters. Specific, no ALL CAPS words, no emoji.
- title_b: a second title for the B version, taking the same angle as hook_b.
- description: one or two plain sentences describing the moment.
- tags: 4 to 6 hashtags. Put the streamer and the game first. Prefer tags from the supplied list when \nthey fit this clip; do not use a tag that has nothing to do with it.
- intro: one spoken sentence (at most 22 words) a narrator says before the clip to set it up: who, where \nin the game, what is about to go wrong or right. It adds context the clip itself does not say; it does \nnot describe what the viewer is about to see word for word.
- intro_b: a second narration sentence for the B version (at most 22 words). Same facts, but it opens \ndifferently from intro: for example with a question, or with the stakes instead of the scene.
- rank_line: one spoken sentence (at most 14 words) introducing this clip inside a ranked "top moments" \nvideo, without a number in it.

Write the way a person talks. No hype words such as "insane", "crazy", "you won't believe". \nThe streamer may swear; your text does not."""

SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {"type": "boolean"},
        "reason": {"type": "string"},
        "hook": {"type": "string"},
        "hook_b": {"type": "string"},
        "title": {"type": "string"},
        "title_b": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "intro": {"type": "string"},
        "intro_b": {"type": "string"},
        "rank_line": {"type": "string"},
    },
    "required": ["keep", "reason", "hook", "hook_b", "title", "title_b", "description", "tags", "intro", "intro_b", "rank_line"],
    "additionalProperties": False,
}


class LlmUnavailable(RuntimeError):
    pass


def available(cfg: Config) -> bool:
    load_env()
    return bool(cfg.llm.enabled and os.environ.get("ANTHROPIC_API_KEY"))


def _log_usage(cfg: Config, model: str, usage: Any, what: str) -> None:
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    tokens_in = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0)
    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "service": "anthropic", "model": model, "what": what,
        "input_tokens": tokens_in, "output_tokens": usage.output_tokens,
        "est_usd": round(tokens_in / 1e6 * price_in + usage.output_tokens / 1e6 * price_out, 5),
    }
    path = cfg.data_dir / "usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    log.info("paid API use: %s", entry)


def clip_prompt(state: dict, trend_tags: list[str]) -> str:
    return (
        f"Streamer: {state.get('channel', '')}\n"
        f"Stream title: {state.get('stream_title', '')}\n"
        f"Clip length: {round(float(state.get('cut_end_s', state.get('end_s', 0))) - float(state.get('cut_start_s', state.get('start_s', 0))))} seconds\n"
        f"Chat reaction: {state.get('score')} times the normal chat rate\n"
        f"Hashtags used by well-viewed Shorts on this topic right now: {' '.join(trend_tags) or '(none available)'}\n\n"
        f"Transcript:\n{state.get('transcript') or '(no speech detected)'}"
    )


def ask(cfg: Config, model: str, system: str, user: str, schema: dict, what: str) -> dict:
    """One question to Claude with a JSON answer in the given shape. Raises LlmUnavailable with the reason when it cannot."""
    if not available(cfg):
        raise LlmUnavailable("there is no ANTHROPIC_API_KEY in the .env file on this computer (or llm.enabled is false)")
    import anthropic

    client = anthropic.Anthropic()
    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=16000,
        # The instructions are identical for every call of one kind, so they are cached; only the material varies.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"effort": cfg.llm.effort, "format": {"type": "json_schema", "schema": schema}},
    )
    try:
        if model in FALLBACK_MODELS:
            # If a safety classifier declines the request, the API re-runs it on a fallback model in the same call.
            response = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            response = client.messages.create(**kwargs)
    except anthropic.AuthenticationError as e:
        raise LlmUnavailable("the ANTHROPIC_API_KEY in .env was rejected") from e
    except anthropic.RateLimitError as e:
        raise LlmUnavailable("Claude API rate limit reached; try again in a minute") from e
    except anthropic.APIStatusError as e:
        raise LlmUnavailable(f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LlmUnavailable("could not reach the Claude API (connection problem)") from e
    _log_usage(cfg, model, response.usage, what)
    if response.stop_reason == "refusal":
        raise LlmUnavailable("Claude declined this request")
    if response.stop_reason == "max_tokens":
        raise LlmUnavailable("Claude's reply was cut off")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LlmUnavailable("Claude's reply was not valid JSON") from e


def write_copy(state: dict, trend_tags: list[str], cfg: Config) -> dict:
    """Ask Claude for this clip's text. Raises LlmUnavailable when there is no key or the call fails."""
    return clean_copy(ask(cfg, cfg.llm.model, SYSTEM, clip_prompt(state, trend_tags), SCHEMA, f"copy for clip {state.get('id')}"))


def clean_copy(data: dict) -> dict:
    """Normalise model output so nothing downstream has to trust its shape."""
    tags = []
    for t in data.get("tags") or []:
        t = "#" + "".join(ch for ch in str(t) if ch.isalnum() or ch == "_")
        if len(t) > 1 and t.lower() not in (x.lower() for x in tags):
            tags.append(t)
    return {
        "keep": bool(data.get("keep", True)),
        "reason": str(data.get("reason", "")).strip(),
        "hook": " ".join(str(data.get("hook", "")).split()[:10]),
        "hook_b": " ".join(str(data.get("hook_b", "")).split()[:10]),
        "title": str(data.get("title", "")).strip()[:100],
        "title_b": str(data.get("title_b", "")).strip()[:100],
        "description": str(data.get("description", "")).strip(),
        "tags": tags[:8],
        "intro": str(data.get("intro", "")).strip(),
        "intro_b": str(data.get("intro_b", "")).strip(),
        "rank_line": str(data.get("rank_line", "")).strip(),
    }


def fallback_copy(state: dict, trend_tags: list[str], max_tags: int = 6) -> dict:
    """Plain text built without an LLM, so the pipeline still produces something usable."""
    words = (state.get("transcript") or "").split()
    channel = state.get("channel") or "Stream"
    # Without AI nothing here understands the clip, so the text stays general on purpose:
    # the first few words of the speech, cut off, read as nonsense on screen.
    hook = "Wait for it" if len(words) >= 4 else f"{channel} moment"
    tags = ["#" + "".join(ch for ch in channel if ch.isalnum())] + [t for t in trend_tags if t]
    seen, uniq = set(), []
    for t in tags:
        if len(t) > 1 and t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)
    return {
        "keep": len(words) >= 6,
        "reason": "not judged by AI: kept because the clip has speech" if len(words) >= 6 else "almost no speech in this clip",
        "hook": hook,
        "hook_b": f"Watch what {channel} does next" if len(words) >= 4 else "",
        "title": f"{channel} stream moment",
        "title_b": "",
        "description": "",
        "tags": uniq[:max_tags],
        "intro": f"Here's {channel}.",
        "intro_b": "",
        "rank_line": hook,
    }
