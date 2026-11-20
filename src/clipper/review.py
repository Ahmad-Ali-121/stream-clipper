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

EDITABLE = {"status", "hook", "hook_b", "intro", "intro_b", "explainer", "disclosure", "layout", "facecam", "game", "face_corner", "face_size",
            "cut_start_s", "cut_end_s",
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
            .replace("__STAMP__", json.dumps(code_stamp())))


def videos_made(d: Path, state: dict) -> bool:
    """True when an approved clip's videos exist as it stands now: made, not edited since, explainer included if ticked."""
    if state.get("status") != "approved" or not state.get("exported") or not (d / "final.mp4").exists():
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
        made_at=_made_at(d, c), decided_at=c.get("decided_at") or "", platform=c.get("platform") or "", live=bool(c.get("live")),
        framing=_framing(c, cfg), said=float((c.get("signals") or {}).get("said") or 0), words_from=c.get("words_from") or "",
        campaign=brief.name if brief else "", campaign_issues=_campaign_issues(c, brief), ai_judged=bool(c.get("copy_at")), exported=bool(c.get("exported")),
        lo=c.get("fetch_start_s", c["start_s"]), hi=c.get("fetch_end_s", c["end_s"]),
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
    if not (0 <= x < 1 and 0 <= y < 1 and 0.04 <= w <= 1 and 0.04 <= h <= 1):
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
        state = layout_mod.auto_layout(d, state, cfg)
        write_json(d / "clip.json", state)
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
        state["layout_auto"], state["layout_reason"] = False, "boxes set by you"
        write_json(d / "clip.json", state)
        if body.get("streamer") and channel:
            layout_mod.save_framing(channel, {**state, "source": [state.get("source_width"), state.get("source_height")]}, cfg)
    for name in RENDERS:
        remove(d / name)
    render_clip(d, cfg)
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
    """Validate and apply one review edit to a clip's clip.json. Returns the new state."""
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
    if isinstance(edit.get("tags"), str):  # the page sends hashtags as one line of text
        edit["tags"] = ["#" + t.lstrip("#") for t in re.split(r"[\s,]+", edit["tags"]) if t.strip("#")]
    start = float(edit.get("cut_start_s", state.get("cut_start_s", state["start_s"])))
    end = float(edit.get("cut_end_s", state.get("cut_end_s", state["end_s"])))
    lo = float(state.get("fetch_start_s", state["start_s"]))
    hi = float(state.get("fetch_end_s", state["end_s"]))
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
    state.update(edit)
    if state.get("status", "candidate") != status_before:
        import time as _time

        now = _time.strftime("%Y-%m-%d %H:%M:%S")
        state["decided_at"] = now
        state["status_log"] = (state.get("status_log") or [])[-9:] + [{"at": now, "from": status_before, "to": state["status"], "by": "you"}]
    # Text a person changed here is theirs: the automatic writer must not replace it later.
    typed = {k for k, v in text_before.items() if (state.get(k) or ("" if k != "tags" else [])) != v}
    if typed:
        state["copy_auto"] = [k for k in state.get("copy_auto") or [] if k not in typed]
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
    write_json(d / "words.json", clean)
    state = read_json(d / "clip.json")
    state["transcript"] = " ".join(w["word"] for w in clean)
    state["words_edited"] = True
    write_json(d / "clip.json", state)
    for name in RENDERS:  # captions are burned into every render
        remove(d / name)
    return len(clean)


class ClipInUse(ValueError):
    """A clip's file is open in another program, so it cannot be deleted yet."""


