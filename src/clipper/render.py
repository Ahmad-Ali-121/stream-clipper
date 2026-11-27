"""Render a fetched clip source into the final vertical video with FFmpeg.

One FFmpeg run per clip: cut, reframe (centre crop or facecam-over-gameplay
split), burn in captions and overlays, normalise loudness, encode.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import replace
from pathlib import Path

from .captions import Overlay, build_ass, words_from_json
from .config import Config, RenderSettings
from .tools import ToolError, find_tool, run
from .work import done, read_json, save_clip, write_json, write_text, update_clip

log = logging.getLogger(__name__)


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


LAYOUTS = {
    "crop": "Video only, full height",
    "split": "Face on top, video below",
    "split_under": "Video on top, face below",
    "blur": "Whole picture on a blurred background",
    "pip": "Video full height, face in a corner",
    "circle": "Video full height, face in a circle",
}
FACE_CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
PIP_SHAPE = 4 / 3  # width / height of the face box in the corner layout


def _box_px(box, src_w: int, src_h: int, name: str) -> tuple[int, int, int, int]:
    """A box given as fractions (x, y, width, height) in pixels of the source, kept inside the picture."""
    if len(box) != 4 or not all(0 <= float(v) <= 1 for v in box):
        raise ValueError(f"render.{name} must be four fractions between 0 and 1: x, y, width, height")
    x, y = int(box[0] * src_w), int(box[1] * src_h)
    return x, y, max(2, min(_even(box[2] * src_w), src_w - x)), max(2, min(_even(box[3] * src_h), src_h - y))


def layout_shapes(r: RenderSettings, src_w: int = 16, src_h: int = 9) -> dict[str, dict[str, float]]:
    """The shape (width / height) each box is shown in, per layout. A box of that shape is shown whole, nothing cut off."""
    W, H = r.width, r.height
    top_h = _even(H * r.split_top)
    split = {"face": W / top_h, "video": W / (H - top_h)}
    return {"crop": {"video": W / H}, "split": split, "split_under": split, "blur": {"video": src_w / src_h},
            "pip": {"face": PIP_SHAPE, "video": W / H}, "circle": {"face": 1.0, "video": W / H}}


def layout_rects(src_w: int, src_h: int, r: RenderSettings, layout: str) -> tuple[tuple | None, tuple]:
    """(face box or None, video box) cut out of the source, in pixels: x, y, width, height.

    The video box is the one set by hand (``r.game``) when there is one, else a full-height slice placed by ``crop_x``
    (the whole picture for the blurred layout).
    """
    W, H = r.width, r.height
    if layout not in LAYOUTS:
        raise ValueError(f"layout must be one of {', '.join(LAYOUTS)}, got {layout!r}")
    if layout == "blur":
        return None, (_box_px(r.game, src_w, src_h, "game") if r.game else (0, 0, src_w, src_h))
    face = None if layout == "crop" else _box_px(r.facecam, src_w, src_h, "facecam")
    if r.game:
        return face, _box_px(r.game, src_w, src_h, "game")
    part_h = H - _even(H * r.split_top) if layout in ("split", "split_under") else H
    cw = min(src_w, _even(src_h * W / part_h))
    cx = int(min(max(r.crop_x * src_w - cw / 2, 0), src_w - cw))
    if layout in ("split", "split_under"):
        fx, _fy, fw, _fh = face
        # Keep the facecam out of the gameplay half when there is room to slide past it,
        # so the face is not shown twice.
        if fx + fw <= src_w - cw:  # facecam on the left
            cx = max(cx, fx + fw)
        elif fx >= cw:  # facecam on the right
            cx = min(cx, fx - cw)
    return face, (cx, 0, cw, src_h)


def _cover(w: int, h: int) -> str:
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"


def face_place(r: RenderSettings, fw: int, fh: int) -> tuple[int, int]:
    """Where the small face goes in the corner and circle layouts: clear of the opening text at the top and of the
    captions and the credit line at the bottom."""
    W, H = r.width, r.height
    corner = r.face_corner if r.face_corner in FACE_CORNERS else "top-right"
    x = 36 if corner.endswith("left") else W - fw - 36
    y = 360 if corner.startswith("top") else H - fh - 150
    return x, y


def layout_filter(src_w: int, src_h: int, r: RenderSettings, layout: str) -> str:
    """FFmpeg filter chain from input label [0:v] to output label [v0]."""
    W, H = r.width, r.height
    face, (gx, gy, gw, gh) = layout_rects(src_w, src_h, r, layout)
    if layout == "crop":
        return f"[0:v]crop={gw}:{gh}:{gx}:{gy},{_cover(W, H)}[v0]"
    if layout == "blur":
        fit_w, fit_h = W, _even(W * gh / gw)
        if fit_h > H:  # taller than the screen: fit the height instead and leave blur at the sides
            fit_w, fit_h = _even(H * gw / gh), H
        return (
            "[0:v]split=2[bg0][fg0];"
            f"[bg0]{_cover(W, H)},boxblur=24:3,eq=brightness=-0.10[bg];"
            f"[fg0]crop={gw}:{gh}:{gx}:{gy},scale={fit_w}:{fit_h}[fg];"
            f"[bg][fg]overlay={(W - fit_w) // 2}:{(H - fit_h) // 2}[v0]"
        )
    fx, fy, fw, fh = face
    if layout in ("split", "split_under"):
        top_h = _even(H * r.split_top)
        bot_h = H - top_h
        order = "[top][bot]" if layout == "split" else "[bot][top]"
        return (
            "[0:v]split=2[fa][ga];"
            f"[fa]crop={fw}:{fh}:{fx}:{fy},{_cover(W, top_h)}[top];"
            f"[ga]crop={gw}:{gh}:{gx}:{gy},{_cover(W, bot_h)}[bot];"
            f"{order}vstack=inputs=2[v0]"
        )
    # A small face over the full-height video: a framed box in a corner, or a circle.
    size = min(0.6, max(0.18, float(r.face_size)))
    out_w = _even(W * size)
    if layout == "pip":
        out_h = _even(out_w / PIP_SHAPE)
        small = f"crop={fw}:{fh}:{fx}:{fy},{_cover(out_w - 12, out_h - 12)},pad={out_w}:{out_h}:6:6:white"
    else:
        out_h = out_w
        small = (f"crop={fw}:{fh}:{fx}:{fy},{_cover(out_w, out_h)},format=yuva420p,"
                 "geq=lum='p(X,Y)':cb='p(X,Y)':cr='p(X,Y)':a='if(lte(hypot(X-W/2,Y-H/2),W/2-2),255,0)'")
    px, py = face_place(r, out_w, out_h)
    return (
        "[0:v]split=2[fa][ga];"
        f"[ga]crop={gw}:{gh}:{gx}:{gy},{_cover(W, H)}[bg];"
        f"[fa]{small}[fc];"
        f"[bg][fc]overlay={px}:{py}[v0]"
    )


def _shown_part(box: tuple, aspect: float) -> list[float]:
    """The middle of a pixel box that is really shown once it is scaled to fill a frame of this width/height ratio."""
    x, y, w, h = box
    if w / h > aspect:
        nw = h * aspect
        x, w = x + (w - nw) / 2, nw
    else:
        nh = w / aspect
        y, h = y + (h - nh) / 2, nh
    return [x, y, w, h]


def framing(state: dict, cfg: Config) -> dict | None:
    """Where the face box and the video box sit on this clip's source right now, for the box editor on the review page.

    Boxes are fractions of the source picture and already have the shape they are shown in, so what is inside a box
    is exactly what the viewer sees. Every layout is listed, so the editor can switch between them.
    None when the clip is not downloaded yet.
    """
    sw, sh = int(state.get("source_width") or 0), int(state.get("source_height") or 0)
    if not sw or not sh:
        return None
    r, layout = _layout_for(state, cfg)
    if layout not in LAYOUTS:
        layout = "crop"
    shapes = layout_shapes(r, sw, sh)

    def frac(box, aspect):
        x, y, w, h = _shown_part(box, aspect)
        return [round(x / sw, 4), round(y / sh, 4), round(w / sw, 4), round(h / sh, 4)]

    out = {"layout": layout, "source": [sw, sh], "shapes": shapes, "names": LAYOUTS, "corners": list(FACE_CORNERS),
           "face_corner": r.face_corner if r.face_corner in FACE_CORNERS else "top-right", "face_size": round(float(r.face_size), 2),
           "out": [r.width, r.height], "split_top": r.split_top,
           "custom": bool(state.get("game")) or not state.get("layout_auto", True), "boxes": {}}
    for name in LAYOUTS:
        own = r if name == layout else replace(r, game=[])  # a video box drawn for one layout has that layout's shape only
        try:
            face, video = layout_rects(sw, sh, own, name)
        except ValueError:
            face, video = layout_rects(sw, sh, replace(own, game=[], facecam=[0.0, 0.0, 0.25, 0.25]), name)
        out["boxes"][name] = {"video": frac(video, shapes[name]["video"])}
        if face:
            out["boxes"][name]["face"] = frac(face, shapes[name]["face"])
    return out


def cut_window(state: dict) -> tuple[float, float]:
    """(offset into source.mp4, duration) of the final clip.

    ``cut_start_s`` / ``cut_end_s`` in clip.json (stream time) override the
    moment's own window, so a clip can be tightened at review.
    """
    fetch_start = float(state.get("fetch_start_s", 0.0))
    start = float(state.get("cut_start_s", state["start_s"]))
    end = float(state.get("cut_end_s", state["end_s"]))
    offset = max(0.0, start - fetch_start)
    duration = end - start
    available = float(state.get("source_duration_s") or 0.0)
    if available:
        duration = min(duration, available - offset)
    if duration <= 0:
        raise ValueError(f"clip {state.get('id')}: cut window is empty (start {start}, end {end})")
    return offset, duration


def _measure_loudness(ffmpeg: str, src: str, offset: float, duration: float, target: float, cwd: Path) -> dict | None:
    proc = run(
        [ffmpeg, "-hide_banner", "-nostats", "-ss", f"{offset:.3f}", "-t", f"{duration:.3f}", "-i", src, "-vn",
         "-af", f"loudnorm=I={target}:TP=-1.0:LRA=11:print_format=json", "-f", "null", "-"],
        timeout_s=300, what="loudness measurement", cwd=cwd,
    )
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr or "", re.DOTALL)
    if not m:
        return None
    keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    try:
        data = json.loads(m.group(0))
        if not all(k in data for k in keys):
            return None
        import math

        # A silent clip measures as "-inf", which the second pass refuses and the whole render fails on.
        # Without usable figures the render uses the one-pass form, which copes with silence.
        return data if all(math.isfinite(float(data[k])) for k in keys) else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def credit_text(state: dict) -> str:
    channel = state.get("channel") or ""
    if not channel:
        return ""
    if state.get("platform") == "youtube":
        handle = str(state.get("channel_handle") or "")
        # A YouTube address needs the channel's handle; its display name ("Live Insaan") is not one.
        return f"Clip: youtube.com/{handle}" if handle.startswith("@") else f"Clip: {channel} on YouTube"
    if state.get("platform") == "kick" and state.get("channel_handle"):
        return f"Clip: kick.com/{state['channel_handle']}"
    site = {"twitch": "twitch.tv/"}.get(state.get("platform", ""), "")
    return f"Clip: {site}{channel}" if site else f"Clip: {channel}"


def _layout_for(state: dict, cfg: Config):
    """Render settings and layout name for one clip.

    Precedence: this clip's own setting, then the streamer's profile, then the default.
    """
    r = cfg.render
    profile = cfg.channels.get(str(state.get("channel", "")).lower(), {}) or {}
    layout = state.get("layout") or profile.get("layout") or r.layout
    facecam = state.get("facecam") or profile.get("facecam")
    if facecam:
        r = replace(r, facecam=list(facecam))
    game = state.get("game") or profile.get("game")  # the video box, when one was set by hand
    if game:
        r = replace(r, game=list(game))
    for key in ("face_corner", "face_size"):  # where and how big the small face is, in the corner and circle layouts
        if state.get(key) or profile.get(key):
            r = replace(r, **{key: state.get(key) or profile.get(key)})
    if state.get("crop_x") is not None:  # per-clip horizontal position of the crop
        r = replace(r, crop_x=float(state["crop_x"]))
    return r, layout


def clip_frames(clip_dir: Path, cfg: Config, count: int = 3, width: int = 640) -> list[Path]:
    """A few small frames of the clip as it was streamed (start, middle, end of the cut), for AI to look at."""
    state = read_json(clip_dir / "clip.json")
    if not done(clip_dir / "source.mp4"):
        return []
    offset, duration = cut_window(state)
    out: list[Path] = []
    for n in range(count):
        at = offset + duration * (0.15 + 0.70 * n / max(1, count - 1))
        name = f"look{n + 1}.jpg"
        run([find_tool("ffmpeg", cfg.data_dir), "-y", "-hide_banner", "-v", "error", "-ss", f"{at:.2f}", "-i", "source.mp4",
             "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "5", name], timeout_s=60, what=f"frame of clip {clip_dir.name}", cwd=clip_dir)
        if done(clip_dir / name):
            out.append(clip_dir / name)
    return out


def still_frame(clip_dir: Path, cfg: Config, at_s: float = 1.0, out_name: str = "still.png") -> Path:
    """One frame of the clip in its vertical layout with no text on it (for narration cards)."""
    state = read_json(clip_dir / "clip.json")
    if not done(clip_dir / "source.mp4"):
        raise ToolError(f"clip {clip_dir.name}: source.mp4 is missing; run fetch first")
    r, layout = _layout_for(state, cfg)
    offset, duration = cut_window(state)
    graph = layout_filter(int(state["source_width"]), int(state["source_height"]), r, layout)
    part = f"{Path(out_name).stem}.part.png"
    run(
        [find_tool("ffmpeg", cfg.data_dir), "-y", "-hide_banner", "-v", "error", "-ss", f"{offset + min(at_s, duration / 2):.3f}",
         "-i", "source.mp4", "-filter_complex", graph, "-map", "[v0]", "-frames:v", "1", part],
        timeout_s=120, what=f"still of clip {clip_dir.name}", cwd=clip_dir,
    )
    os.replace(clip_dir / part, clip_dir / out_name)
    return clip_dir / out_name


def opening_filters(r: RenderSettings) -> tuple[str, str]:
    """(video filter, audio graph) for an eye-catching start.

    Video: the picture starts 14% zoomed in and settles to normal in half a second.
    Audio: a soft whoosh under the first half second, mixed with the clip's own sound
    (expects the clip audio in [a0] and leaves the mix in [a]).
    """
    video = (
        f"zoompan=z='1+0.14*pow(1-min(in_time/0.5\\,1)\\,2)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={r.width}x{r.height}:fps={r.fps}"
    )
    audio = (
        "anoisesrc=d=0.55:c=pink:a=0.6,highpass=f=400,lowpass=f=6000,afade=t=in:d=0.25,afade=t=out:st=0.25:d=0.3,"
        "volume=0.4,aresample=48000[sw];[a0][sw]amix=inputs=2:normalize=0:duration=first[a]"
    )
    return video, audio


_render_locks: dict[str, "threading.Lock"] = {}
_render_locks_guard = __import__("threading").Lock()


def render_clip(clip_dir: Path, cfg: Config, force: bool = False, out_name: str = "final.mp4", hook: bool = True, variant: str = "a") -> Path:
    """Render one clip. Two renders of the same clip in this program (boxes saved twice, a B preview during Make videos)
    wait for each other: they write the same working files."""
    import threading

    with _render_locks_guard:
        lock = _render_locks.setdefault(str(clip_dir.resolve()), threading.Lock())
    with lock:
        # A render takes a while and reads the clip's text and cut at its start. If the person changed them meanwhile,
        # the file just made shows the old ones while the card shows the new: it is made once more, so what is
        # exported is what the card says.
        before = _edit_mark(clip_dir)
        out = _render_clip(clip_dir, cfg, force, out_name, hook, variant)
        if _edit_mark(clip_dir) != before:
            log.info("clip %s was edited while its video was being made; making it again with the change", clip_dir.name)
            out = _render_clip(clip_dir, cfg, True, out_name, hook, variant)
        return out


def _edit_mark(clip_dir: Path) -> tuple:
    """What a person can change on a clip's card that is seen in its video: the opening texts, the cut, the layout, the words."""
    state = read_json(clip_dir / "clip.json", {})
    try:
        words = (clip_dir / "words.json").stat().st_mtime_ns
    except OSError:
        words = 0
    return tuple(json.dumps(state.get(k), sort_keys=True) for k in ("hook", "hook_b", "cut_start_s", "cut_end_s", "layout", "facecam", "game", "face_corner", "face_size", "crop_x", "disclosure")) + (words,)


