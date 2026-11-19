"""Choose each clip's layout by looking at it.

Streams switch between a full-screen camera (talking to chat) and a small
facecam over gameplay, sometimes within minutes. A fixed per-streamer layout
gets one of those wrong, so a few frames of every clip are checked for faces:

  * a big face in most frames      -> "crop", centred on that face
  * a small face in the facecam box -> "split" (facecam on top, gameplay below)
  * neither                         -> the streamer's default from config

Faces in the game itself and in chat emotes are everywhere, which is why a
single frame is never trusted: a face only counts if it is there in most samples.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Sequence

from .config import Config

log = logging.getLogger(__name__)

YUNET_FILE = "face_detection_yunet_2023mar.onnx"
BIG_FACE_H = 0.18  # a face this tall (share of frame height) means the camera fills the screen
MIN_SCORE = 0.8
SAMPLES = 5


@dataclass(frozen=True)
class Face:
    x: float  # all as fractions of the frame: left, top, width, height
    y: float
    w: float
    h: float
    score: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass(frozen=True)
class LayoutChoice:
    layout: str | None  # "crop", "split", or None when nothing could be decided
    crop_x: float | None = None
    facecam: list[float] | None = None
    reason: str = ""


def _in_box(f: Face, box: Sequence[float]) -> bool:
    return box[0] <= f.cx <= box[0] + box[2] and box[1] <= f.cy <= box[1] + box[3]


def estimate_facecam(faces: Sequence[Face]) -> list[float]:
    """Guess the facecam rectangle around a small, steady face (used when the streamer has no profile).

    On the layouts measured so far the facecam is five to seven face-widths wide
    and roughly 16:9 (measured: xQc 4.7, Clix 6.2, Lacy 6.9). Boxes that land near a frame edge are snapped to it, because
    facecams sit in corners. This is an estimate; it is flagged for review.
    """
    cx, cy, fw = median(f.cx for f in faces), median(f.cy for f in faces), median(f.w for f in faces)
    w = min(max(fw * 6.0, 0.15), 0.45)
    h = w  # a 16:9 box inside a 16:9 frame covers equal fractions of width and height
    x = min(max(cx - w / 2, 0.0), 1.0 - w)
    y = min(max(cy - h * 0.45, 0.0), 1.0 - h)
    if x < 0.06:
        x = 0.0
    if x + w > 0.94:
        x = 1.0 - w
    if y < 0.06:
        y = 0.0
    if y + h > 0.94:
        y = 1.0 - h
    return [round(x, 3), round(y, 3), round(w, 3), round(h, 3)]


def steady_small_face(frames: Sequence[Sequence[Face]]) -> list[Face]:
    """The small face that sits in the same place in the most frames (one Face per frame it appears in)."""
    frames = [[f for f in faces if f.score >= MIN_SCORE and f.h < BIG_FACE_H] for faces in frames]
    best: list[Face] = []
    for anchor in (f for faces in frames for f in faces):
        group = [next((f for f in faces if abs(f.cx - anchor.cx) < 0.04 and abs(f.cy - anchor.cy) < 0.05), None) for faces in frames]
        group = [f for f in group if f]
        if len(group) > len(best):
            best = group
    return best


# ---- measuring the facecam box from the picture itself ----
#
# A facecam is a rectangle whose border stays put while the game around it
# moves. Take several frames, keep only the edges present in most of them, and
# look for the rectangle of such edges around the streamer's face. The sides
# may also be the edge of the frame, since facecams usually sit in a corner.

MEASURE_W = 960  # frames are shrunk to this width for measuring
EDGE_LEVEL = 14.0  # brightness step (0-255) that counts as an edge
FRAME_EDGE_SCORE = 0.85  # a side lying on the frame edge scores as a fairly good border
MEASURE_OK = 0.95  # below this the measured box is not trusted
AGREE = 0.015  # two measurements agree when every side is within this fraction of the frame


def measure_box(grays: Sequence[Any], cx: float, cy: float, fw: float) -> tuple[list[float], float] | None:
    """Find the facecam rectangle around a face. Pure numpy.

    ``grays``: same-size greyscale frames (2-D arrays). ``cx, cy, fw``: face centre
    and width as fractions of the frame. Returns ([x, y, w, h] fractions, score)
    or None. The score is about 1.0+ for a clean rectangle.
    """
    import itertools

    import numpy as np

    st = np.stack([np.asarray(g, dtype=np.float32) for g in grays])
    if st.shape[0] < 3:
        return None
    H, W = st.shape[1:]
    ex = np.zeros((H, W), bool)  # steady vertical edges
    ey = np.zeros((H, W), bool)  # steady horizontal edges
    ex[:, 1:-1] = np.median(np.abs(st[:, :, 2:] - st[:, :, :-2]), axis=0) > EDGE_LEVEL
    ey[1:-1, :] = np.median(np.abs(st[:, 2:, :] - st[:, :-2, :]), axis=0) > EDGE_LEVEL
    px, py, pw = cx * W, cy * H, fw * W

    def vcov(x: int, y0: float, y1: float) -> float:
        if y1 - y0 < 5:
            return 0.0
        return float(ex[int(y0):int(y1), max(x - 1, 0):min(x + 2, W)].any(axis=1).mean())

    def hcov(y: int, x0: float, x1: float) -> float:
        if x1 - x0 < 5:
            return 0.0
        return float(ey[max(y - 1, 0):min(y + 2, H), int(x0):int(x1)].any(axis=0).mean())

    def peaks(profile: Any, lo: float, hi: float) -> list[int]:
        lo, hi = max(int(lo), 0), min(int(hi), len(profile))
        idx = [i for i in range(lo, hi) if profile[i] >= 0.35 and profile[i] == profile[max(lo, i - 3):min(hi, i + 4)].max()]
        return sorted(idx, key=lambda i: -profile[i])[:6]

    span = pw * 1.2
    vprof = np.array([vcov(x, max(py - span, 0), min(py + span, H)) for x in range(W)])
    hprof = np.array([hcov(y, max(px - span, 0), min(px + span, W)) for y in range(H)])
    lefts = peaks(vprof, px - pw * 6, px - pw * 0.8) + ([0] if px - pw * 6 < 0 else [])
    rights = peaks(vprof, px + pw * 0.8, px + pw * 6) + ([W] if px + pw * 6 > W else [])
    tops = peaks(hprof, py - pw * 5, py - pw * 0.8) + ([0] if py - pw * 5 < 0 else [])
    bottoms = peaks(hprof, py + pw * 0.8, py + pw * 6) + ([H] if py + pw * 6 > H else [])
    best: tuple[float, list[float]] | None = None
    for left, right, top, bottom in itertools.product(lefts, rights, tops, bottoms):
        w, h = right - left, bottom - top
        if not (2.5 * pw <= w <= 9 * pw) or not (1.0 <= w / h <= 1.95) or not (0.2 <= (px - left) / w <= 0.8):
            continue
        sides = [
            vcov(left, top, bottom) if left > 0 else FRAME_EDGE_SCORE,
            vcov(right, top, bottom) if right < W else FRAME_EDGE_SCORE,
            hcov(top, left, right) if top > 0 else FRAME_EDGE_SCORE,
            hcov(bottom, left, right) if bottom < H else FRAME_EDGE_SCORE,
        ]
        score = min(sides) + 0.25 * sum(sides) / 4  # the weakest side decides: all four must be real
        if best is None or score > best[0]:
            best = (score, [round(left / W, 4), round(top / H, 4), round(w / W, 4), round(h / H, 4)])
    return (best[1], round(best[0], 3)) if best else None


def consensus_box(boxes: Sequence[Sequence[float]]) -> list[float] | None:
    """The box that at least two measurements agree on (their average), or None.

    One measurement can be fooled by a shelf or a door frame behind the streamer;
    the same wrong answer twice from different clips is unlikely.
    """
    best: list[Sequence[float]] = []
    for a in boxes:
        group = [b for b in boxes if all(abs(x - y) <= AGREE for x, y in zip(a, b))]
        if len(group) > len(best):
            best = group
    if len(best) < 2:
        return None
    return [round(sum(b[i] for b in best) / len(best), 4) for i in range(4)]


def _learned_path(cfg: Config) -> Path:
    return cfg.data_dir / "facecams.json"


def learned_facecam(channel: str, cfg: Config) -> list[float] | None:
    """The facecam box measured for this channel on earlier clips, once two clips agree."""
    from .work import read_json

    entries = (read_json(_learned_path(cfg), {}) or {}).get(channel.lower(), [])
    return consensus_box([e["box"] for e in entries])


def remember_facecam(channel: str, clip: str, box: Sequence[float], score: float, cfg: Config, keep: int = 20) -> None:
    from .work import read_json, write_json

    data = read_json(_learned_path(cfg), {}) or {}
    entries = [e for e in data.get(channel.lower(), []) if e.get("clip") != clip]
    entries.append({"clip": clip, "box": list(box), "score": score})
    data[channel.lower()] = entries[-keep:]
    write_json(_learned_path(cfg), data)


def measure_facecam(video: Path, faces: Sequence[Face], samples: int = 12) -> tuple[list[float], float] | None:
    """Measure the facecam box in a video around the given steady face."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    grays = []
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / samples))
            ok, img = cap.read()
            if ok:
                h, w = img.shape[:2]
                small = cv2.resize(img, (MEASURE_W, max(int(round(MEASURE_W * h / w)), 2)), interpolation=cv2.INTER_AREA)
                grays.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    finally:
        cap.release()
    if len(grays) < 3 or not faces:
        return None
    return measure_box(grays, median(f.cx for f in faces), median(f.cy for f in faces), median(f.w for f in faces))


