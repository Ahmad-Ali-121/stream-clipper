"""Load chat logs from every source into one shape: a time-sorted list of ChatMessage.

Supported inputs (auto-detected):
  * TwitchDownloaderCLI ``chatdownload`` JSON  ({"comments": [...]})
  * yt-dlp YouTube ``live_chat`` JSON lines    ({"replayChatItemAction": ...} per line)
  * native JSON lines written by our own live capturers: {"t": 12.3, "author": "x", "text": "hi"}

``t`` is always seconds from the start of the stream/recording.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)


class ChatFormatError(ValueError):
    pass


@dataclass(frozen=True)
class ChatMessage:
    t: float
    author: str = ""
    text: str = ""


def _from_twitch(data: dict[str, Any]) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    for c in data.get("comments") or []:
        t = c.get("content_offset_seconds")
        if t is None:
            continue
        commenter = c.get("commenter") or {}
        message = c.get("message") or {}
        out.append(
            ChatMessage(
                t=float(t),
                author=str(commenter.get("display_name") or commenter.get("name") or ""),
                text=str(message.get("body") or ""),
            )
        )
    return out


def _youtube_text(renderer: dict[str, Any]) -> str:
    parts: list[str] = []
    for run in (renderer.get("message") or {}).get("runs") or []:
        if "text" in run:
            parts.append(str(run["text"]))
        elif "emoji" in run:
            # Standard emoji carry the character as emojiId; custom channel emotes
            # carry a long id, so use their :shortcut: name instead.
            emoji = run["emoji"] or {}
            shortcuts = emoji.get("shortcuts") or []
            emoji_id = str(emoji.get("emojiId") or "")
            parts.append(emoji_id if emoji_id and len(emoji_id) <= 4 else (str(shortcuts[0]) if shortcuts else ""))
    return "".join(parts)


def _from_youtube(lines: Iterable[dict[str, Any]]) -> list[ChatMessage]:
    # Replays carry videoOffsetTimeMsec. Chat captured while live may not, so we
    # fall back to wall-clock timestamps relative to the first message. That
    # fallback is only as accurate as "first message == recording start".
    raw: list[tuple[float | None, float | None, str, str]] = []
    for obj in lines:
        replay = obj.get("replayChatItemAction") or obj
        offset = replay.get("videoOffsetTimeMsec")
        for action in replay.get("actions") or []:
            item = (action.get("addChatItemAction") or {}).get("item") or {}
            renderer = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
            if not renderer:
                continue
            ts = renderer.get("timestampUsec")
            raw.append(
                (
                    float(offset) / 1000.0 if offset is not None else None,
                    float(ts) / 1_000_000.0 if ts is not None else None,
                    str((renderer.get("authorName") or {}).get("simpleText") or ""),
                    _youtube_text(renderer),
                )
            )
    if not raw:
        return []
    if all(offset is not None for offset, *_ in raw):
        return [ChatMessage(t=offset, author=a, text=x) for offset, _, a, x in raw]  # type: ignore[arg-type]
    stamps = [ts for _, ts, *_ in raw if ts is not None]
    if not stamps:
        raise ChatFormatError("YouTube chat has neither video offsets nor timestamps")
    log.warning("YouTube chat has no video offsets; using timestamps relative to the first message")
    t0 = min(stamps)
    return [ChatMessage(t=ts - t0, author=a, text=x) for _, ts, a, x in raw if ts is not None]


def _from_native(lines: Iterable[dict[str, Any]]) -> list[ChatMessage]:
    return [
        ChatMessage(t=float(o["t"]), author=str(o.get("author") or ""), text=str(o.get("text") or ""))
        for o in lines
        if "t" in o
    ]


def parse_chat(text: str) -> list[ChatMessage]:
    """Parse chat file contents. Raises ChatFormatError when the format is not recognised."""
    stripped = text.lstrip("﻿ \t\r\n")
    if not stripped:
        return []
    first_line = stripped.split("\n", 1)[0].strip()
    first: Any = None
    try:
        first = json.loads(first_line)
    except json.JSONDecodeError:
        pass

    if isinstance(first, dict) and "comments" in first:
        messages = _from_twitch(first)
    elif isinstance(first, dict) and ("replayChatItemAction" in first or "t" in first or "actions" in first):
        objs = []
        for n, line in enumerate(stripped.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # A recorder killed mid-write leaves a torn last line; skip it rather than lose the file.
                log.warning("skipping unreadable chat line %d", n)
                continue
            if isinstance(obj, dict):
                objs.append(obj)
        messages = _from_native(objs) if "t" in first else _from_youtube(objs)
    else:
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ChatFormatError(f"chat file is not valid JSON or JSON lines: {e}") from e
        if isinstance(data, dict) and "comments" in data:
            messages = _from_twitch(data)
        else:
            raise ChatFormatError(
                "unrecognised chat format (expected TwitchDownloader JSON, yt-dlp live_chat JSON lines, or native JSON lines)"
            )
    messages.sort(key=lambda m: m.t)
    return messages


def load_chat(path: str | Path) -> list[ChatMessage]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"chat file not found: {p}")
    return parse_chat(p.read_text(encoding="utf-8", errors="replace"))
