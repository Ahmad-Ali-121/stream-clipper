"""Templates: the finished clip set inside a frame.

  card        the video as a rounded card on a background (a blurred copy of itself, a colour, a gradient or your own image)
  xpost       the video inside a social-media post: a round letter, your name and handle, the opening text as the post
  xpost_dark  the same on a dark page
  news        a "breaking news" banner with the opening text as the headline
  hookbox     the opening text in a black band on top for the whole clip, the video below

A template takes the 9:16 picture the layout made, places it in a box on a canvas of the same size, and draws the
rest (boxes, circles, text) as subtitle shapes, so it costs almost nothing to render. Captions move into the
video's box.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .captions import _bgr, ass_time, clean_text
from .config import RenderSettings

TEMPLATES = {
    "none": "No template: the video fills the screen",
    "card": "Card: the video as a rounded card on a background",
    "xpost": "Post (light): your name, account name and the opening text above the video",
    "xpost_dark": "Post (dark)",
    "news": "Breaking news: a banner with the opening text as the headline",
    "hookbox": "Text box: the opening text stays on top for the whole clip",
}
CARD_BACKGROUNDS = {"blur": "A blurred copy of the video", "color": "One colour", "gradient": "Two colours blending", "image": "Your own image"}
# Where the video sits on the 1080 x 1920 canvas (x, y, width, height) and how round its corners are.
BOXES = {"card": (70, 170, 940, 1580, 46), "xpost": (40, 560, 1000, 1190, 38), "xpost_dark": (40, 560, 1000, 1190, 38), "hookbox": (0, 430, 1080, 1490, 0)}


@dataclass
class Frame:
    graph: str  # FFmpeg filters from [vz] (the 9:16 picture) to [t0] (the framed picture)
    inputs: list[str] = field(default_factory=list)  # extra input files, in order (their numbers start at ``first_input``)
    styles: list[str] = field(default_factory=list)  # extra subtitle styles
    events: list[str] = field(default_factory=list)  # extra subtitle events (the drawings and texts)
    caption_margin: int | None = None  # where captions go (distance from the bottom), when the video is not full screen
    shows_hook: bool = False  # the template shows the opening text itself, so the usual pop-in is left out
    hook_margin: int | None = None


def _scaled(box: tuple, r: RenderSettings) -> tuple[int, int, int, int, int]:
    sx, sy = r.width / 1080, r.height / 1920
    x, y, w, h, rad = box
    even = lambda v: int(round(v / 2)) * 2  # noqa: E731
    return even(x * sx), even(y * sy), even(w * sx), even(h * sy), int(rad * sx)


def rounded_mask(path: Path, w: int, h: int, radius: int) -> Path:
    """A white rounded rectangle on black, used to round the video's corners."""
    import cv2
    import numpy as np

    m = np.zeros((h, w), np.uint8)
    rad = max(1, min(radius, w // 2, h // 2))
    cv2.rectangle(m, (rad, 0), (w - rad - 1, h - 1), 255, -1)
    cv2.rectangle(m, (0, rad), (w - 1, h - rad - 1), 255, -1)
    for cx, cy in ((rad, rad), (w - rad - 1, rad), (rad, h - rad - 1), (w - rad - 1, h - rad - 1)):
        cv2.circle(m, (cx, cy), rad, 255, -1, lineType=cv2.LINE_AA)
    ok, data = cv2.imencode(".png", m)  # encoded here and written by Python: OpenCV's own writer fails on folder names with other letters
    if not ok:
        raise ValueError("the rounded-corner mask for the template could not be made")
    path.write_bytes(data.tobytes())
    return path


def _shape(x: int, y: int, w: int, h: int, colour: str, start: float, end: float, layer: int = 0, alpha: str = "00") -> str:
    """A filled rectangle as a subtitle drawing."""
    return (f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},Shape,,0,0,0,,"
            f"{{\\an7\\pos({x},{y})\\1c&H{_bgr(colour)}&\\1a&H{alpha}&\\bord0\\shad0\\p1}}m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}")


def _circle(cx: int, cy: int, rad: int, colour: str, start: float, end: float, layer: int = 0) -> str:
    k = round(rad * 0.5523)  # the usual four-curve circle
    path = (f"m 0 {-rad} b {k} {-rad} {rad} {-k} {rad} 0 b {rad} {k} {k} {rad} 0 {rad} "
            f"b {-k} {rad} {-rad} {k} {-rad} 0 b {-rad} {-k} {-k} {-rad} 0 {-rad}")
    return (f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},Shape,,0,0,0,,"
            f"{{\\an7\\pos({cx},{cy})\\1c&H{_bgr(colour)}&\\bord0\\shad0\\p1}}{path}{{\\p0}}")


def _text(style: str, text: str, start: float, end: float, layer: int = 1, tags: str = "") -> str:
    return f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{{\\q0{tags}}}{clean_text(text)}"


def _hex(value: str, default: str) -> str:
    v = str(value or "").lstrip("#")
    return v if len(v) == 6 and all(c in "0123456789abcdefABCDEF" for c in v) else default


