"""Commentary formats built from finished clips.

explained   narrator sets the clip up over a blurred still, then the clip plays.
top         "top N moments": for each clip, counted down to #1, a rank card with a
            spoken line, then the clip.

Both add something of the maker's own (selection, order, context, a voice) on
top of the raw clip, which is what YouTube's reused-content rule asks for.
Whether a given channel passes that review is YouTube's call; this only makes
the format possible.

A narrated card shows a still of the clip it introduces with motion graphics
(see ``make_card``), the narration as timed captions and, for "top", a rank number.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import time
from dataclasses import replace
from pathlib import Path

from . import hw
from .captions import Overlay, Word, build_ass
from .config import Config
from .render import render_clip, still_frame
from .tools import ToolError, find_tool, run
from .tts import speak
from .work import done, read_json, write_json, write_text

log = logging.getLogger(__name__)


def spread_words(text: str, duration: float) -> list[Word]:
    """Approximate word timings for narration: time is shared out by word length.

    Only the fallback: normally ``narration_words`` times the captions from the
    audio itself. This never drifts past the end of the audio.
    """
    words = text.split()
    if not words or duration <= 0:
        return []
    weights = [len(re.sub(r"\W", "", w)) + 2 for w in words]  # +2: every word takes some time, however short
    total = float(sum(weights))
    out, t = [], 0.0
    for w, weight in zip(words, weights):
        d = duration * weight / total
        out.append(Word(round(t, 3), round(t + d, 3), w))
        t += d
    return out


def _norm(word: str) -> str:
    return re.sub(r"\W", "", word.lower())


def align_words(text: str, heard: list[dict], duration: float) -> list[Word]:
    """Caption timings for narration, taken from where each word is really spoken.

    ``heard`` is the speech recogniser's word list for the narration audio
    ({"start", "end", "word"}). The words shown are always the ones in ``text``;
    only the times come from ``heard``. Words the recogniser missed or heard
    differently share the gap between their matched neighbours. If too little
    matches, fall back to sharing the time out by word length.
    """
    words = text.split()
    if not words or duration <= 0:
        return []
    ours, theirs = [_norm(w) for w in words], [_norm(h["word"]) for h in heard]
    times: list[tuple[float, float] | None] = [None] * len(words)
    for block in difflib.SequenceMatcher(None, ours, theirs, autojunk=False).get_matching_blocks():
        for k in range(block.size):
            if ours[block.a + k]:
                h = heard[block.b + k]
                times[block.a + k] = (float(h["start"]), float(h["end"]))
    if sum(t is not None for t in times) * 2 < len(words):
        return spread_words(text, duration)
    out: list[Word] = []
    i = 0
    while i < len(words):
        if times[i] is not None:
            out.append(Word(times[i][0], times[i][1], words[i]))
            i += 1
            continue
        j = i
        while j < len(words) and times[j] is None:
            j += 1
        left = out[-1].end if out else min(float(heard[0]["start"]), times[j][0])
        right = times[j][0] if j < len(words) else max(min(float(heard[-1]["end"]), duration), left)
        for w in spread_words(" ".join(words[i:j]), max(right - left, 0.05 * (j - i))):
            out.append(Word(left + w.start, left + w.end, w.text))
        i = j
    fixed, t = [], 0.0
    for w in out:  # keep the words in order and inside the audio
        start = min(max(w.start, t), duration)
        end = min(max(w.end, start + 0.05), max(duration, start + 0.05))
        fixed.append(Word(round(start, 3), round(end, 3), w.text))
        t = end
    return fixed


def narration_words(text: str, voice: Path, duration: float, cfg: Config) -> list[Word]:
    """Word timings for a narration file: listen to it; if that fails, estimate."""
    from .transcribe import transcribe_file

    try:
        return align_words(text, transcribe_file(voice, cfg), duration)
    except Exception as e:  # captions slightly off beat a failed video
        log.warning("could not time the narration from its audio (%s); estimating instead", e)
        return spread_words(text, duration)


def audio_duration(path: Path, cfg: Config) -> float:
    proc = run([find_tool("ffprobe", cfg.data_dir), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
               timeout_s=60, what="ffprobe")
    return float(json.loads(proc.stdout)["format"]["duration"])


# ---- narrated cards with motion graphics (FFmpeg and libass only, nothing paid) ----
#
# A card is laid out on a 1080x1920 canvas (the ASS script resolution; libass
# scales it to the real output size):
#   background  the clip's own still, blurred and darkened, slowly zooming
#   picture     the same still, sharp, in a white frame; slides up at the start and keeps zooming
#   top         a label that fades in over an accent line, or a rank number that pops in
#   bar         a progress bar under the picture that fills over the length of the card
#   captions    the narration, word by word, timed from the voice
#   sound       a short whoosh as the picture slides in (and a low thump under a rank number)

CARD_LEAD_S = 0.35  # the picture slides in before the voice starts
CARD_PIC = (154, 310, 772, 962)  # x, y, width, height of the framed picture on the 1080x1920 canvas
CARD_BAR_Y = 1296
CARD_CAPTION_MARGIN_V = 450  # captions sit just under the bar


def card_graphics(duration: float, accent_bgr: str, label: str = "", big: str = "") -> tuple[list[str], list[str]]:
    """ASS styles and events for a card's label or rank number and its progress bar. Pure text."""
    from .captions import ass_time, clean_text

    x, _y, w, _h = CARD_PIC
    end = ass_time(duration)
    ms = int(duration * 1000)
    bar = f"m 0 0 l {w} 0 {w} 12 0 12"
    styles = [
        "Style: Label,Arial Black,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,3,0,1,5,2,5,0,0,0,1",
        f"Style: Rank,Arial Black,190,&H00{accent_bgr},&H00{accent_bgr},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,9,4,5,0,0,0,1",
    ]
    events = [
        f"Dialogue: 2,0:00:00.00,{end},Shape,,0,0,0,,{{\\an7\\pos({x},{CARD_BAR_Y})\\1c&H404040&\\1a&H40&\\p1}}{bar}{{\\p0}}",
        f"Dialogue: 3,0:00:00.00,{end},Shape,,0,0,0,,{{\\an7\\pos({x},{CARD_BAR_Y})\\1c&H{accent_bgr}&\\fscx0\\t(0,{ms},\\fscx100)\\p1}}{bar}{{\\p0}}",
    ]
    if big:
        events.append(
            f"Dialogue: 4,0:00:00.20,{end},Rank,,0,0,0,,{{\\an5\\pos(540,190)\\fscx10\\fscy10\\t(0,180,\\fscx125\\fscy125)\\t(180,320,\\fscx100\\fscy100)}}{clean_text(big)}"
        )
    elif label:
        events.append(f"Dialogue: 4,0:00:00.10,{end},Label,,0,0,0,,{{\\an5\\pos(540,190)\\fad(250,0)}}{clean_text(label).upper()}")
        events.append(
            f"Dialogue: 4,0:00:00.10,{end},Shape,,0,0,0,,{{\\an7\\pos(390,242)\\1c&H{accent_bgr}&\\fscx0\\t(0,400,\\fscx100)\\p1}}m 0 0 l 300 0 300 10 0 10{{\\p0}}"
        )
    return styles, events


