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
from .work import clip_dirs, read_json, write_json

log = logging.getLogger(__name__)

EDITABLE = {"status", "hook", "hook_b", "intro", "intro_b", "explainer", "disclosure", "layout", "facecam", "game", "cut_start_s", "cut_end_s",
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
        framing=_framing(c, cfg),
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

    work, state = d.parent.parent, read_json(d / "clip.json")
    if not (d / "source.mp4").exists():
        raise ValueError("this clip has no downloaded video yet, so there is nothing to frame")
    channel = str(state.get("channel") or "")
    if body.get("auto"):
        for k in ("layout", "facecam", "game", "crop_x"):
            state.pop(k, None)
        state["layout_auto"] = True
        layout_mod.save_framing(channel, None, cfg)
        state = layout_mod.auto_layout(d, state, cfg)
        write_json(d / "clip.json", state)
        for name in RENDERS:
            (d / name).unlink(missing_ok=True)
        edit = None
    else:
        if body.get("layout") not in ("split", "crop"):
            raise ValueError("choose face and video, or video only")
        if not body.get("game") or (body["layout"] == "split" and not body.get("facecam")):
            raise ValueError("a box is missing")
        edit = {"layout": body["layout"], "game": body["game"]}
        if body["layout"] == "split":
            edit["facecam"] = body["facecam"]
        state = apply_edit(work, d.name, edit)
        state["layout_auto"], state["layout_reason"] = False, "boxes set by you"
        write_json(d / "clip.json", state)
        if body.get("streamer") and channel:
            layout_mod.save_framing(channel, {**state, "source": [state.get("source_width"), state.get("source_height")]}, cfg)
    for name in RENDERS:
        (d / name).unlink(missing_ok=True)
    render_clip(d, cfg)
    others: list[Path] = []
    if edit and body.get("others"):
        for o in clip_dirs(work):
            st = read_json(o / "clip.json", {})
            same = (st.get("source_width"), st.get("source_height")) == (state.get("source_width"), state.get("source_height"))
            if o != d and same and (o / "source.mp4").exists() and st.get("status") in ("candidate", "approved"):
                apply_edit(work, o.name, edit)
                st = read_json(o / "clip.json")
                st["layout_auto"], st["layout_reason"] = False, "boxes set by you"
                write_json(o / "clip.json", st)
                others.append(o)
    return {"others": others}


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
    if edit.get("layout") not in (None, "", "split", "crop"):
        raise ValueError("layout must be split, crop or empty")
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
        return (st.get("hook") or "", (st.get("layout") or "", tuple(st.get("facecam") or ()), tuple(st.get("game") or ())), bool(st.get("disclosure")),
                round(float(st.get("cut_start_s", st["start_s"])), 2), round(float(st.get("cut_end_s", st["end_s"])), 2))

    before = shown(state)
    text_before = {k: state.get(k) or ("" if k != "tags" else []) for k in TEXT_FIELDS}
    state.update(edit)
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
        (d / name).unlink(missing_ok=True)
    write_json(path, state)
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
        (d / name).unlink(missing_ok=True)
    return len(clean)


def delete_clip(d: Path) -> float:
    """Delete a clip: every file in its folder goes, megabytes freed are returned.

    clip.json stays, marked "deleted", so the page hides the clip and no later run makes it again.
    Finished videos already exported to the ready folder are yours and are not touched.
    """
    path = d / "clip.json"
    state = read_json(path)
    freed = 0
    for f in d.iterdir():
        if f.is_file() and f.name != "clip.json":
            freed += f.stat().st_size
            f.unlink()
    state["status"] = "deleted"
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

                weights = scoring.load_weights(cfg.data_dir) if cfg is not None else None
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
                with f.open("rb") as fh:
                    fh.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = fh.read(min(256 * 1024, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionError):
                pass  # the browser stopped the download (seek or page close)

        def do_POST(self) -> None:
            # Only this page may give orders: a request sent by some other website carries its own Origin.
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                return self._send(403, b"not allowed from another site", "text/plain")
            actions = {"/api/options": "save", "/api/watch/start": "start_watch", "/api/watch/stop": "stop_watch",
                       "/api/finish": "start_finish", "/api/run": "start_run", "/api/run/stop": "stop_run", "/api/cut": "start_cut", "/api/range": "start_range", "/api/range/cut": "start_range_cut", "/api/ai/check": "start_ai_check",
                       "/api/health/check": "start_full_check", "/api/health/repair": "start_repair", "/api/health/update": "start_update",
                       "/api/restart": "restart"}
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
                    result = getattr(control, name)(body) if name in ("save", "start_watch", "start_run", "start_cut", "start_range", "start_range_cut") else getattr(control, name)()
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
                shutil.rmtree(d, ignore_errors=True)
                log.info("deleted the downloaded part %s/%s, %.0f MB freed", m.group(1), m.group(2), mb)
                return self._send(200, json.dumps({"deleted": 1, "mb": round(mb)}).encode(), "application/json")
            if self.path == "/api/clips/delete-rejected":
                gone, mb = 0, 0.0
                for d in list_clips(list(by_name.values())):
                    if read_json(d / "clip.json", {}).get("status") == "rejected":
                        mb += delete_clip(d)
                        gone += 1
                log.info("deleted %d rejected clip(s), %.0f MB freed", gone, mb)
                return self._send(200, json.dumps({"deleted": gone, "mb": round(mb)}).encode(), "application/json")
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
            m = re.fullmatch(r"/api/clip/([A-Za-z0-9_-]+)/([0-9a-z]+)/framing", self.path)
            if m and m.group(1) in by_name and cfg is not None:
                d = by_name[m.group(1)] / "clips" / m.group(2)
                if not (d / "clip.json").exists():
                    return self._send(404, b"no such clip", "text/plain")
                use = control.cfg if control else cfg
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                    others = set_framing(d, body, use)["others"]
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
                return self._send(200, json.dumps({"others": len(others)}).encode(), "application/json")
            m = re.fullmatch(r"/api/clip/([A-Za-z0-9_-]+)/([0-9a-z]+)/(delete|clear-error)", self.path)
            if m and m.group(1) in by_name:
                d = by_name[m.group(1)] / "clips" / m.group(2)
                if not (d / "clip.json").exists():
                    return self._send(404, b"no such clip", "text/plain")
                if m.group(3) == "delete":
                    mb = delete_clip(d)
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
                if not llm.available(cfg):
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

    try:
        server = OneAtATimeServer(("127.0.0.1", port), Handler)
    except OSError as e:
        raise ValueError(
            f"the review page cannot start: port {port} is already in use ({e}). It is probably already running in another "
            f"terminal window. Close that one first (Ctrl+C), or start this one on another port:  uv run clipper review --port {port + 1}"
        ) from e
    print(f"Review page: http://127.0.0.1:{port}/   (Ctrl+C to stop)")
    if control:
        control.shutdown = server.shutdown
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
