"""The words of a YouTube video, taken from YouTube's own captions instead of listening to it.

YouTube already writes down what is said in most videos and finished streams, with a time for every word.
Asking for that takes a second or two; working it out here with the speech model takes minutes per clip on a
processor and up to an hour for a whole stream. So for YouTube links these words are used when they exist, and
the speech model stays the fallback (no captions, a live stream, a Twitch video, a file).

What comes back is the same shape the speech model gives: [{"start", "end", "word"}], seconds from the start of
the video. Captions written by a person are preferred over automatic ones. Sound descriptions such as [Music]
are left out.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from pathlib import Path

from .config import Config
from .work import done, read_json, write_json

log = logging.getLogger(__name__)

CACHE = "yt_words.json"
_failed: dict[str, float] = {}  # work folder -> when asking YouTube last failed, so one run does not ask once per clip
_NOTE = re.compile(r"^[\[(（].*[\])）]$")  # [Music], (applause)
MAX_WORD_S = 0.8  # a word is never shown as lasting longer than this, however long the pause after it


def pick_track(info: dict, want: str = "") -> tuple[str, str, str] | None:
    """(address, language, "written"|"automatic") of the best caption track in a video's details, or None.

    ``want`` is the language spoken (en, hi, ur; "" or "auto" = the video's own). A track written by a person in
    that language wins; then YouTube's automatic track in the language it heard (marked "-orig"); never a
    machine translation into another language, which would not match the words spoken.
    """
    written, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}
    spoken = (want if want and want != "auto" else "") or str(info.get("language") or "")

    def json3(tracks) -> str:
        return next((t["url"] for t in tracks or [] if t.get("ext") == "json3" and t.get("url")), "")

    def base(code: str) -> str:
        return code.split("-")[0].lower()

    for code, tracks in written.items():
        if spoken and base(code) == base(spoken) and code != "live_chat" and json3(tracks):
            return json3(tracks), code, "written"
    originals = [c for c in auto if c.endswith("-orig")]
    for code in originals:
        if not spoken or base(code) == base(spoken):
            if json3(auto[code]):
                return json3(auto[code]), code[:-5], "automatic"
    # Spoken Urdu and Hindi are one language; YouTube labels such streams either way.
    if base(spoken) in ("ur", "hi"):
        for code in originals:
            if base(code) in ("ur", "hi") and json3(auto[code]):
                return json3(auto[code]), code[:-5], "automatic"
    if len(originals) == 1 and not want and json3(auto[originals[0]]):
        return json3(auto[originals[0]]), originals[0][:-5], "automatic"
    return None


def parse_json3(data: dict) -> list[dict]:
    """YouTube's timed-text format as a list of words with times in seconds."""
    out: list[dict] = []
    for event in data.get("events") or []:
        segs = [s for s in event.get("segs") or [] if str(s.get("utf8") or "").strip()]
        if not segs:
            continue
        t0, length = float(event.get("tStartMs") or 0), float(event.get("dDurationMs") or 0)
        timed = len(segs) > 1 or "tOffsetMs" in segs[0]  # automatic captions time every word; written ones time a line
        line_words = [w for s in segs for w in str(s["utf8"]).split()]
        n = 0
        for seg in segs:
            for word in str(seg["utf8"]).split():
                at = t0 + float(seg.get("tOffsetMs") or 0) if timed else t0 + length * n / max(1, len(line_words))
                n += 1
                out.append({"start": round(at / 1000, 3), "word": word.replace("।", ".")})  # the Hindi full stop
    out.sort(key=lambda w: w["start"])
    # Sound descriptions in square brackets are left out: single ones, and ones spread over several words
    # ("[Music", "playing]"). Round brackets are ordinary speech in captions written by a person ("(which is rare),"),
    # so those words stay and only the bracket signs go.
    kept, inside = [], False
    for w in out:
        text = w["word"]
        if inside:
            inside = "]" not in text  # the closing bracket may carry punctuation after it: "laughs]!"
            continue
        bare = text.strip(".,!?;:")
        if _NOTE.match(bare) and bare[:1] == "[" or text in (">>", "-", "--", "♪") or bare in ("(Music)", "(Applause)", "(Laughter)"):
            continue
        if text[:1] == "[":
            inside = "]" not in text
            continue
        text = text.replace("(", "").replace(")", "").replace("（", "").replace("）", "")
        if not text:
            continue
        w["word"] = text
        kept.append(w)
    for i, w in enumerate(kept):
        nxt = kept[i + 1]["start"] if i + 1 < len(kept) else w["start"] + 0.4
        w["end"] = round(max(w["start"] + 0.08, min(nxt, w["start"] + MAX_WORD_S)), 3)
    return _drop_loops(kept)


def _drop_loops(words: list[dict]) -> list[dict]:
    """Automatic captions sometimes repeat one word many times over noise ("no no no no no no"). Keep three."""
    out: list[dict] = []
    run = 0
    for w in words:
        same = bool(out) and out[-1]["word"].lower() == w["word"].lower()
        run = run + 1 if same else 0
        if run < 3:
            out.append(w)
    return out


def video_words(url: str, work: Path, cfg: Config) -> list[dict] | None:
    """Every word of a YouTube video from its own captions, or None when it has none in the language spoken.

    Asked once per video and kept in the work folder. Problems (no internet, a format change) are logged and
    give None, so the caller falls back to the speech model: this is a faster way, never the only way.
    """
    if cfg.transcribe.youtube_captions == "off":
        return None
    import time

    cache = work / CACHE
    if done(cache):
        saved = read_json(cache, {})
        # A video with words keeps them. "None yet" is asked again after six hours: YouTube writes captions a while after a stream ends.
        if saved.get("language_asked") == cfg.transcribe.language and (saved.get("words") or time.time() - float(saved.get("at") or 0) < 6 * 3600):
            return saved.get("words") or None
    # Asking failed a moment ago (no internet, still live): do not ask again for every clip of the same run.
    if time.time() - _failed.get(str(work), 0.0) < 600:
        return None
    try:
        from .fetch import _ytdlp
        from .tools import run

        info = json.loads(run(_ytdlp(cfg) + ["--skip-download", "--dump-single-json", url], timeout_s=180, retries=1,
                              what="asking YouTube for this video's captions").stdout)
        if info.get("is_live"):
            log.info("this stream is still live, so YouTube has no finished captions for it yet; the speech model is used")
            _failed[str(work)] = time.time()
            return None
        track = pick_track(info, cfg.transcribe.language)
        if not track:
            log.info("YouTube has no captions for this video in the language spoken (%s); the speech model is used", cfg.transcribe.language)
            write_json(cache, {"language_asked": cfg.transcribe.language, "words": [], "why": "no caption track", "at": time.time()})
            return None
        address, language, kind = track
        req = urllib.request.Request(address, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            words = parse_json3(json.loads(resp.read(60_000_000)))
    except Exception as e:
        log.warning("YouTube's captions could not be read (%s); the speech model is used instead", e)
        _failed[str(work)] = time.time()
        return None
    write_json(cache, {"language_asked": cfg.transcribe.language, "language": language, "kind": kind, "words": words})
    log.info("YouTube's own captions: %d words (%s, %s)", len(words), language, kind)
    return words or None


def window(words: list[dict], start_s: float, end_s: float) -> list[dict]:
    """The words between two moments of the video, with times counted from ``start_s`` (as a clip's words are)."""
    return [{"start": round(w["start"] - start_s, 3), "end": round(min(w["end"], end_s) - start_s, 3), "word": w["word"], "p": 1.0}
            for w in words if start_s <= w["start"] < end_s]


def as_lines(words: list[dict], line_s: float = 8.0) -> list[dict]:
    """Words grouped into short timed lines ({"t", "e", "text"}), the shape the whole-stream reader stores."""
    rows: list[dict] = []
    for w in words:
        if rows and w["start"] - rows[-1]["t"] < line_s and w["start"] - rows[-1]["e"] < 2.5:
            rows[-1]["text"] += " " + w["word"]
            rows[-1]["e"] = round(w["end"], 1)
        else:
            rows.append({"t": round(w["start"], 1), "e": round(w["end"], 1), "text": w["word"]})
    return rows