def card_filter(width: int, height: int, fps: int, frames: int, ass_name: str, variant: str = "a", accent: str = "FFE600") -> str:
    """FFmpeg video filter graph for a card: zooming blurred background + framed picture that slides in + ASS.

    Variant "a": white frame, picture slides up, everything zooms in.
    Variant "b": frame in the accent colour, picture slides in from the left, everything zooms out.
    """
    k = width / 1080
    px, py, pw, ph = (int(round(v * k / 2)) * 2 for v in CARD_PIC)
    border = max(int(round(6 * k)), 2)
    iw, ih = pw - 2 * border, ph - 2 * border
    centre = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    if variant == "b":
        ease = "pow(1-min(t/0.45\\,1)\\,3)"
        return (
            f"[0:v]trim=end_frame=1,scale={width}:{height},setsar=1,split=2[s1][s2];"
            f"[s1]scale={width * 2}:{height * 2},zoompan=z='1.12-0.12*on/{frames}':{centre}:d={frames}:s={width}x{height}:fps={fps},"
            f"boxblur=18:4,eq=brightness=-0.26:saturation=0.85[bg];"
            f"[s2]crop=iw:iw*{ih}/{iw}:0:(ih-iw*{ih}/{iw})/2,scale={iw * 2}:{ih * 2},"
            f"zoompan=z='1.07-0.07*on/{frames}':{centre}:d={frames}:s={iw}x{ih}:fps={fps},"
            f"pad={pw}:{ph}:{border}:{border}:0x{accent}[pic];"
            f"[bg][pic]overlay=x='{px}-{int(1000 * k)}*{ease}':y={py},format=yuv420p,ass={ass_name}[v]"
        )
    return (
        f"[0:v]trim=end_frame=1,scale={width}:{height},setsar=1,split=2[s1][s2];"
        f"[s1]scale={width * 2}:{height * 2},zoompan=z='1+0.12*on/{frames}':{centre}:d={frames}:s={width}x{height}:fps={fps},"
        f"boxblur=18:4,eq=brightness=-0.26:saturation=0.85[bg];"
        f"[s2]crop=iw:iw*{ih}/{iw}:0:(ih-iw*{ih}/{iw})/2,scale={iw * 2}:{ih * 2},"
        f"zoompan=z='1+0.07*on/{frames}':{centre}:d={frames}:s={iw}x{ih}:fps={fps},"
        f"pad={pw}:{ph}:{border}:{border}:white[pic];"
        f"[bg][pic]overlay=x={px}:y='{py}+{int(900 * k)}*pow(1-min(t/0.45\\,1)\\,3)',format=yuv420p,ass={ass_name}[v]"
    )


