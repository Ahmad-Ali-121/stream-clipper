"""Prepare approved clips for manual posting: copy the video and write a post sheet.

Nothing is uploaded. For each approved, rendered clip this writes into
<data_dir>/ready/<date>/ :
    <channel>_<clip id>.mp4     the video to upload
    <channel>_<clip id>.txt     title, description and hashtags per platform, ready to copy

Text comes from the clip's clip.json: ``title``, ``description``, ``tags``.
When those are empty, plain defaults are built from the hook and channel; they
are meant to be improved (by Claude in a session, or by hand) before posting.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path

from .brief import Brief, ClipInfo, check_clip, passes
from .moments import format_time
from .work import done, read_json, write_json, write_text

log = logging.getLogger(__name__)

YOUTUBE_TITLE_MAX = 100


def _tag(text: str) -> str:
    return "#" + re.sub(r"[^0-9A-Za-z_]", "", text)


def build_tags(state: dict, brief: Brief | None) -> list[str]:
    tags: list[str] = []
    for t in (brief.required_tags if brief else []) + list(state.get("tags") or []):
        t = t if t.startswith("#") else _tag(t)
        if len(t) > 1 and t.lower() not in (x.lower() for x in tags):
            tags.append(t)
    if not state.get("tags"):
        for t in (_tag(state.get("channel", "")), "#clips", "#streamer"):
            if len(t) > 1 and t.lower() not in (x.lower() for x in tags):
                tags.append(t)
    return tags


def build_caption(state: dict, brief: Brief | None) -> tuple[str, str, list[str]]:
    """(title, description, tags) for this clip, with required campaign text included."""
    title = (state.get("title") or state.get("hook") or f"{state.get('channel', 'Stream')} clip").strip()
    if state.get("part") and int(state.get("parts") or 0) > 1 and f"part {state['part']}" not in title.lower():
        title = f"{title} (Part {state['part']})"
    description = (state.get("description") or "").strip()
    credit = f"Clip from {state.get('channel')}'s stream: {state.get('source_url')}" if state.get("channel") else ""
    for phrase in brief.required_text if brief else []:
        if phrase.lower() not in (title + " " + description).lower():
            description = (description + "\n" + phrase).strip()
    description = "\n".join(x for x in (description, credit) if x)
    return title, description, build_tags(state, brief)


def campaign_check(state: dict, brief: Brief, width: int = 1080, height: int = 1920) -> list[dict]:
    """What a campaign's rules say about a clip as it stands now, before any video is made."""
    title, description, tags = build_caption(state, brief)
    start, end = float(state.get("cut_start_s", state.get("start_s", 0))), float(state.get("cut_end_s", state.get("end_s", 0)))
    clip = ClipInfo(source_url=state.get("source_url", ""), duration_s=round(end - start, 1), width=width, height=height,
                    caption=f"{title}\n{description}\n{' '.join(tags)}", has_disclosure_overlay=bool(state.get("disclosure")))
    return [{"level": i.level, "message": i.message} for i in check_clip(clip, brief)]


def post_sheet(state: dict, video_name: str, brief: Brief | None) -> tuple[str, list]:
    title, description, tags = build_caption(state, brief)
    tag_line = " ".join(tags)
    yt_title = title if len(title) <= YOUTUBE_TITLE_MAX else title[: YOUTUBE_TITLE_MAX - 1].rstrip() + "…"
    short_caption = f"{title} {tag_line}".strip()
    issues = []
    lines = [
        f"VIDEO FILE: {video_name}",
        f"SOURCE: {state.get('channel')} | {state.get('vod_url')} at {format_time(state.get('cut_start_s', state['start_s']))}",
        f"LENGTH: {state.get('duration_s')}s   SIZE: {state.get('width')}x{state.get('height')}",
        "",
        "=== YOUTUBE SHORTS ===",
        f"Title: {yt_title}",
        "Description:",
        f"{description}\n{tag_line}".strip(),
        "",
        "=== TIKTOK ===",
        f"Caption: {short_caption}",
        "",
        "=== INSTAGRAM REELS ===",
        "Caption:",
        f"{title}\n{description}\n{tag_line}".strip(),
        "",
        "=== FACEBOOK REELS ===",
        f"Caption: {short_caption}",
        "",
    ]
    if brief:
        clip = ClipInfo(
            source_url=state.get("source_url", ""),
            duration_s=float(state.get("duration_s") or 0),
            width=int(state.get("width") or 0),
            height=int(state.get("height") or 0),
            caption=f"{title}\n{description}\n{tag_line}",
            has_disclosure_overlay=bool(state.get("has_disclosure_overlay")),
        )
        issues = check_clip(clip, brief)
        lines += [f"=== CAMPAIGN: {brief.name} ===", "RESULT: " + ("passes the automatic checks" if passes(issues) else "WOULD BE REJECTED, fix before posting")]
        lines += [f"  [{'FAIL' if i.level == 'fail' else 'check by hand'}] {i.message}" for i in issues]
        lines += ["  Submit the post link to the campaign right after posting (Content Rewards: within 30 minutes).", ""]
    lines += [
        "=== SECOND ACCOUNT ===",
        "The same file and text can be used on another person's own accounts for a DIFFERENT campaign or platform.",
        "Do not submit the same clip to the same campaign twice; campaigns reject duplicates.",
    ]
    return "\n".join(lines) + "\n", issues