def choose_layout(frames: Sequence[Sequence[Face]], facecam: Sequence[float] | None) -> LayoutChoice:
    """Decide from the faces found in several frames. Pure logic; see module docstring."""
    frames = [[f for f in faces if f.score >= MIN_SCORE] for faces in frames]
    if not frames:
        return LayoutChoice(None, reason="no frames")
    need = len(frames) // 2 + 1

    big = [max((f for f in faces if f.h >= BIG_FACE_H), key=lambda f: f.h, default=None) for faces in frames]
    big = [f for f in big if f]
    if len(big) >= need:
        return LayoutChoice("crop", crop_x=round(min(max(median(f.cx for f in big), 0.0), 1.0), 3),
                            reason=f"large face in {len(big)} of {len(frames)} frames")

    if facecam:
        hits = [next((f for f in faces if _in_box(f, facecam)), None) for faces in frames]
        hits = [f for f in hits if f]
        if len(hits) >= need:
            return LayoutChoice("split", facecam=list(facecam), reason=f"face inside the facecam box in {len(hits)} of {len(frames)} frames")
        return LayoutChoice(None, reason="no steady face found")

    # No profile for this streamer: look for a small face that stays put.
    best = steady_small_face(frames)
    if len(best) >= need:
        return LayoutChoice("split", facecam=estimate_facecam(best), reason=f"steady small face in {len(best)} of {len(frames)} frames; facecam box estimated")
    return LayoutChoice(None, reason="no steady face found")


