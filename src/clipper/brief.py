"""Check a clip against a campaign brief before rendering/posting.

A campaign only pays when the post matches its brief, and a missed brief detail
is the most common rejection reason. This catches the mechanical ones (wrong
source, too long, wrong shape, missing tag or disclosure). Things a script
cannot judge are returned as "manual" items for the human review step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Brief:
    name: str
    allowed_sources: list[str] = field(default_factory=list)  # URLs or channel handles the clip must come from
    min_duration_s: float = 0.0
    max_duration_s: float | None = None
    aspect: str | None = "9:16"
    required_tags: list[str] = field(default_factory=list)  # must appear in the post caption, e.g. "#ad"
    required_text: list[str] = field(default_factory=list)  # other phrases that must appear in the caption
    require_disclosure_overlay: bool = False  # on-screen ad disclosure burned into the video
    forbid_watermark: bool = True
    manual_checks: list[str] = field(default_factory=list)  # extra rules only a human can judge


@dataclass
class ClipInfo:
    source_url: str
    duration_s: float
    width: int
    height: int
    caption: str = ""
    has_disclosure_overlay: bool = False


@dataclass(frozen=True)
class Issue:
    level: str  # "fail" = will be rejected; "manual" = a human must confirm
    message: str


def load_brief(path: str | Path) -> Brief:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"brief file not found: {p}")
    data: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or "name" not in data:
        raise ValueError(f"{p.name}: a brief must be a mapping with at least a 'name'")
    known = set(Brief.__dataclass_fields__)
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"{p.name}: unknown brief keys: {', '.join(sorted(unknown))}")
    # One value written where a list belongs ("required_tags: '#ad'") is that one entry, not its letters; and a
    # number written in quotes is a number. Anything else of the wrong kind is said plainly instead of misjudging clips.
    for key in ("allowed_sources", "required_tags", "required_text", "manual_checks"):
        if isinstance(data.get(key), str):
            data[key] = [data[key]]
        elif key in data and data[key] is None:
            data[key] = []
        elif key in data and not (isinstance(data[key], list) and all(isinstance(x, (str, int, float)) for x in data[key])):
            raise ValueError(f"{p.name}: '{key}' must be a list of texts")
        elif key in data:
            data[key] = [str(x) for x in data[key]]
    for key in ("min_duration_s", "max_duration_s"):
        if data.get(key) is not None:
            try:
                data[key] = float(data[key])
            except (TypeError, ValueError):
                raise ValueError(f"{p.name}: '{key}' must be a number of seconds, not {data[key]!r}") from None
    for key in ("require_disclosure_overlay", "forbid_watermark"):
        if key in data and not isinstance(data[key], bool):
            raise ValueError(f"{p.name}: '{key}' must be true or false, not {data[key]!r}")
    if data.get("aspect") is not None and not re.fullmatch(r"\d+(\.\d+)?:\d+(\.\d+)?", str(data["aspect"])):
        raise ValueError(f"{p.name}: 'aspect' must look like 9:16, not {data['aspect']!r}")
    return Brief(**data)


_YT_ID = re.compile(r"(?:youtu\.be/|[?&]v=|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")


def _normalise(url: str) -> str:
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^(www\.|m\.)", "", u)
    return u.rstrip("/")


def _youtube_id(url: str) -> str | None:
    m = _YT_ID.search(url)
    return m.group(1) if m else None


def source_allowed(source_url: str, allowed: list[str]) -> bool:
    """True when the clip's source matches one allowed entry.

    An entry can be a full video URL, a channel URL/handle (matched as a
    substring of the source URL), or a YouTube URL in any of its forms
    (watch?v=, youtu.be/, /live/, /shorts/ all compare by video id).
    """
    src = _normalise(source_url)
    src_id = _youtube_id(source_url)
    for entry in allowed:
        entry_id = _youtube_id(entry)
        if entry_id and src_id:
            if entry_id == src_id:
                return True
            continue
        want = _normalise(entry)
        at = src.find(want) if want else -1
        # the whole name, not its beginning: "twitch.tv/lacy" must not allow "twitch.tv/lacyfan"
        if at >= 0 and (at + len(want) == len(src) or not (src[at + len(want)].isalnum() or src[at + len(want)] in "_-")):
            return True
    return False


def _has_token(text: str, token: str) -> bool:
    # "#ad" must not be satisfied by "#advert" or "#adidas".
    return re.search(r"(?<![\w#])" + re.escape(token) + r"(?!\w)", text, re.IGNORECASE) is not None


def _aspect_ok(width: int, height: int, aspect: str, tolerance: float = 0.01) -> bool:
    w, h = (float(x) for x in aspect.split(":"))
    if height <= 0:
        return False
    return abs(width / height - w / h) <= tolerance * (w / h)


def check_clip(clip: ClipInfo, brief: Brief) -> list[Issue]:
    issues: list[Issue] = []
    if brief.allowed_sources and not source_allowed(clip.source_url, brief.allowed_sources):
        issues.append(Issue("fail", f"source {clip.source_url} is not one of the campaign's allowed sources"))
    if clip.duration_s < brief.min_duration_s:
        issues.append(Issue("fail", f"clip is {clip.duration_s:.1f}s, shorter than the {brief.min_duration_s:g}s minimum"))
    if brief.max_duration_s is not None and clip.duration_s > brief.max_duration_s:
        issues.append(Issue("fail", f"clip is {clip.duration_s:.1f}s, longer than the {brief.max_duration_s:g}s maximum"))
    if brief.aspect and not _aspect_ok(clip.width, clip.height, brief.aspect):
        issues.append(Issue("fail", f"clip is {clip.width}x{clip.height}, not {brief.aspect}"))
    for tag in brief.required_tags:
        if not _has_token(clip.caption, tag):
            issues.append(Issue("fail", f"caption is missing the required tag {tag}"))
    for phrase in brief.required_text:
        if phrase.lower() not in clip.caption.lower():
            issues.append(Issue("fail", f"caption is missing the required text: {phrase}"))
    if brief.require_disclosure_overlay and not clip.has_disclosure_overlay:
        issues.append(Issue("fail", "the brief requires an on-screen ad disclosure and the clip has none"))
    if brief.forbid_watermark:
        issues.append(Issue("manual", "confirm no watermark or other app's logo is visible"))
    for item in brief.manual_checks:
        issues.append(Issue("manual", item))
    return issues


def passes(issues: list[Issue]) -> bool:
    return not any(i.level == "fail" for i in issues)
