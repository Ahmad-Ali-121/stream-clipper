"""Claude writes the words: hook, title, description, hashtags and narration lines.

Two ways to reach Claude, chosen with ``llm.backend``:
  api          the Claude API, paid per use. Needs ANTHROPIC_API_KEY in .env.
  claude-code  the official Claude Code command (``claude -p``) installed on this computer and signed in
               with this person's own Claude plan. No API bill; it uses the plan's usage limits instead.
               This program only runs that command. It never reads, stores or passes on anyone's Claude sign-in.

Without either, every caller falls back to plain text, so the pipeline never depends on it.
Each call is logged to <data_dir>/usage.jsonl with its token counts and cost.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import Config, load_env

log = logging.getLogger(__name__)

# USD per million tokens (input, output), for the usage log only.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50), "claude-fable-5-2": (8.0, 40.0)}
# Models that accept the server-side refusal fallback.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1"}

SYSTEM = """You write the on-screen and caption text for short vertical clips cut from gaming livestreams, \nand short narration lines for commentary videos built from those clips.

You are given one clip: the streamer, the stream title, a transcript of what is said in the clip \n(speech-to-text, so it has mistakes and no punctuation), and a list of hashtags that currently \nwell-viewed YouTube Shorts on the same topic are using.

You may also be given three still frames from the clip (near its start, middle and end). Use them to see what \\nthe words do not say: what is on screen, the streamer's face, whether anything visible happens. Do not describe \\nthe frames in your text.

Decide first whether the clip is worth posting. It is not worth posting when the transcript is filler: \ngreetings, reading chat, giveaways or sub talk, technical problems, silence, a menu, or talk with no \npayoff. Be honest; most automatic clips are filler. If you cannot tell from the transcript, keep it \nand say so in the reason.

Give a rating from 0 to 100 for how well this works as a short clip for someone who never saw the stream: \\na clear hook in the first seconds, a payoff before the end, and no need for outside context. 80 and above means \\npost it as it is; around 50 means it needs a better cut or is only mildly interesting; under 30 means filler.

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
        "rating": {"type": "integer"},
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
    "required": ["keep", "rating", "reason", "hook", "hook_b", "title", "title_b", "description", "tags", "intro", "intro_b", "rank_line"],
    "additionalProperties": False,
}


BACKENDS = {"api": "Claude API key (paid per use)", "claude-code": "Claude Code on this computer (uses your Claude plan)"}
CHOICES = {"auto": "Automatic (use whichever works)", **BACKENDS}
CHECK_MODEL = "claude-haiku-5-5"  # the cheapest model: a connection check must cost next to nothing
CHECK_FRESH_S = 6 * 3600
CHECK_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


class LlmUnavailable(RuntimeError):
    pass


def static_problem(cfg: Config, backend: str) -> str:
    """Why this connection cannot even be tried on this computer; "" when its pieces are in place."""
    if backend == "claude-code":
        if not shutil.which("claude"):
            return ("the Claude Code command is not installed on this computer. Install Claude Code, run `claude` once in a terminal "
                    "and sign in with your own Claude plan")
        return ""
    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "there is no ANTHROPIC_API_KEY in the .env file on this computer"
    return ""


def _fingerprint(backend: str) -> str:
    """Changes when the thing a check was made with changes (another key, another install), so old results are not trusted."""
    import hashlib

    load_env()
    what = shutil.which("claude") or "" if backend == "claude-code" else os.environ.get("ANTHROPIC_API_KEY", "")
    return hashlib.sha256(what.encode()).hexdigest()[:12]


def _state_path(cfg: Config):
    return cfg.data_dir / "ai_check.json"