def delete_clip(d: Path) -> float:
    """Delete a clip: every file in its folder goes, megabytes freed are returned.

    clip.json stays, marked "deleted", so the page hides the clip and no later run makes it again.
    Finished videos already exported to the ready folder are yours and are not touched.
    """
    path = d / "clip.json"
    state = read_json(path)
    freed = 0
    busy: list[str] = []
    # The big video files first: if one of them is in use, nothing has been removed yet and the clip is left whole.
    for f in sorted((f for f in d.iterdir() if f.is_file() and f.name != "clip.json"), key=lambda f: f.suffix != ".mp4"):
        try:
            size = f.stat().st_size
            remove(f, wait_s=0.5)
            freed += size
        except FileNotFoundError:
            continue  # a job renamed it away this instant
        except FileInUse:
            # Windows refuses to delete a file that is open: a job is still working on this clip, or its video is playing.
            busy.append(f.name)
            if not freed:
                break
    if busy:
        raise ClipInUse(f"this clip cannot be deleted right now: its file {busy[0]} is in use. A job is still working on it "
                        "(press Stop in step 1, or wait for it to end), or its video is playing. Then delete it again.")
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
    control = None
    if cfg is not None:
        from .control import Control

        control = Control(cfg)

    def refresh() -> None:
        if every_stream and cfg is not None:
            from .pipeline import all_works

            by_name.clear()
            by_name.update({w.name: w for w in all_works(cfg)})

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

        def do_GET(self) -> None:
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
                    except Exception as e:  # one unreadable clip must not blank the whole page
                        log.error("clip %s could not be shown: %s", d, e, exc_info=True)
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
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                return self._send(403, b"not allowed from another site", "text/plain")
            actions = {"/api/options": "save", "/api/watch/start": "start_watch", "/api/watch/stop": "stop_watch", "/api/watch/clip-now": "clip_now",
                       "/api/finish": "start_finish", "/api/run": "start_run", "/api/run/stop": "stop_run", "/api/cut": "start_cut", "/api/range": "start_range", "/api/range/cut": "start_range_cut", "/api/ai/check": "start_ai_check", "/api/ai/local-setup": "start_local_ai",
                       "/api/health/check": "start_full_check", "/api/health/repair": "start_repair", "/api/health/update": "start_update",
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
                for old in folder.glob(f"{kind}.*"):
                    old.unlink(missing_ok=True)
                target.write_bytes(data)
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
                    result = getattr(control, name)(body) if name in ("save", "start_watch", "start_run", "start_cut", "start_range", "start_range_cut", "clip_now") else getattr(control, name)()
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

                    if not (d / "final.mp4").exists():
                        render_clip(d, cfg)
                    render_clip(d, cfg, out_name="final_b.mp4", variant="b")
                    return self._send(200, b"{}", "application/json")
                except (ValueError, TypeError, KeyError) as e:
                    return self._send(400, str(e).encode(), "text/plain")
                except Exception as e:
                    log.error("clip %s: %s failed: %s", d.name, m.group(3), e, exc_info=True)
                    return self._send(500, f"{type(e).__name__}: {e}".encode(), "text/plain")
            m = re.fullmatch(r"/api/clip/([A-Za-z0-9_-]+)/([0-9a-z]+)/remake-all", self.path)
            if m and m.group(1) in by_name and cfg is not None:
                # Every unfinished clip of this video is drafted again with the look chosen now (caption style, template, branding).
                import threading

                use = control.cfg if control else cfg
                todo = [d for d in clip_dirs(by_name[m.group(1)])
                        if read_json(d / "clip.json", {}).get("status") in ("candidate", "approved") and (d / "source.mp4").exists()]
                if not todo:
                    return self._send(400, b"this video has no downloaded clips to make again", "text/plain")

                def remake_all() -> None:
                    from .render import render_clip

                    for d in todo:
                        try:
                            for name in RENDERS:
                                if name != "final.mp4":
                                    remove(d / name)
                            render_clip(d, use, force=True)
                        except Exception as e:
                            log.error("clip %s: the draft could not be made again: %s", d.name, e, exc_info=not isinstance(e, ValueError))
                            st = read_json(d / "clip.json", {})
                            st["error"] = f"the draft could not be made again: {type(e).__name__}: {e}"
                            write_json(d / "clip.json", st)

                threading.Thread(target=remake_all, daemon=True, name="remake-all").start()
                return self._send(200, json.dumps({"clips": len(todo)}).encode(), "application/json")
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
                state = write_clip_copy(d, read_json(d / "clip.json"), cfg, force=True)
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