def build(r: RenderSettings, clip_dir: Path, hook: str, channel: str, duration: float, font: str, accent: str, first_input: int) -> Frame:
    """Everything a template adds to one render. ``hook`` is the clip's opening text; ``accent`` the caption highlight colour."""
    name = r.template if r.template in TEMPLATES else "none"
    W, H, fps = r.width, r.height, r.fps
    if name == "none":
        return Frame("[vz]null[t0]")
    sx = W / 1080
    px = lambda v: int(round(v * sx))  # noqa: E731  sizes are designed on a 1080-wide canvas
    hook = (hook or "").strip()
    if name == "news":
        # The video stays full screen; a banner near the bottom carries the headline, captions sit above it.
        top = px(1500)
        events = [_shape(0, top, px(430), px(78), "D00000", 0, duration, 2), _text("TplLabel", "BREAKING NEWS", 0, duration, 3),
                  _shape(0, top + px(78), W, px(150), "FFFFFF", 0, duration, 2), _shape(0, top + px(228), W, px(10), "D00000", 0, duration, 2)]
        if hook:
            events.append(_text("TplHeadline", hook.upper(), 0, duration, 3))
        styles = [
            f"Style: TplLabel,{font},{px(46)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,1,0,1,0,0,7,{px(26)},10,{top + px(12)},1",
            f"Style: TplHeadline,{font},{px(50)},&H00101010,&H00101010,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,1,0,0,7,{px(26)},{px(26)},{top + px(90)},1",
        ]
        return Frame("[vz]null[t0]", styles=styles, events=events, caption_margin=H - top + px(40), shows_hook=True)
    x, y, w, h, rad = _scaled(BOXES[name], r)
    crop_h = min(H, int(round(W * h / w / 2)) * 2)  # the middle of the 9:16 picture, in the box's shape
    inputs: list[str] = []
    fg = f"[vz]crop={W}:{crop_h}:0:{(H - crop_h) // 2},scale={w}:{h}"
    if rad:
        rounded_mask(clip_dir / "frame_mask.png", w, h, rad)
        inputs += ["-loop", "1", "-i", "frame_mask.png"]
        mask_in = first_input
        first_input += 1
    styles: list[str] = []
    events: list[str] = []
    frame = Frame("")
    if name == "card":
        kind = r.card_bg if r.card_bg in CARD_BACKGROUNDS else "blur"
        if kind == "image" and not (r.card_image and Path(r.card_image).exists()):
            kind = "blur"  # the image was removed: fall back rather than fail the clip
        c1, c2 = _hex(r.card_color, "151225"), _hex(r.card_color2, "FF3D8B")
        if kind == "blur":
            graph = (f"[vz]split=2[cardsrc][bgsrc];[bgsrc]scale={W // 4}:{H // 4},boxblur=10:2,scale={W}:{H},eq=brightness=-0.16:saturation=1.15[bg];"
                     + fg.replace("[vz]", "[cardsrc]", 1))
        elif kind == "color":
            graph = f"color=c=0x{c1}:s={W}x{H}:r={fps}[bg];" + fg
        elif kind == "gradient":
            graph = f"gradients=s={W}x{H}:c0=0x{c1}:c1=0x{c2}:x0=0:y0=0:x1={W}:y1={H}:nb_colors=2:r={fps}:speed=0.00001[bg];" + fg
        else:
            inputs += ["-loop", "1", "-i", str(r.card_image)]
            graph = (f"[{first_input}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=6:1,eq=brightness=-0.12,fps={fps}[bg];" + fg)
            first_input += 1
        frame.hook_margin = y + px(70)
    elif name == "hookbox":
        graph = f"color=c=0x000000:s={W}x{H}:r={fps}[bg];" + fg
        if hook:
            styles.append(f"Style: TplHook,{font},{px(78)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,{px(60)},{px(60)},0,1")
            events.append(_text("TplHook", hook.upper(), 0, duration, 3, tags=f"\\an5\\pos({W // 2},{y // 2 + px(10)})"))
        frame.shows_hook = True
    else:  # the two post templates
        dark = name == "xpost_dark"
        page, ink, grey = ("000000", "F2F4F7", "8B98A5") if dark else ("FFFFFF", "0F1419", "536471")
        graph = f"color=c=0x{page}:s={W}x{H}:r={fps}[bg];" + fg
        who = (r.post_name or channel or "Clips").strip()
        handle = (r.post_handle or ("@" + "".join(ch for ch in who if ch.isalnum()))).strip()
        handle = handle if handle.startswith("@") else "@" + handle
        cx, cy, cr = px(40) + px(56), px(150), px(56)
        events += [_circle(cx, cy, cr, accent, 0, duration, 2),
                   _text("TplLetter", who[:1].upper(), 0, duration, 3, tags=f"\\an5\\pos({cx},{cy})"),
                   _text("TplName", who, 0, duration, 3, tags=f"\\an4\\pos({cx + cr + px(24)},{cy - px(24)})"),
                   _text("TplHandle", handle, 0, duration, 3, tags=f"\\an4\\pos({cx + cr + px(24)},{cy + px(30)})")]
        styles += [
            f"Style: TplLetter,{font},{px(62)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
            f"Style: TplName,Segoe UI,{px(46)},&H00{_bgr(ink)},&H00{_bgr(ink)},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,4,0,0,0,1",
            f"Style: TplHandle,Segoe UI,{px(38)},&H00{_bgr(grey)},&H00{_bgr(grey)},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,4,0,0,0,1",
            f"Style: TplPost,Segoe UI,{px(50)},&H00{_bgr(ink)},&H00{_bgr(ink)},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,{px(44)},{px(44)},{px(250)},1",
        ]
        if hook:
            events.append(_text("TplPost", hook, 0, duration, 3))
        frame.shows_hook = True
    graph += "[fg0];"
    if rad:
        graph += f"[{mask_in}:v]format=gray,fps={fps}[mk];[fg0][mk]alphamerge=shortest=1[fg];"
    else:
        graph += "[fg0]null[fg];"
    graph += f"[bg][fg]overlay={x}:{y}:shortest=1[t0]"
    frame.graph, frame.inputs, frame.styles, frame.events = graph, inputs, styles, events
    frame.caption_margin = (H - (y + h)) + int(h * 0.24)
    return frame