def detect_faces(video: Path, cfg: Config, samples: int = SAMPLES) -> list[list[Face]]:
    """Faces in evenly spaced frames of a video."""
    import cv2

    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)  # OpenCV prints a harmless warning per detector
    model = cfg.data_dir / "models" / YUNET_FILE
    if not model.exists():
        raise FileNotFoundError(f"face model missing: {model}. Run setup.ps1.")
    cap = cv2.VideoCapture(str(video))
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            return []
        detector = cv2.FaceDetectorYN.create(str(model), "", (320, 320), 0.6, 0.3, 50)
        out: list[list[Face]] = []
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / samples))
            ok, img = cap.read()
            if not ok:
                continue
            h, w = img.shape[:2]
            detector.setInputSize((w, h))
            _n, found = detector.detect(img)
            out.append([
                Face(float(r[0]) / w, float(r[1]) / h, float(r[2]) / w, float(r[3]) / h, float(r[-1]))
                for r in (found if found is not None else [])
            ])
        return out
    finally:
        cap.release()


def _measured_choice(clip_dir: Path, channel: str, frames: Sequence[Sequence[Face]], choice: LayoutChoice, cfg: Config) -> LayoutChoice:
    """For a streamer with no facecam in config.yaml: measure the box in this clip and learn from it."""
    faces = steady_small_face(frames)
    if len(faces) < len(frames) // 2 + 1:
        return choice
    measured = measure_facecam(clip_dir / "source.mp4", faces)
    good = bool(measured and measured[1] >= MEASURE_OK)
    if good:
        remember_facecam(channel, f"{clip_dir.parent.parent.name}/{clip_dir.name}", measured[0], measured[1], cfg)
    cx, cy = median(f.cx for f in faces), median(f.cy for f in faces)
    learned = learned_facecam(channel, cfg)
    if learned and learned[0] <= cx <= learned[0] + learned[2] and learned[1] <= cy <= learned[1] + learned[3]:
        return LayoutChoice("split", facecam=learned, reason="facecam box measured on this and earlier clips")
    if good:
        return LayoutChoice("split", facecam=measured[0], reason="facecam box measured on this clip only; check the framing")
    return choice


