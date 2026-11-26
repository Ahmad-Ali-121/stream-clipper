"""Local review page: watch each candidate, approve or reject it, edit its hook and cut.

Runs a small web server on this machine only (127.0.0.1). Nothing is posted
anywhere from here; it only edits each clip's clip.json.
"""

from __future__ import annotations

import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .moments import format_time
from .work import FileInUse, clip_dirs, read_json, remove, write_json

log = logging.getLogger(__name__)

EDITABLE = {"status", "hook", "hook_b", "intro", "intro_b", "explainer", "simple", "held", "disclosure", "layout", "facecam", "game", "face_corner", "face_size",
            "cut_start_s", "cut_end_s", "ab", "cover_avoid",
            "title", "title_b", "description", "tags", "notes"}
TEXT_FIELDS = ("hook", "hook_b", "intro", "intro_b", "title", "title_b", "description", "tags")

PAGE_FILE = Path(__file__).with_name("review_page.html")


def code_stamp() -> str:
    """Changes only when the program's code really changes (an update).

    It is made from what the files contain, not from their dates: a file that is merely touched,
    re-saved unchanged or checked out again must not make the page ask for a restart.
    """
    import hashlib

    h = hashlib.sha256()
    for f in sorted(Path(__file__).parent.glob("*.py")):
        h.update(f.name.encode())
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))  # the same code with other line endings is the same code
    return h.hexdigest()[:16]


RUNNING_STAMP = code_stamp()  # the code this running program was started with


class OneAtATimeServer(ThreadingHTTPServer):
    """Refuses to start when the port is already taken.

    The standard server lets a second program listen on the same port on Windows. The browser then
    talks to whichever answers first, usually the older one, which is how a page ends up showing new
    buttons wired to an old program.
    """

    allow_reuse_address = False

    def server_bind(self) -> None:
        import socket

        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def page_html(title: str, has_panel: bool) -> str:
    """The review page: one HTML file, with the title and whether the controls are shown filled in."""
    return (PAGE_FILE.read_text(encoding="utf-8")
            .replace("__TITLE__", json.dumps(title).replace("</", "<\\/"))
            .replace("__HAS_PANEL__", "true" if has_panel else "false")
            .replace("__STAMP__", json.dumps(code_stamp()))
            # the table that puts technical faults into plain words (clipper.plain), so the page explains every error itself
            .replace("__PLAIN__", json.dumps(__import__("clipper.plain", fromlist=["for_page"]).for_page()).replace("</", "<\\/")))


def videos_made(d: Path, state: dict) -> bool:
    """True when an approved clip's videos exist as it stands now: made, not edited since, explainer included if ticked."""
    if state.get("status") != "approved" or not state.get("exported") or not (d / "final.mp4").exists():
        return False
    if state.get("made_all") is False:  # the making was stopped or failed part-way through this clip: it is not finished
        return False
    exports = state.get("exports")
    if isinstance(exports, dict):  # a version ticked after the videos were made is still to be made
        if (state.get("simple", True) and "a" not in exports) or (state.get("explainer") and "xa" not in exports):
            return False
        # version B was made and its render was taken away since (its opening text was changed): it is to be made again
        if (state.get("simple", True) and "b" in exports and not (d / "final_b.mp4").exists()) or \
                (state.get("explainer") and "xb" in exports and not (d / "explained_b.mp4").exists()):
            return False
    return not state.get("explainer") or (d / "explained.mp4").exists()


def _made_at(d: Path, state: dict) -> float:
    """When this clip was first made, as seconds since 1970. Older clips did not record it: their first file's time is used."""
    import time as _time

    for key in ("made_at", "found_at"):
        try:
            return _time.mktime(_time.strptime(str(state.get(key)), "%Y-%m-%d %H:%M:%S"))
        except (ValueError, TypeError, OverflowError):
            continue
    try:
        return min(f.stat().st_ctime for f in d.iterdir() if f.is_file())
    except (OSError, ValueError):
        return 0.0


def _framing(state: dict, cfg) -> dict | None:
    if cfg is None:
        return None
    from .render import framing

    try:
        return framing(state, cfg)
    except Exception as e:  # a clip with odd numbers still shows; only its box editor is missing
        log.warning("clip %s: its framing could not be worked out: %s", state.get("id"), e)
        return None


_preview_lock = __import__("threading").Lock()


def look_preview(works: list[Path], cfg, n: int = 0) -> dict:
    """Frames for the Look and branding page: one of the person's own clips drawn with the settings as they are now.

    ``n`` picks among the newest clips that have their video and captions (0 = the newest). The frames are made again
    only when the look, the clip or the clip's text changed since the last time."""
    import hashlib

    from .render import look_signature, preview_stills

    found = []
    for w in works:
        for d in clip_dirs(w):
            try:
                if (d / "source.mp4").exists() and (d / "words.json").exists():
                    found.append(((d / "clip.json").stat().st_mtime, d))
            except OSError:
                continue
    if not found:
        raise ValueError("There is no clip to show yet. Make one clip (New clips), then this preview shows your own video with these choices.")
    found.sort(key=lambda x: x[0], reverse=True)
    found = found[:12]
    d = found[n % len(found)][1]
    state = read_json(d / "clip.json")
    out = Path(cfg.data_dir) / "tmp" / "look-preview"
    r = cfg.render
    key = hashlib.sha256(repr((look_signature(cfg), bool(r.opening), r.hook_seconds, str(d), (d / "clip.json").stat().st_mtime_ns,
                               (d / "source.mp4").stat().st_mtime_ns, (d / "words.json").stat().st_mtime_ns)).encode()).hexdigest()[:16]
    with _preview_lock:  # one at a time: a second request waits and then finds the pictures already made
        mark = out / "key.txt"
        fresh = mark.exists() and mark.read_text(encoding="utf-8") == key and all((out / f"{x}.jpg").exists() for x in ("open", "mid", "end"))
        if not fresh:
            mark.unlink(missing_ok=True)
            preview_stills(d, cfg, out)
            mark.write_text(key, encoding="utf-8")
    return {"v": key, "clip": f"{d.parent.parent.name}/{d.name}", "title": state.get("title") or state.get("hook") or d.name, "of": len(found),
            "has_hook": bool(state.get("hook")), "seconds": float(__import__("clipper.render", fromlist=["PREVIEW_SECONDS"]).PREVIEW_SECONDS)}