def read_checks(cfg: Config) -> dict:
    """What is known about each connection: {"api": {"ok", "problem", "at", "fresh"}, "claude-code": {...}, "note": str}."""
    try:
        data = json.loads(_state_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    out: dict[str, Any] = {"note": data.get("note", "")}
    for b in BACKENDS:
        c = data.get(b) or {}
        fresh = bool(c) and c.get("id") == _fingerprint(b) and time.time() - float(c.get("at", 0)) < CHECK_FRESH_S
        out[b] = {"ok": c.get("ok") if fresh else None, "problem": c.get("problem", "") if fresh else "", "at": c.get("at"), "fresh": fresh}
    return out


def _remember(cfg: Config, backend: str | None = None, ok: bool | None = None, problem: str = "", note: str | None = None) -> None:
    path = _state_path(cfg)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if backend:
        data[backend] = {"ok": ok, "problem": problem, "at": time.time(), "id": _fingerprint(backend)}
    if note is not None:
        data["note"] = note
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def candidates(cfg: Config) -> list[str]:
    """The connections to try, in order. One for a fixed choice; for "auto", the preferred one first,
    with any that a recent check found broken moved to the back."""
    choice = cfg.llm.backend
    if choice in BACKENDS:
        return [choice]
    order = [cfg.llm.prefer] + [b for b in BACKENDS if b != cfg.llm.prefer]
    order = [b for b in order if b in BACKENDS and not static_problem(cfg, b)]
    checks = read_checks(cfg)
    return sorted(order, key=lambda b: checks[b]["ok"] is False)  # stable: keeps the preference among equals


def why_unavailable(cfg: Config) -> str:
    """Why AI cannot be used on this computer right now, in words a person can act on; "" when it can."""
    if not cfg.llm.enabled:
        return "AI is switched off (llm.enabled is false in the settings)"
    if cfg.llm.backend not in CHOICES:
        return f"llm.backend must be one of {', '.join(CHOICES)}, not {cfg.llm.backend!r}"
    if cfg.llm.backend in BACKENDS:
        return static_problem(cfg, cfg.llm.backend)
    if candidates(cfg):
        return ""
    return "neither way of reaching Claude is set up: " + "; and ".join(static_problem(cfg, b) for b in BACKENDS)


def available(cfg: Config) -> bool:
    return not why_unavailable(cfg)


def check(cfg: Config, backend: str) -> dict:
    """Really try one connection with a one-word request and remember the answer. Returns {"ok", "problem"}."""
    problem = static_problem(cfg, backend)
    if not problem:
        try:
            run = _ask_claude_code if backend == "claude-code" else _ask_api
            run(cfg, CHECK_MODEL, "You reply in JSON only.", "Set ok to true.", CHECK_SCHEMA, "connection check")
        except LlmUnavailable as e:
            problem = str(e)
    _remember(cfg, backend, ok=not problem, problem=problem)
    log.info("Claude connection check, %s: %s", BACKENDS[backend], "works" if not problem else f"does not work ({problem})")
    return {"ok": not problem, "problem": problem}


def _ask_claude_code(cfg: Config, model: str, system: str, user: str, schema: dict, what: str, images: list | None = None) -> dict:
    """The same question through the Claude Code command, signed in with this person's own plan."""
    exe = shutil.which("claude")
    if not exe:
        raise LlmUnavailable(static_problem(cfg, "claude-code"))
    # An empty folder of its own for every question: no project files, settings or hooks get picked up, and two questions
    # asked at the same moment (a help question while a clip's text is written) cannot read each other's files.
    import tempfile

    base = cfg.data_dir / "tmp" / "claude-code"
    base.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="q-", dir=str(base)))
    try:
        return _ask_claude_code_in(folder, exe, cfg, model, system, user, schema, what, images)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _ask_claude_code_in(folder: Path, exe: str, cfg: Config, model: str, system: str, user: str, schema: dict, what: str, images: list | None) -> dict:
    prompt_file = folder / "system.txt"
    prompt_file.write_text(system, encoding="utf-8")
    cmd = [exe, "-p", "--model", model, "--output-format", "json", "--json-schema", json.dumps(schema),
           "--system-prompt-file", str(prompt_file), "--no-session-persistence", "--strict-mcp-config", "--setting-sources", ""]
    if images:
        # The command reads pictures from files: copy them into its own empty folder and let it use only the file reader.
        names = []
        for n, src in enumerate(images):
            dst = folder / f"frame{n + 1}{Path(src).suffix}"
            shutil.copyfile(src, dst)
            names.append(dst.name)
        cmd += ["--tools", "Read", "--allowedTools", "Read"]
        user = (f"Before answering, look at these {len(names)} still frames from the clip by reading the files "
                f"{', '.join(names)} in the current folder (start, middle, end of the clip).\n\n" + user)
    else:
        cmd += ["--tools", ""]
    # With an API key in the environment the command would bill the API instead of using the plan.
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    try:
        proc = subprocess.run(cmd, input=user, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              cwd=str(folder), env=env, timeout=1200)
    except subprocess.TimeoutExpired as e:
        raise LlmUnavailable("Claude Code did not answer within 20 minutes") from e
    except OSError as e:
        raise LlmUnavailable(f"Claude Code could not be started: {e}") from e
    try:
        reply = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as e:
        tail = (proc.stderr or proc.stdout).strip()[-400:]
        raise LlmUnavailable(f"Claude Code gave no usable answer (exit code {proc.returncode}): {tail or 'nothing was printed'}") from e
    if reply.get("is_error") or proc.returncode != 0:
        raise LlmUnavailable(f"Claude Code reported a problem: {str(reply.get('result') or reply.get('subtype') or proc.stderr)[:400]}. "
                             "If it mentions signing in, run `claude` in a terminal and sign in; if it mentions a limit, your plan's usage limit is reached")
    usage = reply.get("usage") or {}
    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "service": "claude-code (your Claude plan, not billed per use)", "model": model, "what": what,
        "input_tokens": sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
        "output_tokens": int(usage.get("output_tokens") or 0), "list_price_usd": reply.get("total_cost_usd"),
    }
    path = cfg.data_dir / "usage.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    log.info("AI use through your Claude plan: %s", entry)
    data = reply.get("structured_output")
    if isinstance(data, dict):
        return data
    try:
        return json.loads(str(reply.get("result") or ""))
    except json.JSONDecodeError as e:
        raise LlmUnavailable("Claude Code's reply was not in the expected form") from e


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
        + (f"This is part {state['part']} of {state['parts']} of one continuous video cut into parts; the screen shows \"Part {state['part']}\". "
           "Write a hook that makes sense for this part and, unless it is the last part, leaves the viewer wanting the next one.\n"
           if state.get("part") and int(state.get("parts") or 0) > 1 else "")
        + (f"Chat reaction: {state.get('score')} times the normal chat rate\n" if state.get("trigger") != "manual" else
           "This range was chosen by hand, so there is no chat reaction to go by.\n")
        +
        f"Hashtags used by well-viewed Shorts on this topic right now: {' '.join(trend_tags) or '(none available)'}\n\n"
        f"Transcript:\n{state.get('transcript') or '(no speech detected)'}"
    )