def _video_seconds(path: Path) -> float | None:
    """Length of a video file, or None when it cannot be read."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        fps, frames = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
        return round(frames / fps, 2) if fps and frames else None
    finally:
        cap.release()


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
# Which version of a clip a file-name ending stands for: a and b are the plain video, xa and xb the explainer.
VERSION_OF = {"": "a", "_A": "a", "_B": "b", "_explained": "xa", "_explained_A": "xa", "_explained_B": "xb"}


def safe_name(text: str, limit: int = 90) -> str:
    """A title as a file name Windows accepts: no \\ / : * ? " < > |, no line breaks, not too long. Empty when nothing is left."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip(" .")[:limit].rstrip(" .")
    return "" if not text or text.split(".")[0].upper() in _RESERVED else text


def export_stem(state: dict, clip_id: str, suffix: str = "") -> tuple[str, bool]:
    """The name of an exported video, without ".mp4", and whether it is the clip's title.

    The title is used when there is a real one (written by AI or typed by you): title A for the A version, title B for
    the B version, "(explained)" added for the explainer. A clip whose title is only the placeholder written without AI,
    or that has none, keeps the old name: streamer_clip, with _A, _B or _explained.
    """
    key = VERSION_OF.get(suffix, "a")
    placeholder = set(state.get("copy_auto") or []) if state.get("copy_source") == "plain" else set()
    a, b = (("" if k in placeholder else safe_name(state.get(k))) for k in ("title", "title_b"))
    if key in ("b", "xb"):
        title = b if b and b.casefold() != a.casefold() else (f"{a} (B)" if a else "")
    else:
        title = a
    if title:
        return title + (" (explained)" if key.startswith("x") else ""), True
    channel = re.sub(r"[^0-9A-Za-z_-]", "", state.get("channel") or "clip") or "clip"
    return f"{channel}_{clip_id}{suffix}", False


def export_clip(clip_dir: Path, ready_root: Path, brief: Brief | None = None, video_name: str = "final.mp4",
                suffix: str = "", main: bool = True) -> tuple[Path, bool]:
    """Copy one approved clip and its post sheet to the ready folder. Returns (video path, passes brief).

    ``video_name``/``suffix`` export another version of the same clip (B version, explainer)
    beside the main one; ``main=False`` keeps that from replacing the main export's record.
    The file is named after the clip's title (see ``export_stem``). Where each version went is kept in the clip's
    ``exports``, so making it again replaces its own file instead of leaving a second copy under the old title.
    """
    import os

    state = read_json(clip_dir / "clip.json")
    final = clip_dir / video_name
    if not done(final):
        raise FileNotFoundError(f"clip {clip_dir.name} has no {video_name}; run render first")
    sheet_state = dict(state)
    if suffix.endswith("_B"):  # the B version is posted with its own title and hook
        sheet_state["title"] = state.get("title_b") or state.get("hook_b") or state.get("title")
        sheet_state["hook"] = state.get("hook_b") or state.get("hook")
    if video_name != "final.mp4":
        proc_len = _video_seconds(final)
        if proc_len:
            sheet_state["duration_s"] = proc_len
    day = ready_root / time.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    key = VERSION_OF.get(suffix, "a")
    exports = dict(state.get("exports") or {})
    before = Path(exports[key]) if exports.get(key) else None
    stem, titled = export_stem(state, clip_dir.name, suffix)
    video, n = day / f"{stem}.mp4", 2
    same = lambda a, b: b is not None and os.path.normcase(str(a)) == os.path.normcase(str(b))  # noqa: E731
    # Another clip has this name today: keep both. That also holds for clips without a title, which are named after
    # their channel and their place in their own video: two videos can each have a clip at the same second.
    while video.exists() and not same(video, before):
        video, n = day / f"{stem} ({n}).mp4", n + 1
    tmp = video.with_name(video.name + ".part")
    try:
        shutil.copyfile(final, tmp)
        tmp.replace(video)
    finally:
        tmp.unlink(missing_ok=True)  # only still there when the copy or the swap failed (the video is open elsewhere)
    if before is not None and not same(video, before) and same(before.parent, day):
        # The same version made earlier today under another title: one video, one name.
        for old in (before, before.with_suffix(".txt"), before.with_suffix(".jpg")):
            try:
                old.unlink(missing_ok=True)
            except OSError as e:
                log.warning("the older copy %s could not be removed (%s); it is still in the ready folder", old.name, e)
    exports[key] = str(video)
    sheet, issues = post_sheet(sheet_state, video.name, brief)
    if suffix.endswith(("_A", "_B")):
        sheet += (
            "\n=== A/B TEST ===\n"
            f"This is version {suffix[-1]}. A and B are the same moment with different opening text and titles.\n"
            "Post them on different accounts or platforms and compare views after 24 hours.\n"
            "Never submit both to the same campaign: campaigns reject duplicates.\n"
        )
    write_text(video.with_suffix(".txt"), sheet)
    ok = passes(issues)
    from .work import update_clip

    if not main:
        update_clip(clip_dir / "clip.json", {"exports": exports})
        return video, ok
    write_text(clip_dir / "post.txt", sheet)
    update_clip(clip_dir / "clip.json", {
        "exports": exports, "exported": str(video), "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"), "brief": brief.name if brief else None, "brief_ok": ok if brief else None,
        "brief_issues": [{"level": i.level, "message": i.message} for i in issues] if brief else []})
    return video, ok