def clip_view(d: Path, weights: dict | None = None, brief=None, cfg=None) -> dict:
    """What the page needs to draw one clip."""
    from . import scoring

    c = read_json(d / "clip.json")
    video = "final.mp4" if (d / "final.mp4").exists() else ("source.mp4" if (d / "source.mp4").exists() else "")
    out = {k: c.get(k) for k in (
        "channel", "start_s", "end_s", "cut_start_s", "cut_end_s", "trigger", "score", "activity", "layout", "layout_reason",
        "transcript", "hook", "hook_b", "intro", "intro_b", "title", "title_b", "description", "tags", "explainer", "disclosure",
        "ai_keep", "ai_reason", "ai_rating", "error", "notes", "stream_title", "copy_source", "copy_problem", "ai_pick", "part", "parts",
    )}
    out.update(
        key=f"{d.parent.parent.name}/{c['id']}",  # work folder / clip id: unique across streams
        status=c.get("status", "candidate"), video=video, points=scoring.combined(c, weights), made=videos_made(d, c),
        videos=[n for n in ("final.mp4", "final_b.mp4", "explained.mp4", "explained_b.mp4", "source.mp4") if (d / n).exists()],
        fetch_start_s=c.get("fetch_start_s", 0), has_words=(d / "words.json").exists(),
        exported_name=Path(c["exported"]).name if c.get("exported") else "",
        cover=__import__("clipper.thumbs", fromlist=["cover_info"]).cover_info(d, c),
        simple=bool(c.get("simple", True)), held=bool(c.get("held")) and c.get("status", "candidate") == "candidate",
        # the finished files of this clip that are still in the ready folder, so they can be found by name
        files=[Path(p).name for p in (c.get("exports") or {"a": c.get("exported")}).values() if p and Path(p).exists()],
        made_at=_made_at(d, c), decided_at=c.get("decided_at") or "", platform=c.get("platform") or "", live=bool(c.get("live")),
        framing=_framing(c, cfg), said=float((c.get("signals") or {}).get("said") or 0), words_from=c.get("words_from") or "",
        campaign=brief.name if brief else "", campaign_issues=_campaign_issues(c, brief), ai_judged=bool(c.get("copy_at")), exported=bool(c.get("exported")),
        lo=c.get("fetch_start_s", c["start_s"]), hi=c.get("fetch_end_s", c["end_s"]),
        # two choices that belong to the clip: whether it comes out as A and B, and what its thumbnail must leave out
        ab=bool((cfg.render.ab_versions if cfg is not None else True) if c.get("ab") is None else c.get("ab")),
        cover_avoid=str(c.get("cover_avoid") or ""),
        # Changes whenever the clip's data or its draft video changes, so the page knows to redraw it.
        mtime=int(max((d / n).stat().st_mtime for n in ("clip.json", video) if n) * 1000),
    )
    return out


def clean_box(box, label: str) -> list[float]:
    """A box from the page as four fractions (x, y, width, height) inside the picture; a clear refusal otherwise."""
    try:
        x, y, w, h = (float(v) for v in box)
    except (TypeError, ValueError):
        raise ValueError(f"the {label} box must be four numbers: x, y, width, height") from None
    if not (0 <= x <= 0.96 and 0 <= y <= 0.96 and 0.04 <= w <= 1 and 0.04 <= h <= 1):
        raise ValueError(f"the {label} box is too small or outside the picture")
    w, h = min(w, 1 - x), min(h, 1 - y)
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def set_framing(d: Path, body: dict, cfg) -> dict:
    """Save the face and video boxes chosen on the page for one clip and make its draft again with them.

    ``auto`` goes back to automatic framing. ``streamer`` also keeps the boxes for this streamer's new clips.
    Returns the other clips of the same video that ``others`` asked to get the same boxes (their drafts are remade by the caller).
    """
    from . import layout as layout_mod
    from .render import render_clip

    from .work import update_clip

    work, state = d.parent.parent, read_json(d / "clip.json")
    if not (d / "source.mp4").exists():
        raise ValueError("this clip has no downloaded video yet, so there is nothing to frame")
    channel = str(state.get("channel") or "")
    if body.get("auto"):
        for k in ("layout", "facecam", "game", "crop_x", "face_corner", "face_size"):
            state.pop(k, None)
        state["layout_auto"] = True
        layout_mod.save_framing(channel, None, cfg)
        state = layout_mod.auto_layout(d, state, cfg)  # looks at the video: takes a few seconds
        keys = ("layout", "facecam", "game", "crop_x", "face_corner", "face_size", "layout_auto", "layout_reason")
        state = update_clip(d / "clip.json", {k: state[k] for k in keys if k in state}, drop=tuple(k for k in keys if k not in state))
        edit = None
    else:
        from .render import LAYOUTS

        if body.get("layout") not in LAYOUTS:
            raise ValueError("choose what the clip shows")
        has_face = body["layout"] not in ("crop", "blur")
        if not body.get("game") or (has_face and not body.get("facecam")):
            raise ValueError("a box is missing")
        edit = {"layout": body["layout"], "game": body["game"]}
        if has_face:
            edit["facecam"] = body["facecam"]
        if body["layout"] in ("pip", "circle"):
            edit.update(face_corner=body.get("face_corner") or "top-right", face_size=body.get("face_size") or 0.36)
        state = apply_edit(work, d.name, edit)
        state["layout_auto"] = False
        state["layout_reason"] = "the whole picture, chosen by you" if body["layout"] == "blur" else "boxes set by you"
        write_json(d / "clip.json", state)
        if body.get("streamer") and channel:
            layout_mod.save_framing(channel, {**state, "source": [state.get("source_width"), state.get("source_height")]}, cfg)
    try:
        for name in RENDERS:
            remove(d / name)
        render_clip(d, cfg)
    except Exception as e:
        if not edit:
            raise
        # The boxes are on the clip already; saying "not saved" would be untrue and the person would set them again.
        log.error("clip %s: its boxes were saved, but its draft could not be made again: %s", d.name, e, exc_info=not isinstance(e, ValueError))
        raise ValueError(f"the boxes are saved, but the draft could not be made again with them: {e}. "
                         "Press \"Make this video's drafts again\" on the clip once that is sorted out") from e
    others: list[Path] = []
    skipped: list[str] = []
    if edit and body.get("others"):
        for o in clip_dirs(work):
            st = read_json(o / "clip.json", {})
            same = (st.get("source_width"), st.get("source_height")) == (state.get("source_width"), state.get("source_height"))
            if o != d and same and (o / "source.mp4").exists() and st.get("status") in ("candidate", "approved"):
                try:
                    apply_edit(work, o.name, edit)
                    for name in RENDERS:
                        remove(o / name)
                    update_clip(o / "clip.json", {"layout_auto": False, "layout_reason": "boxes set by you"})
                    others.append(o)
                except (ValueError, KeyError) as e:  # one clip that cannot take the boxes must not stop the rest
                    log.warning("clip %s: the boxes were not applied: %s", o.name, e)
                    skipped.append(o.name)
    return {"others": others, "skipped": skipped}


def apply_edit(work: Path, cid: str, edit: dict) -> dict:
    """Validate and apply one review edit to a clip's clip.json. Returns the new state.

    Done under the same lock as ``work.update_clip``: a step that finishes this clip's export at the same moment
    (and records where the file went) and an edit made on the page can then never overwrite each other.
    """
    from .work import _update_lock, clip_lock

    # (the path is put together here: asking for the clip's folder would create one for a clip that does not exist)
    with _update_lock, clip_lock(work / "clips" / cid / "clip.json"):  # the second one also holds off the watcher, a separate program
        return _apply_edit(work, cid, edit)