_used = __import__("threading").local()  # which AI answered this thread's most recent question: a Claude connection's name, or "local"


def used_last() -> str:
    return getattr(_used, "name", "")


def _ask_local(cfg: Config, system: str, user: str, schema: dict, what: str, why_not_claude: str) -> dict:
    """The question to the free AI on this computer. Only for jobs it is big enough for (a clip's text)."""
    from . import localai

    try:
        data = localai.ask(cfg, system, user, schema, what)
    except localai.LocalAiError as e:
        raise LlmUnavailable(f"{why_not_claude}; and {e}") from e
    _used.name = "local"
    log.info("Claude was not used (%s); the free local AI wrote this instead: %s", why_not_claude, what)
    return data


def text_available(cfg: Config) -> bool:
    """Can a clip's text be written by some AI right now: Claude, or the free one on this computer?"""
    if available(cfg):
        return True
    from . import localai

    return cfg.llm.enabled and localai.ready(cfg)


def ask(cfg: Config, model: str, system: str, user: str, schema: dict, what: str, images: list | None = None, local_ok: bool = False) -> dict:
    """One question to Claude with a JSON answer in the given shape. Raises LlmUnavailable with the reason when it cannot.

    With llm.backend "auto" the preferred connection is tried first; if it fails and llm.fallback is on,
    the other one is used and that is said out loud, because it can move work onto a paid key.
    ``local_ok``: when Claude cannot be reached at all, the free AI on this computer may answer instead.
    """
    problem = why_unavailable(cfg)
    if problem:
        if local_ok and cfg.llm.enabled:
            return _ask_local(cfg, system, user, schema, what, problem)
        raise LlmUnavailable(problem)
    order = candidates(cfg)
    if not cfg.llm.fallback:
        order = order[:1]
    failures: list[str] = []
    for n, backend in enumerate(order):
        try:
            data = (_ask_claude_code if backend == "claude-code" else _ask_api)(cfg, model, system, user, schema, what, images)
        except LlmUnavailable as e:
            _remember(cfg, backend, ok=False, problem=str(e))
            failures.append(f"{BACKENDS[backend]}: {e}")
            if n + 1 < len(order):
                note = (f"{time.strftime('%H:%M')} {BACKENDS[backend]} did not work ({e}), so {BACKENDS[order[n + 1]]} was used instead.")
                log.warning("AI FALLBACK: %s", note)
                _remember(cfg, note=note)
            continue
        _remember(cfg, backend, ok=True)
        _used.name = backend
        return data
    if local_ok:
        return _ask_local(cfg, system, user, schema, what, " | ".join(failures))
    raise LlmUnavailable(" | ".join(failures))


def _ask_api(cfg: Config, model: str, system: str, user: str, schema: dict, what: str, images: list | None = None) -> dict:
    """The question through the Claude API, billed to the key in .env."""
    problem = static_problem(cfg, "api")
    if problem:
        raise LlmUnavailable(problem)
    import anthropic

    client = anthropic.Anthropic()
    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=16000,
        # The instructions are identical for every call of one kind, so they are cached; only the material varies.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": _with_images(user, images)}],
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