def _file_stamp(path: str) -> tuple:
    """A file by what it holds, not only by its name: another image saved under the same name is another logo."""
    try:
        st = Path(path).stat()
        return (str(path), st.st_size, int(st.st_mtime))
    except OSError:
        return (str(path), 0, 0)


def look_signature(cfg: Config) -> str:
    """Changes when something that can be SEEN or HEARD in every clip changes: the caption look, the template, the branding.

    "Make videos" renders a clip again when its draft was made with another look, so this must not change for
    things that make no difference: the name a caption look is saved under, 3 written as 3.0, the corner of a
    logo that is switched off, the colours of a card that is not used.
    """
    import hashlib
    from dataclasses import asdict

    def num(v):
        return round(float(v), 3)

    c = asdict(cfg.captions)
    for hidden in ("script", "preset"):  # the letters are chosen at transcription; the name is only a name
        c.pop(hidden, None)
    c = {k: (num(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in c.items()}
    b, r = cfg.brand, cfg.render
    brand = (
        (_file_stamp(b.logo), b.logo_corner, num(b.logo_size), num(b.logo_opacity)) if b.logo else None,
        (b.outro_text.strip(), num(b.outro_seconds)) if b.outro_text.strip() else None,
        (_file_stamp(b.music), num(b.music_volume), num(b.music_rise_s), num(b.music_peak) if float(b.music_rise_s or 0) >= 1 else 0) if b.music else None,
    )
    template = r.template if r.template else "none"
    frame = [num(r.hook_seconds), template]
    if template == "card":
        frame += [r.card_bg, r.card_color if r.card_bg in ("color", "gradient") else "", r.card_color2 if r.card_bg == "gradient" else "",
                  _file_stamp(r.card_image) if r.card_bg == "image" else ""]
    if template.startswith("xpost"):
        frame += [r.post_name, r.post_handle]
    return "v2-" + hashlib.sha256(repr((sorted(c.items()), brand, frame)).encode()).hexdigest()[:12]


def look_signature_v1(cfg: Config) -> str:
    """The first way the look was recorded (in ``look_used``). Kept only to judge drafts made before ``look2_used`` existed."""
    import hashlib
    from dataclasses import asdict

    c = asdict(cfg.captions)
    c.pop("script", None)  # which letters captions use is decided when a clip is transcribed, not when it is rendered
    r = cfg.render
    frame = tuple(getattr(r, k, None) for k in ("hook_seconds", "template", "card_bg", "card_color", "card_color2", "card_image", "post_name", "post_handle"))
    return hashlib.sha256(repr((sorted(c.items()), sorted(asdict(cfg.brand).items()), frame)).encode()).hexdigest()[:12]


def _corner_xy(corner: str, top: int, bottom: int, side: int = 40) -> tuple[str, str]:
    """FFmpeg overlay position for something placed in a corner of the picture."""
    x = str(side) if corner.endswith("left") else f"main_w-overlay_w-{side}"
    y = str(top) if corner.startswith("top") else f"main_h-overlay_h-{bottom}"
    return x, y


PREVIEW_SECONDS = 14.0  # the preview treats the clip as this short, so its last seconds (the outro line) come quickly
PREVIEW_HOOK = "YOUR OPENING TEXT"


class ClipBusy(ToolError):
    """The clip's video is being made right now, so nothing else can be made from it this moment."""


def clip_render_lock(clip_dir: Path):
    """The lock every render of one clip takes (in this program). Whoever replaces the clip's video takes it too."""
    import threading

    with _render_locks_guard:
        return _render_locks.setdefault(str(clip_dir.resolve()), threading.Lock())


def preview_seconds(cfg: Config) -> float:
    """How long the preview pretends the clip is: long enough for the closing text to appear (it needs four seconds before it)."""
    return max(PREVIEW_SECONDS, float(cfg.brand.outro_seconds) + 6.0)


def preview_stills(clip_dir: Path, cfg: Config, out_dir: Path, wait: bool = True) -> dict[str, Path]:
    """Three frames of one clip exactly as its video would look with the settings as they are now: {"open", "mid", "end"}.

    Made by the renderer itself (same layout, captions, template, logo, opening effects), only without sound and
    without writing a video, so it takes a few seconds instead of a minute. Nothing of the clip is changed."""
    import threading

    out_dir.mkdir(parents=True, exist_ok=True)
    lock = clip_render_lock(clip_dir)
    if not lock.acquire(blocking=wait):  # its video is being made: that takes minutes, and a preview must not wait for it
        raise ClipBusy(f"clip {clip_dir.name} is being made right now")
    try:
        return _render_clip(clip_dir, cfg, True, still={"out": out_dir})
    finally:
        lock.release()
        (clip_dir / "preview.ass").unlink(missing_ok=True)  # also when it failed before the frames were taken


def _render_clip(clip_dir: Path, cfg: Config, force: bool = False, out_name: str = "final.mp4", hook: bool = True, variant: str = "a",
                 still: dict | None = None):
    """Render one clip. ``out_name``/``hook`` let other formats (top-moments videos)
    render a variant, e.g. the clip body without the hook text, beside the normal final.mp4.

    ``variant`` "b" is the second version for A/B testing: it shows ``hook_b`` (when the
    clip has one) on a box in the accent colour instead of ``hook`` on a white box."""
    r = cfg.render
    state = read_json(clip_dir / "clip.json")
    out = clip_dir / out_name
    stem = "preview" if still else Path(out_name).stem
    if done(out) and not force and not still:
        return out
    if not done(clip_dir / "source.mp4"):
        raise ToolError(f"clip {clip_dir.name}: source.mp4 is missing; run fetch first")
    ffmpeg = find_tool("ffmpeg", cfg.data_dir)
    offset, duration = cut_window(state)
    if still:
        duration = min(duration, preview_seconds(cfg))
    r, layout = _layout_for(state, cfg)

    words = words_from_json(read_json(clip_dir / "words.json", []), offset_s=offset)
    words = [w for w in words if w.start < duration]
    overlays: list[Overlay] = []
    hook_text = (state.get("hook_b") or state.get("hook")) if variant == "b" else state.get("hook")
    if still and not hook_text:
        hook_text = PREVIEW_HOOK  # a clip with no opening text yet still shows where it goes
    if hook and hook_text:
        overlays.append(Overlay(hook_text, 0.0, min(r.hook_seconds, duration), "HookB" if variant == "b" else "Hook", pop=r.opening))
    credit = credit_text(state)
    if credit:
        overlays.append(Overlay(credit, 0.0, duration, "Credit"))
    if state.get("disclosure"):
        overlays.append(Overlay(r.disclosure_text, 0.0, min(r.disclosure_seconds, duration), "Disclosure"))
    part, parts = int(state.get("part") or 0), int(state.get("parts") or 0)
    if part and parts > 1 and state.get("part_label", True):
        # Viewers who land on part 2 go looking for part 1, and those on part 1 wait for the next: say which this is.
        overlays.append(Overlay(f"Part {part}", 0.0, duration, "Part"))
        if part < parts and duration > 8:
            overlays.append(Overlay(f"Part {part + 1} next", max(0.0, duration - 3.0), duration, "PartNext", pop=r.opening))
    brand = cfg.brand
    is_final = out_name in ("final.mp4", "final_b.mp4")  # the clip itself, not a piece used inside another video
    captions = cfg.captions
    # A small face in a bottom corner sits where captions and the outro line would be: captions go just above it, the outro to the top.
    face_low = layout in ("pip", "circle") and str(r.face_corner).startswith("bottom")
    if face_low:
        small_w = _even(r.width * min(0.6, max(0.18, float(r.face_size))))
        small_h = small_w if layout == "circle" else _even(small_w / PIP_SHAPE)
        captions = replace(captions, margin_v=max(captions.margin_v, 150 + small_h + 40))
    if is_final and brand.outro_text.strip() and duration > float(brand.outro_seconds) + 4:
        overlays.append(Overlay(brand.outro_text.strip(), duration - float(brand.outro_seconds), duration, "OutroTop" if face_low else "Outro", pop=True))
    top_h = _even(r.height * r.split_top)
    # In the split layout the hook sits on the seam between facecam and gameplay, not over the face.
    hook_margin = top_h - 60 if layout == "split" else 170
    # A template frames the finished picture (a card, a post, a news banner) and may show the opening text itself.
    from . import frames
    from .captions import script_font

    frame = frames.build(r if is_final else replace(r, template="none"), clip_dir, hook_text if hook else "", str(state.get("channel") or ""), duration,
                         script_font([hook_text or "", str(state.get("channel") or "")]) or captions.font, captions.active_color, first_input=1)
    if frame.shows_hook:
        overlays = [o for o in overlays if o.style not in ("Hook", "HookB")]
    if frame.caption_margin is not None:
        captions = replace(captions, margin_v=max(frame.caption_margin, captions.margin_v if face_low else 0))
    if frame.hook_margin is not None:
        hook_margin = frame.hook_margin
    write_text(
        clip_dir / f"{stem}.ass",
        build_ass(words, captions, overlays, hook_size=r.hook_font_size, credit_size=r.credit_font_size, hook_margin_v=hook_margin,
                  progress_s=duration if r.opening else 0.0, extra_styles=frame.styles, extra_events=frame.events),
    )
    zoom, whoosh = opening_filters(r)

    graph = layout_filter(int(state["source_width"]), int(state["source_height"]), r, layout)
    graph += f";[v0]fps={r.fps}{',' + zoom if r.opening else ''}[vz];{frame.graph};[t0]ass={stem}.ass[v1]"
    inputs = ["-ss", f"{offset:.3f}", "-t", f"{duration:.3f}", "-i", "source.mp4"] + frame.inputs
    extra = 1 + frame.inputs.count("-i")  # the number the next input file gets
    last = "[v1]"
    logo = state.get("logo")
    if logo:
        logo_path = Path(logo)
        if not logo_path.exists():
            raise ToolError(f"clip {clip_dir.name}: logo file not found: {logo}")
        # Animated logos loop for the whole clip; still images are held.
        inputs += (["-ignore_loop", "0"] if logo_path.suffix.lower() == ".gif" else ["-loop", "1"]) + ["-i", str(logo_path)]
        graph += f";[{extra}:v]scale={r.width}:{r.height}[lg];[v1][lg]overlay=0:0:shortest=1[v2]"
        last = "[v2]"
        extra += 1
    if brand.logo:
        mark = Path(brand.logo)
        if not mark.exists():
            raise ToolError(f"the logo file is missing: {brand.logo}. Choose it again under Branding, or take it off there")
        inputs += (["-ignore_loop", "0"] if mark.suffix.lower() == ".gif" else ["-loop", "1"]) + ["-i", str(mark)]
        width = _even(r.width * min(0.5, max(0.05, float(brand.logo_size))))
        corner = brand.logo_corner if brand.logo_corner in FACE_CORNERS else "top-left"
        # Top right is where the "Part 2" badge sits: the logo goes under it.
        x, y = _corner_xy(corner, 150 if corner == "top-right" and part and parts > 1 else 50, 190)
        graph += (f";[{extra}:v]scale={width}:-1,format=rgba,colorchannelmixer=aa={min(1.0, max(0.1, float(brand.logo_opacity))):.2f}[mark];"
                  f"{last}[mark]overlay={x}:{y}:shortest=1[v3]")
        last = "[v3]"
        extra += 1

    if still:
        # The picture is complete here; the rest of this function is sound and the video file. Three frames are taken
        # in one pass: early (opening text, zoom), the middle (captions), and the last second (the outro line).
        fps = int(r.fps)
        at = {"open": min(1.4, duration * 0.2), "mid": duration * 0.5, "end": max(0.0, duration - 1.0)}
        picks = sorted({max(0, int(t * fps)) for t in at.values()})
        if len(picks) < 3:
            raise ToolError(f"clip {clip_dir.name} is too short to preview")
        sel = "+".join(f"eq(n\\,{n})" for n in picks)
        try:
            run([ffmpeg, "-y", "-hide_banner", "-v", "error"] + inputs
                + ["-filter_complex", graph + f";{last}select='{sel}',scale=540:-2[pv]", "-map", "[pv]", "-an", "-fps_mode", "vfr", "-frames:v", "3",
                   "-q:v", "4", str(Path(still["out"]) / "frame_%d.jpg")],
                cwd=clip_dir, timeout_s=180, what=f"preview of clip {clip_dir.name}")
        finally:
            (clip_dir / f"{stem}.ass").unlink(missing_ok=True)
        made = {}
        for i, name in enumerate(sorted(at, key=at.get), 1):
            f = Path(still["out"]) / f"frame_{i}.jpg"
            if not done(f):
                raise ToolError(f"the preview of clip {clip_dir.name} produced no picture")
            target = Path(still["out"]) / f"{name}.jpg"
            os.replace(f, target)
            made[name] = target
        return made

    from .fetch import ensure_sound

    ensure_sound(clip_dir / "source.mp4", cfg)
    measured = _measure_loudness(ffmpeg, "source.mp4", offset, duration, r.loudness_lufs, clip_dir)
    loud = f"loudnorm=I={r.loudness_lufs}:TP=-1.0:LRA=11"
    if measured:
        loud += (
            f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}:linear=true"
        )
    graph += f";[0:a:0]{loud},aresample=48000[a0];" + (whoosh if r.opening else "[a0]anull[a]")
    sound = "[a]"
    if is_final and brand.music:
        music = Path(brand.music)
        if not music.exists():
            raise ToolError(f"the music file is missing: {brand.music}. Choose it again under Branding, or take it off there")
        inputs += ["-stream_loop", "-1", "-i", str(music)]  # repeated if it is shorter than the clip
        volume = min(0.5, max(0.0, float(brand.music_volume)))
        rise = min(float(brand.music_rise_s or 0), max(0.0, duration - 3.0))
        if rise >= 1:
            # A riser: level for most of the clip, then steadily louder up to the peak at the very end.
            peak, begin = min(0.8, max(volume, float(brand.music_peak))), duration - rise
            level = f"volume='if(lt(t,{begin:.2f}),{volume:.3f},{volume:.3f}+({peak:.3f}-{volume:.3f})*(t-{begin:.2f})/{rise:.2f})':eval=frame,afade=t=in:d=1.5,afade=t=out:st={max(0.0, duration - 0.35):.2f}:d=0.35"
        else:
            level = f"volume={volume:.3f},afade=t=in:d=1.5,afade=t=out:st={max(0.0, duration - 2.0):.2f}:d=2"
        graph += (f";[{extra}:a:0]{level},aresample=48000[music];"
                  "[a][music]amix=inputs=2:duration=first:normalize=0[amix]")
        sound = "[amix]"
        extra += 1
    from . import hw

    part = f"{stem}.part.mp4"
    cmd = lambda video: (  # noqa: E731
        [ffmpeg, "-y", "-hide_banner", "-v", "error", "-stats"] + inputs
        + ["-filter_complex", graph, "-map", last, "-map", sound]
        + video
        + ["-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(r.fps), "-g", str(r.fps * 2),
           "-c:a", "aac", "-b:a", r.audio_bitrate, "-ar", "48000", "-t", f"{duration:.3f}", "-movflags", "+faststart", part]
    )
    t0 = time.monotonic()
    from . import progress

    enc = hw.run_encode(cfg, r.crf, r.preset, cmd, part=clip_dir / part, timeout_s=1800, what=f"render of clip {clip_dir.name}", cwd=clip_dir,
                        on_line=progress.from_tool(duration))
    if not done(clip_dir / part):
        raise ToolError(f"render of clip {clip_dir.name} produced no file")
    os.replace(clip_dir / part, out)
    made = {"render_seconds": round(time.monotonic() - t0, 1), "duration_s": round(duration, 2), "width": r.width, "height": r.height,
            "layout_used": layout, "encoder_used": enc, "opening_used": bool(r.opening), "style_used": cfg.captions.preset, "look_used": look_signature_v1(cfg), "look2_used": look_signature(cfg), "caption_words": len(words), "has_disclosure_overlay": bool(state.get("disclosure"))}
    state.update(made)
    if out_name == "final.mp4":  # variants must not overwrite the main render's record
        # Only what this render produced is written: a render takes a while, and a person may have edited the clip meanwhile.
        # The main video is new, so a copy exported earlier is out of date: "Make videos" must export it again.
        update_clip(clip_dir / "clip.json", made, drop=("exported",))
    log.info("clip %s: rendered %.0fs %s layout in %.0fs", clip_dir.name, duration, layout, state["render_seconds"])
    return out
