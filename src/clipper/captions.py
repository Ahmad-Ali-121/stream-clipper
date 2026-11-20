"""Build word-by-word highlighted captions as an ASS subtitle file.

Input is a list of words with start/end times in seconds *relative to the clip*
(Whisper word timestamps minus the clip start). Output is ASS text that FFmpeg
burns in with the ``ass=`` filter.

Timing rules, chosen so captions never flicker or overlap:
  * A caption line shows a few words; exactly one is highlighted at a time.
  * Inside a line, a word stays highlighted until the next word starts, so
    there is no blank frame between words.
  * A line never runs past the start of the next line.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .config import CaptionSettings


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class CaptionEvent:
    start: float
    end: float
    words: tuple[str, ...]
    active: int  # index of the highlighted word


def clean_text(text: str) -> str:
    """Make a word safe for ASS: braces start override tags and backslashes start escapes."""
    out = text.replace("{", "(").replace("}", ")").replace("\\", "/")
    return " ".join(out.split())


def words_from_json(items: Iterable[dict[str, Any]], offset_s: float = 0.0) -> list[Word]:
    """Accept faster-whisper style dicts ({"start","end","word"}) or {"start","end","text"}.

    ``offset_s`` is subtracted from every time (pass the clip's start within the
    source). Words that end before 0 after the shift are dropped.
    """
    words: list[Word] = []
    for it in items:
        text = clean_text(str(it.get("word") if it.get("word") is not None else it.get("text", "")))
        if not text:
            continue
        start = float(it["start"]) - offset_s
        end = float(it["end"]) - offset_s
        if end <= 0:
            continue
        words.append(Word(start=max(0.0, start), end=max(end, max(0.0, start)), text=text))
    words.sort(key=lambda w: w.start)
    return words


def group_words(words: Sequence[Word], max_words: int = 3, max_chars: int = 18, max_gap_s: float = 0.6) -> list[list[Word]]:
    """Split words into caption lines by count, width and pauses."""
    lines: list[list[Word]] = []
    current: list[Word] = []
    for w in words:
        if current:
            chars = sum(len(x.text) for x in current) + len(current) + len(w.text)
            gap = w.start - current[-1].end
            if len(current) >= max_words or chars > max_chars or gap > max_gap_s:
                lines.append(current)
                current = []
        current.append(w)
    if current:
        lines.append(current)
    return lines


ANIMATIONS = {"highlight": "The spoken word changes colour", "pop": "The spoken word pops in", "word": "One big word at a time",
              "build": "The line fills in word by word"}


def build_events(words: Sequence[Word], settings: CaptionSettings) -> list[CaptionEvent]:
    lines = group_words(words, 1 if settings.animation == "word" else settings.max_words, settings.max_chars, settings.max_gap_s)
    events: list[CaptionEvent] = []
    for li, line in enumerate(lines):
        next_line_start = lines[li + 1][0].start if li + 1 < len(lines) else None
        texts = tuple(w.text.upper() if settings.uppercase else w.text for w in line)
        for wi, w in enumerate(line):
            if wi + 1 < len(line):
                end = line[wi + 1].start
            else:
                end = max(w.end, w.start + settings.min_word_s)
                if next_line_start is not None:
                    end = min(end, next_line_start)
            # Compare in centiseconds: that is ASS's resolution, and two words
            # landing in the same centisecond would make a zero-length event.
            if _cs(end) <= _cs(w.start):
                continue
            events.append(CaptionEvent(start=w.start, end=end, words=texts, active=wi))
    return events


def _cs(t: float) -> int:
    return int(round(max(0.0, t) * 100))


def ass_time(t: float) -> str:
    cs = _cs(t)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _bgr(rrggbb: str) -> str:
    h = rrggbb.strip().lstrip("#")
    if len(h) != 6 or any(c not in "0123456789abcdefABCDEF" for c in h):
        raise ValueError(f"colour must be RRGGBB hex, got {rrggbb!r}")
    return (h[4:6] + h[2:4] + h[0:2]).upper()


def _event_text(ev: CaptionEvent, s: CaptionSettings) -> str:
    big = s.active_scale
    if s.animation in ("pop", "word", "build"):
        # The spoken word starts small, overshoots a little and settles, in about a sixth of a second.
        size = f"\\fscx70\\fscy70\\t(0,70,\\fscx{big + 10}\\fscy{big + 10})\\t(70,160,\\fscx{big}\\fscy{big})"
    else:
        size = f"\\fscx{big}\\fscy{big}"
    parts = []
    for i, word in enumerate(ev.words):
        if i == ev.active:
            parts.append(f"{{\\c&H{_bgr(s.active_color)}&{size}}}{word}{{\\r}}")
        elif i > ev.active and s.animation == "build":
            parts.append(f"{{\\alpha&HFF&}}{word}{{\\r}}")  # not said yet: there, but invisible, so the line does not jump
        else:
            parts.append(word)
    return " ".join(parts)


@dataclass(frozen=True)
class Overlay:
    """Extra on-screen text burned in with the captions: the hook, the credit, the ad disclosure."""

    text: str
    start: float
    end: float
    style: str  # "Hook", "HookB", "Credit" or "Disclosure"
    pop: bool = False  # the text pops in (grows quickly, then settles) instead of just appearing


# Fonts every Windows has for scripts the caption fonts lack. Without one, Urdu or Hindi words would show as empty boxes.
SCRIPT_FONTS = (((0x0600, 0x06FF), "Segoe UI"), ((0x0750, 0x077F), "Segoe UI"), ((0xFB50, 0xFDFF), "Segoe UI"), ((0xFE70, 0xFEFF), "Segoe UI"),
                ((0x0900, 0x097F), "Nirmala UI"))


def script_font(texts) -> str | None:
    """The font needed for these texts when they hold Urdu (Arabic-script) or Hindi (Devanagari) letters; None for Latin."""
    for text in texts:
        for ch in str(text):
            code = ord(ch)
            for (lo, hi), font in SCRIPT_FONTS:
                if lo <= code <= hi:
                    return font
    return None


def build_ass(
    words: Sequence[Word],
    settings: CaptionSettings,
    overlays: Sequence[Overlay] = (),
    hook_size: int = 76,
    credit_size: int = 40,
    hook_margin_v: int = 170,
    progress_s: float = 0.0,
) -> str:
    # Urdu or Hindi letters anywhere (captions, opening text, credit): switch to a font that has them, and never upper-case.
    font = script_font([w.text for w in words] + [o.text for o in (overlays or [])])
    if font:
        from dataclasses import replace as _replace

        settings = _replace(settings, font=font, uppercase=False)
    s = settings
    header = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {s.play_res_x}",
            f"PlayResY: {s.play_res_y}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding",
            f"Style: Word,{s.font},{s.font_size},&H00{_bgr(s.text_color)},&H00{_bgr(s.text_color)},"
            f"&H00{_bgr(s.outline_color)},&H80000000,-1,0,0,0,100,100,0,0,1,{s.outline},{s.shadow},2,60,60,{s.margin_v},1",
            # Hook: black text on a white box, top centre. BorderStyle 3 draws the box in OutlineColour.
            f"Style: Hook,{s.font},{hook_size},&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,14,0,8,90,90,{hook_margin_v},1",
            # HookB: the B version's hook, black text on a box in the accent colour.
            f"Style: HookB,{s.font},{hook_size},&H00000000,&H00000000,&H00{_bgr(s.active_color)},&H00{_bgr(s.active_color)},-1,0,0,0,100,100,0,0,3,14,0,8,90,90,{hook_margin_v},1",
            "Style: Shape,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
            f"Style: Credit,{s.font},{credit_size},&H30FFFFFF,&H30FFFFFF,&H40000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,0,2,40,40,70,1",
            f"Style: Disclosure,{s.font},{credit_size},&H00FFFFFF,&H00FFFFFF,&H60000000,&H60000000,-1,0,0,0,100,100,0,0,3,8,0,7,40,40,60,1",
            # "PART 2" in the top right corner for the whole clip, and "PART 3 NEXT" near the bottom at the end.
            f"Style: Part,{s.font},{credit_size + 14},&H00000000,&H00000000,&H00{_bgr(s.active_color)},&H00{_bgr(s.active_color)},-1,0,0,0,100,100,0,0,3,10,0,9,40,40,64,1",
            f"Style: PartNext,{s.font},{hook_size},&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,14,0,2,90,90,250,1",
            # The outro ribbon: your own line over the last seconds, on a box in the accent colour, between captions and credit.
            f"Style: Outro,{s.font},{credit_size + 18},&H00000000,&H00000000,&H00{_bgr(s.active_color)},&H00{_bgr(s.active_color)},-1,0,0,0,100,100,0,0,3,12,0,2,70,70,380,1",
            # The same ribbon near the top, for layouts that keep a small face in a bottom corner.
            f"Style: OutroTop,{s.font},{credit_size + 18},&H00000000,&H00000000,&H00{_bgr(s.active_color)},&H00{_bgr(s.active_color)},-1,0,0,0,100,100,0,0,3,12,0,8,70,70,330,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )
    lines = [
        f"Dialogue: 0,{ass_time(ev.start)},{ass_time(ev.end)},Word,,0,0,0,,{_event_text(ev, s)}"
        for ev in build_events(words, s)
    ]
    for ov in overlays:
        if ov.style not in ("Hook", "HookB", "Credit", "Disclosure", "Part", "PartNext", "Outro", "OutroTop"):
            raise ValueError(f"unknown overlay style {ov.style!r}")
        text = clean_text(ov.text)
        if not text or _cs(ov.end) <= _cs(ov.start):
            continue
        if ov.style in ("Hook", "HookB", "Part", "PartNext") and s.uppercase:
            text = text.upper()
        # \q0 turns smart line wrapping on for this event (captions themselves never wrap).
        pop = "\\fscx60\\fscy60\\t(0,140,\\fscx109\\fscy109)\\t(140,250,\\fscx100\\fscy100)" if ov.pop else ""
        lines.append(f"Dialogue: 1,{ass_time(ov.start)},{ass_time(ov.end)},{ov.style},,0,0,0,,{{\\q0{pop}}}{text}")
    if progress_s > 0:
        lines.append(progress_bar(progress_s, s.play_res_x, _bgr(s.active_color)))
    return header + "\n" + "\n".join(lines) + "\n"


def progress_bar(duration: float, width: int, colour_bgr: str, x: int = 0, y: int = 0, height: int = 12) -> str:
    """An ASS event drawing a bar that fills from left to right over ``duration`` seconds."""
    return (
        f"Dialogue: 3,0:00:00.00,{ass_time(duration)},Shape,,0,0,0,,"
        f"{{\\an7\\pos({x},{y})\\1c&H{colour_bgr}&\\fscx0\\t(0,{int(duration * 1000)},\\fscx100)\\p1}}"
        f"m 0 0 l {width} 0 {width} {height} 0 {height}{{\\p0}}"
    )