def _with_images(user: str, images: list | None):
    """The user turn for the API: the pictures first, then the text."""
    if not images:
        return user
    import base64

    blocks: list[dict] = []
    for src in images:
        kind = "image/png" if str(src).lower().endswith(".png") else "image/jpeg"
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": kind, "data": base64.standard_b64encode(Path(src).read_bytes()).decode()}})
    return blocks + [{"type": "text", "text": user}]


TEXT_LANGUAGES = {
    "English": "",
    "Roman Urdu": "Roman Urdu mixed with English: Urdu written in Latin letters, the way Pakistani viewers type in chat (for example "
                  "\"Bhai ne kamaal kar diya\"), with English words kept in English wherever people say them in English "
                  "(game, mission, police, stream, level). Never use Urdu or Hindi letters.",
    "Hinglish": "Hinglish: Hindi written in Latin letters and mixed with English, the way Indian viewers type in chat "
                "(for example \"Bhai ne toh kamaal kar diya\").",
    "Urdu": "Urdu, in Urdu script.",
    "Hindi": "Hindi, in Devanagari script.",
}


def language_note(cfg: Config) -> str:
    """Extra lines for the clip prompt when the stream or the audience is not English."""
    note = ""
    spoken = {"ur": "Urdu", "hi": "Hindi"}.get(cfg.transcribe.language)
    if spoken:
        note += (f"\n\nThe streamer speaks {spoken}, often mixed with English. The transcript may be in {spoken} letters or in Latin letters. "
                 "Judge the clip as a viewer who speaks that language would.")
    how = TEXT_LANGUAGES.get(cfg.llm.text_language, cfg.llm.text_language)
    if how:
        note += (f"\n\nWrite hook, hook_b, title, title_b and description in {how} Keep hashtags in Latin letters. "
                 "Write intro, intro_b and rank_line in English: the narrator's voice is English. Write the reason in English.")
    return note


def write_copy(state: dict, trend_tags: list[str], cfg: Config, images: list | None = None) -> dict:
    """Ask Claude for this clip's text and rating. Raises LlmUnavailable when there is no key or the call fails."""
    return clean_copy(ask(cfg, cfg.llm.model, SYSTEM, clip_prompt(state, trend_tags) + language_note(cfg), SCHEMA,
                          f"copy for clip {state.get('id')}", images, local_ok=True))


ROMAN_SYSTEM = """You are given the words of a speech-to-text transcript of a Hindi or Urdu gaming stream, as a JSON list, in order.

Return a list of exactly the same length. Each item is the same word written in Latin letters, the way people type Hindi or \nUrdu in chat (Roman Urdu, Hinglish): "bhai", "kya", "nahi", "yaar". An English word that was written in Urdu or Devanagari \nletters becomes its normal English spelling ("game", "mission", "police"). A word already in Latin letters stays as it is. \nKeep any punctuation attached to its word. Never merge, split, add, drop or reorder items: item 7 in must be item 7 out."""
ROMAN_SCHEMA = {"type": "object", "properties": {"words": {"type": "array", "items": {"type": "string"}}}, "required": ["words"], "additionalProperties": False}


def romanize(words: list[str], cfg: Config, chunk: int = 150) -> list[str]:
    """The same words in Latin letters, one for one. Raises LlmUnavailable if Claude cannot be used or returns another count."""
    out: list[str] = []
    for i in range(0, len(words), chunk):
        part = words[i:i + chunk]
        data = ask(cfg, cfg.llm.roman_model or cfg.llm.model, ROMAN_SYSTEM, json.dumps(part, ensure_ascii=False), ROMAN_SCHEMA, "captions in Latin letters")
        got = [str(x).strip() for x in (data.get("words") or [])]
        if len(got) != len(part) or not all(got):
            raise LlmUnavailable(f"Claude returned {len(got)} words for {len(part)}; that reply cannot be used")
        out += got
    return out


def clean_copy(data: dict) -> dict:
    """Normalise model output so nothing downstream has to trust its shape."""
    tags = []
    for t in data.get("tags") or []:
        t = "#" + "".join(ch for ch in str(t) if ch.isalnum() or ch == "_")
        if len(t) > 1 and t.lower() not in (x.lower() for x in tags):
            tags.append(t)
    return {
        "keep": bool(data.get("keep", True)),
        "rating": max(0, min(100, int(data["rating"]))) if isinstance(data.get("rating"), (int, float)) else None,
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
        "rating": None,
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
