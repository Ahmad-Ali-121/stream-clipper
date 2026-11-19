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
from .work import done, read_json, write_json, write_text

log = logging.getLogger(__name__)


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def layout_filter(src_w: int, src_h: int, r: RenderSettings, layout: str) -> str:
    """FFmpeg filter chain from input label [0:v] to output label [v0]."""
    W, H = r.width, r.height
    if layout == "crop":
        cw = min(src_w, _even(src_h * W / H))
        cx = int(min(max(r.crop_x * src_w - cw / 2, 0), src_w - cw))
        return f"[0:v]crop={cw}:{src_h}:{cx}:0,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[v0]"
    if layout == "split":
        if len(r.facecam) != 4 or not all(0 <= v <= 1 for v in r.facecam):
            raise ValueError("render.facecam must be four fractions between 0 and 1: x, y, width, height")
        top_h = _even(H * r.split_top)
        bot_h = H - top_h
        fx, fy = int(r.facecam[0] * src_w), int(r.facecam[1] * src_h)
        fw = min(_even(r.facecam[2] * src_w), src_w - fx)
        fh = min(_even(r.facecam[3] * src_h), src_h - fy)
        cw = min(src_w, _even(src_h * W / bot_h))
        cx = int(min(max(r.crop_x * src_w - cw / 2, 0), src_w - cw))
        # Keep the facecam out of the gameplay half when there is room to slide past it,
        # so the face is not shown twice.
        if fx + fw <= src_w - cw:  # facecam on the left
            cx = max(cx, fx + fw)
        elif fx >= cw:  # facecam on the right
            cx = min(cx, fx - cw)
        return (
            "[0:v]split=2[fa][ga];"
            f"[fa]crop={fw}:{fh}:{fx}:{fy},scale={W}:{top_h}:force_original_aspect_ratio=increase,crop={W}:{top_h}[top];"
            f"[ga]crop={cw}:{src_h}:{cx}:0,scale={W}:{bot_h}:force_original_aspect_ratio=increase,crop={W}:{bot_h}[bot];"
            "[top][bot]vstack=inputs=2[v0]"
        )
    raise ValueError(f"layout must be 'crop' or 'split', got {layout!r}")


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
    try:
        data = json.loads(m.group(0))
        return data if all(k in data for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")) else None
    except json.JSONDecodeError:
        return None


def credit_text(state: dict) -> str:
    channel = state.get("channel") or ""
    if not channel:
        return ""
    site = {"twitch": "twitch.tv/", "youtube": "youtube.com/@"}.get(state.get("platform", ""), "")
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


def render_clip(clip_dir: Path, cfg: Config, force: bool = False, out_name: str = "final.mp4", hook: bool = True, variant: str = "a") -> Path:
    """Render one clip. ``out_name``/``hook`` let other formats (top-moments videos)
    render a variant, e.g. the clip body without the hook text, beside the normal final.mp4.

    ``variant`` "b" is the second version for A/B testing: it shows ``hook_b`` (when the
    clip has one) on a box in the accent colour instead of ``hook`` on a white box."""
    r = cfg.render
    state = read_json(clip_dir / "clip.json")
    out = clip_dir / out_name
    stem = Path(out_name).stem
    if done(out) and not force:
        return out
    if not done(clip_dir / "source.mp4"):
        raise ToolError(f"clip {clip_dir.name}: source.mp4 is missing; run fetch first")
    ffmpeg = find_tool("ffmpeg", cfg.data_dir)
    offset, duration = cut_window(state)
    r, layout = _layout_for(state, cfg)

    words = words_from_json(read_json(clip_dir / "words.json", []), offset_s=offset)
    words = [w for w in words if w.start < duration]
    overlays: list[Overlay] = []
    hook_text = (state.get("hook_b") or state.get("hook")) if variant == "b" else state.get("hook")
    if hook and hook_text:
        overlays.append(Overlay(hook_text, 0.0, min(r.hook_seconds, duration), "HookB" if variant == "b" else "Hook", pop=r.opening))
    credit = credit_text(state)
    if credit:
        overlays.append(Overlay(credit, 0.0, duration, "Credit"))
    if state.get("disclosure"):
        overlays.append(Overlay(r.disclosure_text, 0.0, min(r.disclosure_seconds, duration), "Disclosure"))
    top_h = _even(r.height * r.split_top)
    # In the split layout the hook sits on the seam between facecam and gameplay, not over the face.
    hook_margin = top_h - 60 if layout == "split" else 170
    write_text(
        clip_dir / f"{stem}.ass",
        build_ass(words, cfg.captions, overlays, hook_size=r.hook_font_size, credit_size=r.credit_font_size, hook_margin_v=hook_margin,
                  progress_s=duration if r.opening else 0.0),
    )
    zoom, whoosh = opening_filters(r)

    graph = layout_filter(int(state["source_width"]), int(state["source_height"]), r, layout)
    graph += f";[v0]fps={r.fps},{zoom + ',' if r.opening else ''}ass={stem}.ass[v1]"
    inputs = ["-ss", f"{offset:.3f}", "-t", f"{duration:.3f}", "-i", "source.mp4"]
    last = "[v1]"
    logo = state.get("logo")
    if logo:
        logo_path = Path(logo)
        if not logo_path.exists():
            raise ToolError(f"clip {clip_dir.name}: logo file not found: {logo}")
        # Animated logos loop for the whole clip; still images are held.
        inputs += (["-ignore_loop", "0"] if logo_path.suffix.lower() == ".gif" else ["-loop", "1"]) + ["-i", str(logo_path)]
        graph += f";[1:v]scale={r.width}:{r.height}[lg];[v1][lg]overlay=0:0:shortest=1[v2]"
        last = "[v2]"

    measured = _measure_loudness(ffmpeg, "source.mp4", offset, duration, r.loudness_lufs, clip_dir)
    loud = f"loudnorm=I={r.loudness_lufs}:TP=-1.0:LRA=11"
    if measured:
        loud += (
            f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}:linear=true"
        )
    graph += f";[0:a:0]{loud},aresample=48000[a0];" + (whoosh if r.opening else "[a0]anull[a]")
    from . import hw

    enc = hw.encoder(cfg)
    video = hw.encoder_args(enc, r.crf, r.preset)
    part = f"{stem}.part.mp4"
    cmd = (
        [ffmpeg, "-y", "-hide_banner", "-v", "error"] + inputs
        + ["-filter_complex", graph, "-map", last, "-map", "[a]"]
        + video
        + ["-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(r.fps), "-g", str(r.fps * 2),
           "-c:a", "aac", "-b:a", r.audio_bitrate, "-ar", "48000", "-t", f"{duration:.3f}", "-movflags", "+faststart", part]
    )
    t0 = time.monotonic()
    run(cmd, timeout_s=1800, what=f"render of clip {clip_dir.name}", cwd=clip_dir)
    if not done(clip_dir / part):
        raise ToolError(f"render of clip {clip_dir.name} produced no file")
    os.replace(clip_dir / part, out)
    state.update({"render_seconds": round(time.monotonic() - t0, 1), "duration_s": round(duration, 2), "width": r.width, "height": r.height,
                  "layout_used": layout, "encoder_used": enc, "opening_used": bool(r.opening), "style_used": cfg.captions.preset, "caption_words": len(words), "has_disclosure_overlay": bool(state.get("disclosure"))})
    if out_name == "final.mp4":  # variants must not overwrite the main render's record
        write_json(clip_dir / "clip.json", state)
    log.info("clip %s: rendered %.0fs %s layout in %.0fs", clip_dir.name, duration, layout, state["render_seconds"])
    return out