def _apply_edit(work: Path, cid: str, edit: dict) -> dict:
    d = work / "clips" / cid
    path = d / "clip.json"
    if not re.fullmatch(r"[0-9a-z]+", cid) or not path.exists():
        raise KeyError(f"no such clip: {cid}")
    unknown = set(edit) - EDITABLE
    if unknown:
        raise ValueError(f"fields cannot be edited here: {', '.join(sorted(unknown))}")
    if "status" in edit and edit["status"] not in ("candidate", "approved", "rejected"):
        raise ValueError("status must be candidate, approved or rejected")
    from .render import FACE_CORNERS, LAYOUTS

    if edit.get("layout") not in (None, "", *LAYOUTS):
        raise ValueError(f"the layout must be one of: {', '.join(LAYOUTS)}, or empty for automatic")
    if edit.get("face_corner") not in (None, "", *FACE_CORNERS):
        raise ValueError(f"the face corner must be one of: {', '.join(FACE_CORNERS)}")
    if edit.get("face_size") not in (None, ""):
        try:
            edit = {**edit, "face_size": round(min(0.6, max(0.18, float(edit["face_size"]))), 2)}
        except (TypeError, ValueError):
            raise ValueError("the face size must be a number between 0.18 and 0.6") from None
    state = read_json(path)
    edit = dict(edit)
    for name, label in (("facecam", "face"), ("game", "video")):
        if edit.get(name):
            edit[name] = clean_box(edit[name], label)
        elif name in edit:
            edit[name] = None
    if "ab" in edit:
        edit["ab"] = bool(edit["ab"])
    if "cover_avoid" in edit:
        edit["cover_avoid"] = " ".join(str(edit["cover_avoid"] or "").split())[:500]
    if isinstance(edit.get("tags"), str):  # the page sends hashtags as one line of text
        edit["tags"] = ["#" + t.lstrip("#") for t in re.split(r"[\s,]+", edit["tags"]) if t.strip("#")]
    try:
        start = float(edit.get("cut_start_s", state.get("cut_start_s", state["start_s"])))
        end = float(edit.get("cut_end_s", state.get("cut_end_s", state["end_s"])))
        if start != start or end != end:
            raise ValueError
    except (TypeError, ValueError):  # a box left empty arrives as nothing at all
        raise ValueError("type a number of seconds in both the Start and the End box") from None
    lo = float(state.get("fetch_start_s", state["start_s"]))
    hi = float(state.get("fetch_end_s", state["end_s"]))
    # The trimmer's handles move in half seconds; one pulled all the way to an edge can land a fraction outside the
    # video that was downloaded. That is the edge itself, not a mistake.
    if lo - 0.6 <= start < lo:
        start = lo
    if hi < end <= hi + 0.6:
        end = hi
    for name, value in (("cut_start_s", start), ("cut_end_s", end)):
        if name in edit:
            edit[name] = value
    if not (lo <= start < end <= hi):
        raise ValueError(f"cut must stay inside the fetched range {lo:g}-{hi:g} seconds and start before it ends")
    def shown(st: dict) -> tuple:
        """What is burned into the video, compared by meaning: an unset cut is the moment's own, None and "" are the same."""
        return (st.get("hook") or "", (st.get("layout") or "", tuple(st.get("facecam") or ()), tuple(st.get("game") or ()),
                                       st.get("face_corner") or "", st.get("face_size") or 0), bool(st.get("disclosure")),
                round(float(st.get("cut_start_s", st["start_s"])), 2), round(float(st.get("cut_end_s", st["end_s"])), 2))

    before = shown(state)
    text_before = {k: state.get(k) or ("" if k != "tags" else []) for k in TEXT_FIELDS}
    status_before = state.get("status", "candidate")
    for name in ("simple", "held", "explainer", "disclosure"):
        if name in edit:
            edit[name] = bool(edit[name])
    state.update(edit)
    if not state.get("simple", True) and not state.get("explainer"):
        raise ValueError("a clip needs at least one version: tick the normal video or the explainer")
    # Pending is a shelf for clips taken back from Approved; a clip that is approved or rejected again leaves it.
    if state.get("status", "candidate") != "candidate":
        if edit.get("held"):
            raise ValueError("only a clip that is back in review can be put in Pending")
        state.pop("held", None)
    if state.get("status", "candidate") != status_before:
        import time as _time

        now = _time.strftime("%Y-%m-%d %H:%M:%S")
        state["decided_at"] = now
        state["status_log"] = (state.get("status_log") or [])[-9:] + [{"at": now, "from": status_before, "to": state["status"], "by": "you"}]
    # Text a person changed here is theirs: the automatic writer must not replace it later.
    typed = {k for k, v in text_before.items() if (state.get(k) or ("" if k != "tags" else [])) != v}
    if typed:
        state["copy_auto"] = [k for k in state.get("copy_auto") or [] if k not in typed]
    if typed & {"title", "title_b", "description", "tags"}:
        # The finished file is named after the title and its text file holds all four: both are out of date now.
        # The clip goes back to Approved; "Make videos" only copies it again under the new name (nothing is rendered).
        state.pop("exported", None)
    # A choice made by a person is final: automatic layout and cut snapping must not undo it.
    after = shown(state)
    if after[1] != before[1]:
        state["layout_auto"] = False
    if after[3:] != before[3:]:
        state["cut_auto"] = False
    stale: set[str] = set()
    if before != after:  # the rendered files no longer match; render again
        stale |= {"final.mp4", "final_b.mp4", "body.mp4", "explained.mp4", "explained_b.mp4", "intro_card.mp4", "intro_card_b.mp4", "still.png"}
    if "hook_b" in typed:
        stale |= {"final_b.mp4", "explained_b.mp4"}
    if "intro" in typed:
        stale |= {"explained.mp4", "intro_card.mp4", "explained_b.mp4", "intro_card_b.mp4"}
    if "intro_b" in typed:
        stale |= {"explained_b.mp4", "intro_card_b.mp4"}
    for name in stale:
        remove(d / name)  # FileInUse (a ValueError) if it is playing: the page shows why and the edit is not saved
    write_json(path, state, reopen="status" in edit)  # only a press on this page may move a decided clip back to review
    return state


def _campaign_issues(state: dict, brief) -> list[dict]:
    if brief is None:
        return []
    from .post import campaign_check

    try:
        return campaign_check(state, brief)
    except Exception as e:  # a clip the check cannot read still shows up, with the reason
        return [{"level": "fail", "message": f"the campaign check could not run on this clip: {e}"}]


def current_brief(cfg):
    """The campaign chosen on the page, loaded; None when there is none or its file is broken (the page shows that)."""
    if cfg is None or not cfg.render.campaign:
        return None
    from .brief import load_brief
    from .config import PROJECT_ROOT

    try:
        return load_brief(PROJECT_ROOT / "briefs" / f"{cfg.render.campaign}.yaml")
    except Exception as e:
        log.warning("the chosen campaign %r could not be loaded: %s", cfg.render.campaign, e)
        return None


def list_ranges(cfg) -> list[dict]:
    """The downloaded parts waiting on the cutting board, newest first."""
    out = []
    for note in (cfg.data_dir / "work").glob("*/ranges/*/range.json"):
        info = read_json(note, {})
        video = note.with_name("range.mp4")
        if not info.get("ready") or not video.exists():
            continue
        work = note.parent.parent.parent
        if not info.get("duration_s") or info.get("id") != note.parent.name:
            continue  # damaged notes: not offered (the folder can be deleted by hand)
        made = sum(1 for c in clip_dirs(work) if (s := read_json(c / "clip.json", {})).get("range_id") == info["id"] and s.get("status") != "deleted")
        out.append({**{k: info.get(k) for k in ("id", "start_s", "end_s", "duration_s", "width", "height", "title", "channel", "platform")},
                    "key": f"{work.name}/{info['id']}", "mb": round(video.stat().st_size / 1e6), "clips": made,
                    "mtime": int(video.stat().st_mtime * 1000)})
    return sorted(out, key=lambda r: -r["mtime"])