def _framing_path(cfg: Config) -> Path:
    return cfg.data_dir / "framing.json"


def saved_framing(channel: str, cfg: Config) -> dict | None:
    """The face and video boxes a person saved for this streamer on the review page, if any."""
    from .work import read_json

    return (read_json(_framing_path(cfg), {}) or {}).get(channel.lower()) if channel else None


def save_framing(channel: str, framing: dict | None, cfg: Config) -> None:
    """Remember (or with None, forget) the boxes to use for every new clip of this streamer."""
    from .work import read_json, write_json

    if not channel:
        return
    data = read_json(_framing_path(cfg), {}) or {}
    if framing is None:
        data.pop(channel.lower(), None)
    else:
        data[channel.lower()] = {k: framing.get(k) for k in ("layout", "facecam", "game", "source")}
    write_json(_framing_path(cfg), data)


def auto_layout(clip_dir: Path, state: dict, cfg: Config) -> dict:
    """Set layout / crop_x / facecam on a clip's state from its source video.

    A layout chosen by a person (on the review page or by hand) is never overwritten.
    Detection problems are logged and leave the streamer's default in place.
    """
    if state.get("layout") and not state.get("layout_auto"):
        return state
    profile = cfg.channels.get(str(state.get("channel", "")).lower(), {}) or {}
    channel = str(state.get("channel", ""))
    saved = saved_framing(channel, cfg)
    # Boxes are fractions of the picture, so they only fit a stream of the same shape as the one they were drawn on.
    if saved and saved.get("layout") in ("crop", "split"):
        sw, sh = (saved.get("source") or [16, 9])[:2]
        if abs(sw / sh - float(state.get("source_width") or 16) / float(state.get("source_height") or 9)) < 0.02:
            state["layout"], state["layout_auto"] = saved["layout"], True
            state["layout_reason"] = "the boxes you saved for this streamer"
            for k in ("facecam", "game"):
                if saved.get(k):
                    state[k] = list(saved[k])
                else:
                    state.pop(k, None)
            return state
    try:
        frames = detect_faces(clip_dir / "source.mp4", cfg)
        known = profile.get("facecam") or learned_facecam(channel, cfg)
        choice = choose_layout(frames, known)
        if not profile.get("facecam") and choice.layout != "crop":
            choice = _measured_choice(clip_dir, channel, frames, choice, cfg)
    except Exception as e:  # never lose a clip because face detection failed
        log.warning("clip %s: layout detection failed (%s); using the default layout", clip_dir.name, e)
        return state
    state["layout_reason"] = choice.reason
    if choice.layout:
        state["layout"], state["layout_auto"] = choice.layout, True
        if choice.crop_x is not None:
            state["crop_x"] = choice.crop_x
        if choice.facecam and not profile.get("facecam"):
            state["facecam"] = choice.facecam
        log.info("clip %s: layout %s (%s)", clip_dir.name, choice.layout, choice.reason)
    return state