def make_card(still_from: Path, text: str, out: Path, cfg: Config, big: str = "", label: str = "", tail_s: float = 0.35,
              variant: str = "a") -> Path:
    """A narrated card: the still image ``still_from`` with motion graphics + spoken ``text`` as captions.

    ``big`` is a rank such as "#3" that pops in at the top; ``label`` is a small title shown there otherwise.
    """
    from .captions import _bgr

    r = cfg.render
    folder = out.parent
    voice = speak(text, folder / f"{out.stem}_voice", cfg)
    spoken = audio_duration(voice, cfg)
    duration = CARD_LEAD_S + spoken + tail_s
    words = [Word(w.start + CARD_LEAD_S, w.end + CARD_LEAD_S, w.text) for w in narration_words(text, voice, spoken, cfg)]
    styles, events = card_graphics(duration, _bgr(cfg.captions.active_color), label=label, big=big)
    script = build_ass(words, replace(cfg.captions, margin_v=CARD_CAPTION_MARGIN_V)).replace("\n[Events]", "\n".join(styles) + "\n\n[Events]", 1) + "\n".join(events) + "\n"
    write_text(folder / f"{out.stem}.ass", script)
    frames = int(duration * r.fps) + 2
    lead_ms = int(CARD_LEAD_S * 1000)
    sounds = "anoisesrc=d=0.6:c=pink:a=0.6,highpass=f=400,lowpass=f=6000,afade=t=in:d=0.3,afade=t=out:st=0.3:d=0.3,volume=0.5[sw];"
    mix = "[a1][sw]amix=inputs=2:normalize=0"
    if big:  # a low thump as the rank number lands
        sounds += "sine=f=62:d=0.3,afade=t=out:d=0.3,adelay=200|200,volume=0.9[th];"
        mix = "[a1][sw][th]amix=inputs=3:normalize=0"
    graph = (
        card_filter(r.width, r.height, r.fps, frames, f"{out.stem}.ass", variant=variant, accent=cfg.captions.active_color) + ";"
        f"[1:a]aresample=48000,loudnorm=I={r.loudness_lufs}:TP=-1.0:LRA=11,adelay={lead_ms}|{lead_ms}[a1];"
        + sounds + mix + ",aresample=48000,apad[a]"
    )
    ffmpeg = find_tool("ffmpeg", cfg.data_dir)
    part = f"{out.stem}.part.mp4"
    run(
        [ffmpeg, "-y", "-hide_banner", "-v", "error", "-i", str(still_from), "-i", voice.name,
         "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}"]
        + hw.encoder_args(hw.encoder(cfg), r.crf, r.preset)
        + ["-pix_fmt", "yuv420p", "-r", str(r.fps), "-c:a", "aac", "-b:a", r.audio_bitrate, "-ar", "48000", "-ac", "2", part],
        timeout_s=600, what="narration card", cwd=folder,
    )
    os.replace(folder / part, out)
    return out