def wave_peaks(d: Path, cfg, bins: int = 480, name: str = "source.mp4") -> dict:
    """The sound of a clip (or a downloaded part) as ``bins`` loudness values between 0 and 1 over its whole length."""
    cache = d / "wave.json"
    src = d / name
    if cache.exists() and cache.stat().st_mtime >= src.stat().st_mtime:
        return read_json(cache)
    import subprocess

    import numpy as np

    from .tools import find_tool

    raw = subprocess.run([find_tool("ffmpeg", cfg.data_dir), "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, stdin=subprocess.DEVNULL, timeout=300).stdout
    x = np.abs(np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32))
    if len(x) < bins:
        raise ValueError("this clip has no sound to draw")
    step = len(x) // bins
    peaks = x[: step * bins].reshape(bins, step).max(axis=1)
    top = float(peaks.max()) or 1.0
    out = {"seconds": round(len(x) / 8000, 2), "peaks": [round(float(p) / top, 3) for p in peaks]}
    write_json(cache, out)
    return out


RENDERS = ("final.mp4", "final_b.mp4", "body.mp4", "explained.mp4", "explained_b.mp4", "intro_card.mp4", "intro_card_b.mp4", "still.png")


def save_words(d: Path, words: list) -> int:
    """Replace the caption words of a clip (times stay relative to source.mp4). Returns how many are left."""
    clean: list[dict] = []
    for w in words:
        text = str(w.get("word", "")).strip()
        start, end = float(w["start"]), float(w["end"])
        if not text:
            continue  # an emptied word is a deleted word
        if not (0 <= start <= end):
            raise ValueError(f"a caption word has an impossible time ({start}-{end})")
        clean.append({**{k: v for k, v in w.items() if k not in ("word", "start", "end")}, "word": text, "start": start, "end": end})
    clean.sort(key=lambda w: w["start"])
    for name in RENDERS:  # captions are burned into every render; if one is in use, nothing has been changed yet
        remove(d / name)
    write_json(d / "words.json", clean)
    from .work import update_clip

    update_clip(d / "clip.json", {"transcript": " ".join(w["word"] for w in clean), "words_edited": True})
    return len(clean)


class ClipInUse(ValueError):
    """A clip's file is open in another program, so it cannot be deleted yet."""


def delete_clip(d: Path) -> float:
    """Delete a clip: every file in its folder goes, megabytes freed are returned.

    clip.json stays, marked "deleted", so the page hides the clip and no later run makes it again.
    Finished videos already exported to the ready folder are yours and are not touched.
    """
    path = d / "clip.json"
    # A clip whose own record is damaged can still be deleted: that is the one thing a person can do about it on the page.
    state = read_json(path, {})
    state.setdefault("id", d.name)
    freed = 0
    busy: list[str] = []
    # All or nothing. Every file is first set aside under another name, which Windows refuses for a file that is open
    # (a job is still working on this clip, or its video is playing). If one refuses, the others get their names back
    # and the clip is left whole; only when all could be set aside are they removed.
    aside: list[tuple[Path, Path]] = []
    for f in sorted((f for f in d.iterdir() if f.is_file() and f.name != "clip.json"), key=lambda f: f.suffix != ".mp4"):
        if f.name.endswith(".lock"):
            continue  # another program is writing this clip's record this instant; its lock is not the clip's
        if f.name.endswith(".deleting"):  # left by a deletion that was interrupted
            aside.append((f, f))
            continue
        try:
            f.rename(f.with_name(f.name + ".deleting"))
            aside.append((f, f.with_name(f.name + ".deleting")))
        except FileNotFoundError:
            continue  # a job renamed it away this instant
        except FileExistsError:
            aside.append((f, f))
        except PermissionError:
            busy.append(f.name)
            break
    if busy:
        for f, moved in aside:
            if moved != f:
                try:
                    moved.rename(f)
                except OSError as e:
                    log.error("clip %s: %s could not get its name back after a refused delete: %s", d.name, f.name, e)
    else:
        for _f, moved in aside:
            try:
                size = moved.stat().st_size
                remove(moved, wait_s=0.5)
                freed += size
            except FileNotFoundError:
                continue
            except FileInUse:
                log.warning("clip %s: %s could not be removed yet; it goes the next time this clip is deleted or cleaned up", d.name, moved.name)
    if busy:
        raise ClipInUse(f"this clip cannot be deleted right now: its file {busy[0]} is in use. A job is still working on it "
                        "(press Stop where you started it, or wait for it to end), or its video is playing. Then delete it again.")
    import time as _time

    now = _time.strftime("%Y-%m-%d %H:%M:%S")
    state["status_log"] = (state.get("status_log") or [])[-9:] + [{"at": now, "from": state.get("status", "candidate"), "to": "deleted", "by": "you"}]
    state["status"], state["decided_at"] = "deleted", now
    state.pop("error", None)
    write_json(path, state)
    return freed / 1e6


def list_clips(works: list[Path], weights: dict | None = None) -> list[Path]:
    """Clip folders of the given streams: waiting ones first, best score first; then approved, then rejected (newest first)."""
    from . import scoring

    order = {"candidate": 0, "approved": 1, "rejected": 2}
    dirs = [d for w in works for d in clip_dirs(w) if read_json(d / "clip.json", {}).get("status") != "deleted"]

    def key(d: Path):
        state = read_json(d / "clip.json", {})
        status = state.get("status", "candidate")
        # A draft still being made (no video yet) goes after the finished ones, so the top of the list is always watchable.
        ready = (d / "final.mp4").exists() or (d / "source.mp4").exists()
        if status == "candidate" and state.get("trigger") == "manual":
            # Clips you cut yourself come first and stay in their own order: part 1, part 2, part 3.
            return (0, -1, -10**6, d.parent.parent.name, float(state.get("peak_s") or 0))
        first = (-int(ready), -scoring.combined(state, weights)) if status == "candidate" else (int(videos_made(d, state)), 0)
        return (order.get(status, 0), *first, "", -(d / "clip.json").stat().st_mtime)

    return sorted(dirs, key=key)


def all_states(works: list[Path]) -> list[dict]:
    """Every clip's state, including deleted ones (a deletion is a decision too)."""
    return [read_json(d / "clip.json", {}) for w in works for d in clip_dirs(w)]


def serve(works: list[Path], title: str, port: int = 8765, cfg=None, every_stream: bool = False, open_browser: bool = False) -> int:
    """Serve the review page for one or more stream work folders (all under the same parent).

    With ``cfg`` the page also gets the options panel (watch, versions, finish).
    ``every_stream`` re-reads the list of streams on each request, so streams the
    watcher starts while the page is open show up.
    """
    by_name = {w.name: w for w in works}
    broken_said: set[str] = set()  # damaged clips already reported in the terminal
    control = None
    if cfg is not None:
        from .control import Control

        control = Control(cfg)

    def refresh() -> None:
        if every_stream and cfg is not None:
            from .pipeline import all_works

            # Add first, then take away what is gone: the list is never empty for another request in between.
            now = {w.name: w for w in all_works(cfg)}
            by_name.update(now)
            for gone in [name for name in list(by_name) if name not in now]:
                by_name.pop(gone, None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # keep the terminal quiet
            log.debug("review: " + fmt, *args)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _foreign(self) -> bool:
            """Was this request sent to another name than this computer's own? A web page elsewhere can point its own
            name at 127.0.0.1 and then read this page's answers (its browser thinks it is still talking to that site).
            Such a request names that site as Host; ours names 127.0.0.1 or localhost."""
            host = (self.headers.get("Host") or "").strip().lower()
            name = host.rsplit(":", 1)[0] if host.count(":") == 1 or host.startswith("[") else host
            if name in ("127.0.0.1", "localhost", "[::1]"):
                return False
            self._send(403, b"this page only answers on this computer's own address (127.0.0.1)", "text/plain")
            return True

        def do_GET(self) -> None:
            if self._foreign():
                return
            if self.path == "/api/options" and control:
                return self._send(200, json.dumps({**control.status(), "stamp": RUNNING_STAMP}).encode(), "application/json")
            refresh()
            if self.path == "/":
                return self._send(200, page_html(title, bool(control)).encode("utf-8"), "text/html; charset=utf-8")
            if self.path == "/api/clips":
                from . import scoring

                use_cfg = control.cfg if control else cfg
                weights = scoring.mixed(scoring.load_weights(cfg.data_dir), use_cfg.moments.mix) if cfg is not None else None
                brief = current_brief(control.cfg if control else cfg)
                views = []
                for d in list_clips(list(by_name.values()), weights):
                    try:
                        views.append(clip_view(d, weights, brief, control.cfg if control else cfg))
                        broken_said.discard(str(d))  # it reads again: a later fault on it is said anew
                    except Exception as e:  # one unreadable clip must not blank the whole page
                        if str(d) not in broken_said:  # once, not on every look of the page
                            broken_said.add(str(d))
                            log.error("clip %s could not be shown: %s", d, e, exc_info=not isinstance(e, ValueError))
                        # ...and it must not vanish without a word either: it is shown as a card that says what is wrong.
                        views.append({"key": f"{d.parent.parent.name}/{d.name}", "status": "candidate", "broken": True, "channel": "", "title": "",
                                      "error": "This clip's own record is damaged and cannot be read, most likely because the computer was switched off while "
                                               "it was being written. The clip cannot be shown or changed. Press Delete to remove it. "
                                               f"(Its folder is {d})"})
                return self._send(200, json.dumps(views).encode("utf-8"), "application/json")
            if self.path.split("?")[0] in ("/api/tracker", "/api/tracker.csv") and cfg is not None:
                from . import tracker

                rows = tracker.load(cfg.data_dir)
                if self.path.startswith("/api/tracker.csv"):
                    self.send_response(200)
                    body = tracker.to_csv(rows).encode("utf-8-sig")
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="stream-clipper-tracker.csv"')
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                data = {"rows": rows, "totals": tracker.totals(rows), "platforms": tracker.PLATFORMS, "statuses": tracker.STATUSES}
                return self._send(200, json.dumps(data).encode("utf-8"), "application/json")
            m = re.fullmatch(r"/api/clip/([A-Za-z0-9_-]+)/([0-9a-z]+)/captions\.(srt|vtt)", self.path.split("?")[0])
            if m and m.group(1) in by_name and cfg is not None:
                from . import words as words_mod

                d = by_name[m.group(1)] / "clips" / m.group(2)
                if not (d / "words.json").exists():
                    return self._send(404, b"this clip has no captions yet", "text/plain")
                cues = words_mod.clip_cues(d, cfg)
                body = (words_mod.to_srt(cues) if m.group(3) == "srt" else words_mod.to_vtt(cues)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/x-subrip; charset=utf-8" if m.group(3) == "srt" else "text/vtt; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{m.group(1)}_{m.group(2)}.{m.group(3)}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            m = re.fullmatch(r"/api/clip/([A-Za-z0-9_-]+)/([0-9a-z]+)/(wave|words)", self.path.split("?")[0])
            if m and m.group(1) in by_name:
                d = by_name[m.group(1)] / "clips" / m.group(2)
                try:
                    if m.group(3) == "wave":
                        if not (d / "source.mp4").exists():
                            return self._send(404, b"this clip has no downloaded video yet", "text/plain")
                        data = wave_peaks(d, cfg)
                    else:
                        data = read_json(d / "words.json", [])
                except Exception as e:
                    log.error("clip %s: %s could not be read: %s", d.name, m.group(3), e, exc_info=True)
                    return self._send(500, str(e).encode(), "text/plain")
                return self._send(200, json.dumps(data).encode(), "application/json")
            if self.path == "/api/ranges" and cfg is not None:
                return self._send(200, json.dumps(list_ranges(cfg)).encode("utf-8"), "application/json")
            m = re.fullmatch(r"/api/range/([A-Za-z0-9_-]+)/([0-9a-z-]+)/wave", self.path.split("?")[0])
            if m and m.group(1) in by_name:
                d = by_name[m.group(1)] / "ranges" / m.group(2)
                if not (d / "range.mp4").exists():
                    return self._send(404, b"this downloaded part is not there any more", "text/plain")
                try:
                    data = wave_peaks(d, cfg, bins=1200, name="range.mp4")
                except Exception as e:
                    log.error("downloaded part %s: its sound could not be read: %s", d.name, e, exc_info=True)
                    return self._send(500, str(e).encode(), "text/plain")
                return self._send(200, json.dumps(data).encode(), "application/json")
            if self.path.split("?")[0] == "/api/look/preview" and cfg is not None:
                from .tools import ToolError

                try:
                    n = int((re.search(r"[?&]n=(\d{1,3})", self.path) or [0, 0])[1])
                    return self._send(200, json.dumps(look_preview(list(by_name.values()), control.reload() if control else cfg, n)).encode(), "application/json")
                except (ValueError, ToolError) as e:
                    return self._send(400, str(e).encode("utf-8", "replace"), "text/plain")
                except Exception as e:
                    log.error("the preview for the Look and branding page could not be made: %s", e, exc_info=True)
                    return self._send(500, f"{type(e).__name__}: {e}".encode("utf-8", "replace"), "text/plain")
            m = re.fullmatch(r"/api/look/preview/(open|mid|end)\.jpg", self.path.split("?")[0])
            if m and cfg is not None:
                f = Path((control.cfg if control else cfg).data_dir) / "tmp" / "look-preview" / f"{m.group(1)}.jpg"
                if not f.exists():
                    return self._send(404, b"no preview has been made yet", "text/plain")
                return self._send(200, f.read_bytes(), "image/jpeg")
            m = re.fullmatch(r"/media/([A-Za-z0-9_-]+)/([0-9a-z]+)/cover\.jpg", self.path.split("?")[0])
            if m and m.group(1) in by_name:
                f = by_name[m.group(1)] / "clips" / m.group(2) / "cover.jpg"
                if not f.exists():
                    return self._send(404, b"this clip has no thumbnail yet", "text/plain")
                return self._send(200, f.read_bytes(), "image/jpeg")
            m = re.fullmatch(r"/media/([A-Za-z0-9_-]+)/([0-9a-z]+)/(final|final_b|explained|explained_b|source)\.mp4", self.path.split("?")[0])
            rm = re.fullmatch(r"/media/([A-Za-z0-9_-]+)/([0-9a-z]+-[0-9a-z]+)/range\.mp4", self.path.split("?")[0])
            if rm and rm.group(1) in by_name:
                f = by_name[rm.group(1)] / "ranges" / rm.group(2) / "range.mp4"
            elif not m or m.group(1) not in by_name:
                return self._send(404, b"not found", "text/plain")
            else:
                f = by_name[m.group(1)] / "clips" / m.group(2) / f"{m.group(3)}.mp4"
            if not f.exists():
                return self._send(404, b"not found", "text/plain")
            size = f.stat().st_size
            start, end = 0, size - 1
            rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "") or "")
            if rng and (rng.group(1) or rng.group(2)):  # browsers need byte ranges to seek in a video
                if rng.group(1):
                    start = int(rng.group(1))
                    end = min(int(rng.group(2)), size - 1) if rng.group(2) else size - 1
                else:
                    start = max(0, size - int(rng.group(2)))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                at, left = start, end - start + 1
                while left > 0:
                    # Opened for each piece only: a browser can keep a video's connection waiting for minutes, and a file
                    # held open that long could not be replaced or deleted (Windows), so edits and deletes would be refused.
                    with f.open("rb") as fh:
                        fh.seek(at)
                        chunk = fh.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    at += len(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionError):
                pass  # the browser stopped the download (seek or page close)
            except OSError as e:
                log.info("stopped sending %s: %s", f.name, e)  # the file was replaced or deleted while it was being watched

        def do_POST(self) -> None:
            # Only this page may give orders: a request sent by some other website carries its own Origin.
            if self._foreign():
                return
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                return self._send(403, b"not allowed from another site", "text/plain")
            try:
                if int(self.headers.get("Content-Length", "0") or 0) < 0:
                    raise ValueError
            except ValueError:
                return self._send(400, b"the request's length is not a valid number", "text/plain")
            if int(self.headers.get("Content-Length", "0") or 0) > 30_000_000 and not self.path.startswith(("/api/upload", "/api/brand/upload")):
                return self._send(400, b"that is far more than this page ever sends; refused", "text/plain")
            actions = {"/api/options": "save", "/api/watch/start": "start_watch", "/api/watch/stop": "stop_watch", "/api/watch/clip-now": "clip_now",
                       "/api/finish": "start_finish", "/api/finish/stop": "stop_finish", "/api/ready/open": "open_ready", "/api/run": "start_run", "/api/run/stop": "stop_run", "/api/cut": "start_cut", "/api/range": "start_range", "/api/range/cut": "start_range_cut", "/api/ai/check": "start_ai_check", "/api/ai/local-setup": "start_local_ai",
                       "/api/health/check": "start_full_check", "/api/health/repair": "start_repair", "/api/health/update": "start_update",
                       "/api/setup/install": "setup_install", "/api/setup/signin": "setup_signin", "/api/setup/refresh": "setup_refresh", "/api/setup/finish": "setup_finish",
                       "/api/restart": "restart"}
            if self.path.startswith("/api/brand/upload") and cfg is not None and control:
                # Your logo or music file: kept under the data folder, then set as the one to use.
                from urllib.parse import parse_qs, urlparse

                q = parse_qs(urlparse(self.path).query)
                kind = (q.get("kind") or [""])[0]
                ext = Path((q.get("name") or [""])[0]).suffix.lower()
                allowed = {"logo": (".png", ".jpg", ".jpeg", ".webp", ".gif"), "music": (".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac"),
                           "card": (".png", ".jpg", ".jpeg", ".webp")}
                if kind not in allowed or ext not in allowed[kind]:
                    return self._send(400, ("a logo must be an image (png, jpg, webp, gif); music must be a sound file (mp3, m4a, wav, ogg)").encode(), "text/plain")
                left = int(self.headers.get("Content-Length", "0"))
                if not 0 < left <= 60_000_000:
                    return self._send(400, b"the file is empty or larger than 60 MB", "text/plain")
                folder = cfg.data_dir / "brand"
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / f"{kind}{ext}"
                data = self.rfile.read(left)
                if len(data) != left:
                    return self._send(400, b"the file did not arrive completely; try again", "text/plain")
                arriving = folder / f"{kind}.arriving"
                arriving.write_bytes(data)
                for old in folder.glob(f"{kind}.*"):
                    if old != arriving:
                        old.unlink(missing_ok=True)
                arriving.replace(target)
                result = control.save({"card_image": str(target), "card_bg": "image"} if kind == "card" else {f"brand_{kind}": str(target)})
                return self._send(200, json.dumps({**result, "stamp": RUNNING_STAMP}).encode(), "application/json")
            if self.path.startswith("/api/upload") and cfg is not None:
                # A video file chosen on the page is saved under the data folder, piece by piece, and its path is given back.
                from urllib.parse import parse_qs, urlparse

                name = re.sub(r"[^A-Za-z0-9 ._()-]+", "_", (parse_qs(urlparse(self.path).query).get("name") or ["video.mp4"])[0])[-120:] or "video.mp4"
                if not name.lower().endswith((".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts", ".flv")):
                    return self._send(400, b"that does not look like a video file (mp4, mkv, mov, webm, avi)", "text/plain")
                folder = cfg.data_dir / "uploads"
                folder.mkdir(parents=True, exist_ok=True)
                target, left = folder / name, int(self.headers.get("Content-Length", "0"))
                if left <= 0:
                    return self._send(400, b"the file arrived empty", "text/plain")
                # Another video under a name used before gets a name of its own: a video is known by its path, so it
                # would otherwise be taken for the earlier one (its length, its clips, its downloaded parts).
                # The same file sent again (same size) keeps its name, so its clips stay with it.
                n = 1
                while target.exists() and target.stat().st_size != left:
                    n += 1
                    target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
                from .work import free_gb

                if free_gb(cfg.data_dir) < left / 1e9 + 2:
                    return self._send(400, b"not enough free disk space for this file", "text/plain")
                part = target.with_name(target.name + ".part")
                with part.open("wb") as fh:
                    while left > 0:
                        chunk = self.rfile.read(min(1024 * 1024, left))
                        if not chunk:
                            break
                        fh.write(chunk)
                        left -= len(chunk)
                if left:
                    part.unlink(missing_ok=True)
                    return self._send(400, b"the file did not arrive completely; try again", "text/plain")
                part.replace(target)
                log.info("video file received: %s (%.0f MB)", target, target.stat().st_size / 1e6)
                return self._send(200, json.dumps({"path": str(target)}).encode("utf-8"), "application/json")
            if control and self.path in actions:
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                    name = actions[self.path]
                    result = getattr(control, name)(body) if name in ("save", "start_watch", "start_run", "start_cut", "start_range", "start_range_cut", "clip_now", "start_finish", "stop_finish", "setup_install", "setup_signin", "setup_finish") else getattr(control, name)()
                except (ValueError, TypeError) as e:
                    return self._send(400, str(e).encode(), "text/plain")
                if isinstance(result, dict) and "watching" in result:
                    result = {**result, "stamp": RUNNING_STAMP}  # the same answer the page gets when it polls
                return self._send(200, json.dumps(result).encode(), "application/json")
            refresh()
            m = re.fullmatch(r"/api/tracker/(add|update|delete|refresh)", self.path)
            if m and cfg is not None:
                from . import tracker

                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                    if m.group(1) == "add":
                        result = tracker.add(cfg.data_dir, body, allow_duplicate=bool(body.get("allow_duplicate")))
                    elif m.group(1) == "update":
                        result = tracker.update(cfg.data_dir, str(body.get("id")), body.get("changes") or {})
                    elif m.group(1) == "delete":
                        result = {"deleted": tracker.delete(cfg.data_dir, str(body.get("id")))}
                    else:
                        result = tracker.refresh_views(cfg.data_dir, cfg)
                except KeyError as e:
                    return self._send(404, str(e).encode(), "text/plain")
                except (ValueError, TypeError) as e:
                    return self._send(400, str(e).encode(), "text/plain")
                return self._send(200, json.dumps(result).encode("utf-8"), "application/json")
            m = re.fullmatch(r"/api/range/([A-Za-z0-9_-]+)/([0-9a-z]+-[0-9a-z]+)/delete", self.path)
            if m and m.group(1) in by_name:
                import shutil

                d = by_name[m.group(1)] / "ranges" / m.group(2)
                if control and control.running():
                    return self._send(400, b"a job is still running; wait for it to end, then delete this download", "text/plain")
                mb = sum(f.stat().st_size for f in d.glob("*") if f.is_file()) / 1e6 if d.is_dir() else 0
                try:
                    remove(d / "range.mp4", wait_s=3)  # the video first: if it is in use, the notes stay and it is still listed
                except FileInUse:
                    return self._send(400, b"this download is in use right now (its video is still open on the page, or a job is cutting "
                                           b"from it). Reload the page or wait, then delete it again", "text/plain")
                shutil.rmtree(d, ignore_errors=True)
                log.info("deleted the downloaded part %s/%s, %.0f MB freed", m.group(1), m.group(2), mb)
                return self._send(200, json.dumps({"deleted": 1, "mb": round(mb)}).encode(), "application/json")
            if self.path == "/api/help" and cfg is not None:
                from . import help as help_mod

                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 400_000)) or b"{}")
                result = help_mod.answer(control.cfg if control else cfg, body.get("question"), body.get("history"), body.get("targets"), body.get("state"))
                return self._send(200, json.dumps(result).encode("utf-8"), "application/json")
            if self.path in ("/api/words", "/api/words/answers") and cfg is not None:
                from . import words as words_mod

                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                use = control.cfg if control else cfg
                if self.path == "/api/words/answers":
                    return self._send(200, json.dumps(words_mod.parse_answers(str(body.get("text") or ""), float(body.get("duration_s") or 0))).encode("utf-8"), "application/json")
                from .sources import parse_source

                parse_source(str(body.get("url") or ""))  # a plain refusal for anything that is not a video
                data = words_mod.video_lines(str(body.get("url")), use)
                data["parts"] = words_mod.chatbot_parts(data["lines"], data["title"])
                return self._send(200, json.dumps(data).encode("utf-8"), "application/json")
            m = re.fullmatch(r"/api/clip/([A-Za-z0-9_-]+)/([0-9a-z]+)/captions-import", self.path)
            if m and m.group(1) in by_name and cfg is not None:
                from . import words as words_mod
                from .render import cut_window

                d = by_name[m.group(1)] / "clips" / m.group(2)
                if not (d / "clip.json").exists():
                    return self._send(404, b"no such clip", "text/plain")
                text = self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 5_000_000)).decode("utf-8", errors="replace")
                cues = words_mod.parse_subtitles(text)
                offset, duration = cut_window(read_json(d / "clip.json"))
                cues = [c for c in cues if c[0] < duration]
                if not cues:
                    raise ValueError("every subtitle in that file starts after this clip ends. Its times must count from the start of the clip")
                count = save_words(d, words_mod.words_from_cues(cues, shift_s=offset))  # a clip's words are timed from its download's start
                return self._send(200, json.dumps({"words": count, "lines": len(cues)}).encode(), "application/json")
            if self.path == "/api/clips/hold-approved":
                # Every approved clip whose videos are not made yet goes to Pending: out of "Make videos", and not mixed into To review.
                moved, stuck = 0, []
                for d in list_clips(list(by_name.values())):
                    st = read_json(d / "clip.json", {})
                    if st.get("status") == "approved" and not videos_made(d, st):
                        try:
                            apply_edit(d.parent.parent, d.name, {"status": "candidate", "held": True})
                            moved += 1
                        except (ValueError, KeyError) as e:  # one clip that cannot be moved must not stop the others
                            log.error("clip %s could not be moved to Pending: %s", d.name, e)
                            stuck.append(f"{st.get('title') or d.name}: {e}")
                if moved and cfg is not None:
                    from . import scoring

                    scoring.relearn(cfg.data_dir, all_states(list(by_name.values())))
                log.info("moved %d approved clip(s) to Pending", moved)
                return self._send(200, json.dumps({"moved": moved, "stuck": stuck}).encode(), "application/json")
            if self.path == "/api/clips/delete-rejected":
                gone, mb, busy = 0, 0.0, 0
                for d in list_clips(list(by_name.values())):
                    if read_json(d / "clip.json", {}).get("status") == "rejected":
                        try:
                            mb += delete_clip(d)
                            gone += 1
                        except ClipInUse as e:
                            busy += 1
                            log.warning("clip %s: %s", d.name, e)
                log.info("deleted %d rejected clip(s), %.0f MB freed", gone, mb)
                if busy and not gone:
                    return self._send(400, f"{busy} rejected clip(s) are in use right now and could not be deleted; stop the running job or wait, then try again".encode(), "text/plain")
                return self._send(200, json.dumps({"deleted": gone, "mb": round(mb), "busy": busy}).encode(), "application/json")
            m = re.fullmatch(r"/api/clip/([A-Za-z0-9_-]+)/([0-9a-z]+)/(words|render-b)", self.path)
            if m and m.group(1) in by_name and cfg is not None:
                d = by_name[m.group(1)] / "clips" / m.group(2)
                if not (d / "clip.json").exists():
                    return self._send(404, b"no such clip", "text/plain")
                try:
                    if m.group(3) == "words":
                        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                        return self._send(200, json.dumps({"words": save_words(d, body.get("words") or [])}).encode(), "application/json")
                    from .render import render_clip

                    now_cfg = control.cfg if control else cfg  # the look chosen on the page, not the one the program started with
                    if not (d / "final.mp4").exists():
                        render_clip(d, now_cfg)
                    render_clip(d, now_cfg, out_name="final_b.mp4", variant="b", force=True)
                    return self._send(200, b"{}", "application/json")
                except (ValueError, TypeError, KeyError) as e:
                    return self._send(400, str(e).encode(), "text/plain")
                except Exception as e:
                    log.error("clip %s: %s failed: %s", d.name, m.group(3), e, exc_info=True)
                    return self._send(500, f"{type(e).__name__}: {e}".encode(), "text/plain")
            m = re.fullmatch(r"/api/clip/([A-Za-z0-9_-]+)/([0-9a-z]+)/cover", self.path)
            if m and m.group(1) in by_name and cfg is not None:
                # A thumbnail for this one clip: from the clip itself, drawn by ChatGPT, or from the person's own description.
                from . import thumbs
                from .tools import ToolError
                from .work import update_clip

                d = by_name[m.group(1)] / "clips" / m.group(2)
                if not (d / "clip.json").exists():
                    return self._send(404, b"no such clip", "text/plain")
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 20_000)) or b"{}")
                if body.get("how") == "none":  # take the thumbnail off again
                    remove(d / "cover.jpg")
                    update_clip(d / "clip.json", {}, drop=("cover_by", "cover_at", "cover_words", "cover_prompt", "cover_error", "cover_error_mode"))
                    return self._send(200, b"{}", "application/json")
                if "avoid" in body:  # what this one clip's picture must leave out, kept with the clip for its next thumbnail too
                    update_clip(d / "clip.json", {"cover_avoid": " ".join(str(body.get("avoid") or "").split())[:500]})
                try:
                    thumbs.make_cover(d, control.cfg if control else cfg, str(body.get("how") or ""), str(body.get("prompt") or ""))
                except (ValueError, ToolError) as e:
                    if isinstance(e, ToolError):
                        log.error("clip %s: the thumbnail could not be made: %s", d.name, e)
                        update_clip(d / "clip.json", {"cover_error": str(e)})
                    return self._send(400, str(e).encode("utf-8", "replace"), "text/plain")
                state = read_json(d / "clip.json")
                if state.get("exported") and Path(state["exported"]).exists():
                    thumbs.export_cover(d, Path(state["exported"]))  # its videos are made already: the picture joins them
                return self._send(200, json.dumps(thumbs.cover_info(d, state)).encode(), "application/json")
            m = re.fullmatch(r"/api/clip/([A-Za-z0-9_-]+)/([0-9a-z]+)/remake-all", self.path)
            if m and m.group(1) in by_name and cfg is not None:
                # Every unfinished clip of this video is drafted again with the look chosen now (caption style, template, branding).
                import threading

                use = control.cfg if control else cfg
                # Not the finished ones (Done): rendering those again would take their videos back to Approved.
                todo = [d for d in clip_dirs(by_name[m.group(1)])
                        if (st := read_json(d / "clip.json", {})).get("status") in ("candidate", "approved") and (d / "source.mp4").exists()
                        and not videos_made(d, st)]
                if not todo:
                    return self._send(400, b"this video has no downloaded clips to make again", "text/plain")

                def remake_all() -> None:
                    from .render import render_clip
                    from .work import update_clip

                    for d in todo:
                        try:
                            for name in RENDERS:
                                if name != "final.mp4":
                                    remove(d / name)
                            render_clip(d, use, force=True)
                            update_clip(d / "clip.json", {}, drop=("error",))
                        except Exception as e:
                            log.error("clip %s: the draft could not be made again: %s", d.name, e, exc_info=not isinstance(e, ValueError))
                            st = read_json(d / "clip.json", {})
                            st["error"] = f"the draft could not be made again: {type(e).__name__}: {e}"
                            write_json(d / "clip.json", st)

                threading.Thread(target=remake_all, daemon=True, name="remake-all").start()
                return self._send(200, json.dumps({"clips": len(todo)}).encode(), "application/json")
            m = re.fullmatch(r"/api/clip/([A-Za-z0-9_-]+)/([0-9a-z]+)/widen", self.path)
            if m and m.group(1) in by_name and cfg is not None:
                # More of the stream before or after this clip, so its start or end can be moved further out.
                from .pipeline import PipelineError, widen_clip
                from .render import render_clip
                from .tools import ToolError

                d = by_name[m.group(1)] / "clips" / m.group(2)
                if not (d / "clip.json").exists():
                    return self._send(404, b"no such clip", "text/plain")
                use = control.cfg if control else cfg
                try:
                    body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 2000)) or b"{}")
                    state = widen_clip(d, use, body.get("before") or 0, body.get("after") or 0)
                except (ValueError, TypeError, KeyError, ToolError, PipelineError) as e:
                    log.error("clip %s: no more of the stream could be added: %s", d.name, e)
                    return self._send(400, str(e).encode("utf-8", "replace"), "text/plain")
                except Exception as e:
                    log.error("clip %s: no more of the stream could be added: %s", d.name, e, exc_info=True)
                    return self._send(500, f"{type(e).__name__}: {e}".encode("utf-8", "replace"), "text/plain")
                try:  # the draft is made again from the new video, so the two never disagree
                    for name in RENDERS:
                        remove(d / name)
                    render_clip(d, use)
                except Exception as e:
                    log.error("clip %s: more of the stream was added, but its draft could not be made again: %s", d.name, e, exc_info=not isinstance(e, ValueError))
                    return self._send(400, ("the seconds were added, but the draft could not be made again: " + str(e)
                                            + ". Press \"Make this video's drafts again\" on the clip").encode("utf-8", "replace"), "text/plain")
                return self._send(200, json.dumps({"lo": state["fetch_start_s"], "hi": state["fetch_end_s"]}).encode(), "application/json")
            m = re.fullmatch(r"/api/clip/([A-Za-z0-9_-]+)/([0-9a-z]+)/framing", self.path)
            if m and m.group(1) in by_name and cfg is not None:
                d = by_name[m.group(1)] / "clips" / m.group(2)
                if not (d / "clip.json").exists():
                    return self._send(404, b"no such clip", "text/plain")
                use = control.cfg if control else cfg
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                    done_ = set_framing(d, body, use)
                    others, skipped = done_["others"], done_.get("skipped", [])
                except (ValueError, TypeError, KeyError) as e:
                    return self._send(400, str(e).encode(), "text/plain")
                except Exception as e:
                    log.error("clip %s: the draft could not be made with the new boxes: %s", d.name, e, exc_info=True)
                    return self._send(500, f"{type(e).__name__}: {e}".encode(), "text/plain")
                if others:
                    import threading

                    def remake() -> None:
                        from .render import render_clip

                        for o in others:
                            try:
                                render_clip(o, use)
                            except Exception as e:
                                log.error("clip %s: the draft could not be made with the new boxes: %s", o.name, e, exc_info=True)
                                st = read_json(o / "clip.json")
                                st["error"] = f"the draft could not be made with the new boxes: {type(e).__name__}: {e}"
                                write_json(o / "clip.json", st)

                    threading.Thread(target=remake, daemon=True).start()
                return self._send(200, json.dumps({"others": len(others), "skipped": skipped}).encode(), "application/json")
            m = re.fullmatch(r"/api/clip/([A-Za-z0-9_-]+)/([0-9a-z]+)/(delete|clear-error)", self.path)
            if m and m.group(1) in by_name:
                d = by_name[m.group(1)] / "clips" / m.group(2)
                if not (d / "clip.json").exists():
                    return self._send(404, b"no such clip", "text/plain")
                if m.group(3) == "delete":
                    try:
                        mb = delete_clip(d)
                    except ClipInUse as e:
                        log.warning("clip %s/%s: %s", m.group(1), m.group(2), e)
                        return self._send(400, str(e).encode(), "text/plain")
                    log.info("deleted clip %s/%s, %.0f MB freed", m.group(1), m.group(2), mb)
                    return self._send(200, json.dumps({"deleted": 1, "mb": round(mb)}).encode(), "application/json")
                state = read_json(d / "clip.json")
                state.pop("error", None)
                write_json(d / "clip.json", state)
                return self._send(200, b"{}", "application/json")
            m = re.fullmatch(r"/api/clip/([A-Za-z0-9_-]+)/([0-9a-z]+)/rewrite", self.path)
            if m and m.group(1) in by_name and cfg is not None:
                from . import llm
                from .pipeline import write_clip_copy

                d = by_name[m.group(1)] / "clips" / m.group(2)
                if not (d / "clip.json").exists():
                    return self._send(404, b"no such clip", "text/plain")
                if not llm.text_available(cfg):
                    return self._send(400, f"AI is not available: {llm.why_unavailable(cfg)}.".encode(), "text/plain")
                state = write_clip_copy(d, read_json(d / "clip.json"), control.cfg if control else cfg, force=True)
                if state.get("copy_source") == "plain":
                    return self._send(400, f"AI could not write the text: {state.get('copy_problem')}".encode(), "text/plain")
                return self._send(200, json.dumps({"status": state.get("status")}).encode(), "application/json")
            m = re.fullmatch(r"/api/clip/([A-Za-z0-9_-]+)/([0-9a-z]+)", self.path)
            if not m or m.group(1) not in by_name:
                return self._send(404, b"not found", "text/plain")
            try:
                edit = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                state = apply_edit(by_name[m.group(1)], m.group(2), edit)
            except KeyError as e:
                return self._send(404, str(e).encode(), "text/plain")
            except (ValueError, TypeError) as e:
                return self._send(400, str(e).encode(), "text/plain")
            if "status" in edit and cfg is not None:
                from . import scoring

                scoring.relearn(cfg.data_dir, all_states(list(by_name.values())))  # every decision teaches the ranking
            self._send(200, json.dumps({"status": state.get("status")}).encode(), "application/json")

    def answered(fn):
        """Whatever goes wrong inside a request, the page gets the reason instead of a dropped connection."""
        def inner(self) -> None:
            try:
                fn(self)
            except (BrokenPipeError, ConnectionError):
                pass  # the browser went away
            except Exception as e:
                log.exception("%s %s failed", self.command, self.path)
                try:
                    # A ValueError is a plain refusal written for a person; anything else is a fault and keeps its name.
                    self._send(400 if isinstance(e, ValueError) else 500, (str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}").encode("utf-8", "replace"), "text/plain")
                except Exception:
                    pass  # the answer had already started; the log has the reason

        return inner

    Handler.do_GET, Handler.do_POST = answered(Handler.do_GET), answered(Handler.do_POST)
    try:
        server = OneAtATimeServer(("127.0.0.1", port), Handler)
    except OSError as e:
        raise ValueError(
            f"the review page cannot start: port {port} is already in use ({e}). It is probably already running in another "
            f"terminal window. Close that one first (Ctrl+C), or start this one on another port:  uv run clipper review --port {port + 1}"
        ) from e
    print(f"Review page: http://127.0.0.1:{port}/   (Ctrl+C to stop)")
    if cfg is not None:
        # Every start, so also after every update: a decision a clip file has lost is put back from the record.
        from .work import restore_decisions

        try:
            back = restore_decisions(cfg.data_dir)
            if back:
                print(f"{back} clip(s) had lost their approved / rejected mark; it was put back from the decisions record.")
        except Exception as e:
            log.error("the decisions record could not be checked: %s", e, exc_info=True)
    if control:
        control.shutdown = server.shutdown
        control.autostart()
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if control:
            control.stop_watch()
            control.stop_run()
    return control.exit_code if control else 0