def concat(parts: list[Path], out: Path, cfg: Config) -> Path:
    """Join videos end to end, re-encoding so every part ends up with identical settings."""
    if not parts:
        raise ToolError("nothing to join")
    r = cfg.render
    ffmpeg = find_tool("ffmpeg", cfg.data_dir)
    inputs: list[str] = []
    chains, labels = [], ""
    for i, p in enumerate(parts):
        inputs += ["-i", str(p)]
        chains.append(f"[{i}:v]scale={r.width}:{r.height},fps={r.fps},setsar=1,format=yuv420p[v{i}];[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        labels += f"[v{i}][a{i}]"
    graph = ";".join(chains) + f";{labels}concat=n={len(parts)}:v=1:a=1[v][a]"
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.stem + ".part.mp4")
    run(
        [ffmpeg, "-y", "-hide_banner", "-v", "error"] + inputs + ["-filter_complex", graph, "-map", "[v]", "-map", "[a]"]
        + hw.encoder_args(hw.encoder(cfg), r.crf, r.preset)
        + ["-pix_fmt", "yuv420p", "-r", str(r.fps), "-c:a", "aac", "-b:a", r.audio_bitrate, "-ar", "48000", "-movflags", "+faststart", str(part)],
        timeout_s=1800, what="joining segments",
    )
    os.replace(part, out)
    return out


def explained(clip_dir: Path, cfg: Config, force: bool = False, variant: str = "a") -> Path:
    """<clip>/explained.mp4: a narrated set-up card, then the clip.

    Variant "b" (explained_b.mp4) is the A/B partner: it speaks ``intro_b``, uses the
    other card animation and is followed by the B version of the clip.
    """
    b = variant == "b"
    out = clip_dir / ("explained_b.mp4" if b else "explained.mp4")
    if done(out) and not force:
        return out
    state = read_json(clip_dir / "clip.json")
    intro = ((state.get("intro_b") if b else "") or state.get("intro") or "").strip()
    if not intro:
        raise ToolError(f"clip {clip_dir.name} has no intro line. Run 'clipper write' first, or set \"intro\" in its clip.json.")
    body = render_clip(clip_dir, cfg, out_name="final_b.mp4", variant="b") if b else render_clip(clip_dir, cfg)
    t0 = time.monotonic()
    card = make_card(still_frame(clip_dir, cfg), intro, clip_dir / ("intro_card_b.mp4" if b else "intro_card.mp4"), cfg,
                     label=str(state.get("channel") or ""), variant=variant)
    concat([card, body], out, cfg)
    from .work import update_clip

    state = update_clip(clip_dir / "clip.json", {"explained_seconds": round(time.monotonic() - t0, 1)})
    log.info("clip %s: explained version made in %.0fs", clip_dir.name, state["explained_seconds"])
    return out


def top_moments(clip_dirs: list[Path], out: Path, cfg: Config, opener: str = "") -> Path:
    """One video counting down the given clips. ``clip_dirs`` is best-first; the video plays worst to best."""
    if len(clip_dirs) < 2:
        raise ToolError("a top-moments video needs at least 2 clips")
    work = out.parent / f"{out.stem}_parts"
    work.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    first_body = None
    bodies = []
    for rank, d in enumerate(clip_dirs, 1):
        state = read_json(d / "clip.json")
        line = (state.get("rank_line") or state.get("hook") or "").strip() or f"Moment number {rank}."
        body = render_clip(d, cfg, out_name="body.mp4", hook=False)  # the card already says what this is
        bodies.append((rank, line, body, still_frame(d, cfg)))
    for rank, line, body, still in reversed(bodies):
        if first_body is None and opener:
            first_body = body
            parts.append(make_card(still, opener, work / "opener.mp4", cfg, label=f"Top {len(bodies)}"))
        parts.append(make_card(still, line, work / f"card_{rank}.mp4", cfg, big=f"#{rank}"))
        parts.append(body)
    return concat(parts, out, cfg)
