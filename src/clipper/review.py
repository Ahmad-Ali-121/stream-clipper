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

EDITABLE = {"status", "hook", "hook_b", "intro", "intro_b", "explainer", "disclosure", "layout", "cut_start_s", "cut_end_s",
            "title", "title_b", "description", "tags", "notes"}
TEXT_FIELDS = ("hook", "hook_b", "intro", "intro_b", "title", "title_b", "description", "tags")

PAGE_FILE = Path(__file__).with_name("review_page.html")


def code_stamp() -> str:
    """Changes whenever the program's files on disk change (an update)."""
    return str(int(max(f.stat().st_mtime for f in Path(__file__).parent.glob("*.py"))))


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


def clip_view(d: Path) -> dict:
    """What the page needs to draw one clip."""
    c = read_json(d / "clip.json")
    video = "final.mp4" if (d / "final.mp4").exists() else ("source.mp4" if (d / "source.mp4").exists() else "")
    out = {k: c.get(k) for k in (
        "channel", "start_s", "end_s", "cut_start_s", "cut_end_s", "trigger", "score", "activity", "layout", "layout_reason",
        "transcript", "hook", "hook_b", "intro", "intro_b", "title", "title_b", "description", "tags", "explainer", "disclosure",
        "ai_keep", "ai_reason", "error", "notes", "stream_title", "copy_source", "copy_problem", "ai_pick",
    )}
    out.update(
        key=f"{d.parent.parent.name}/{c['id']}",  # work folder / clip id: unique across streams
        status=c.get("status", "candidate"), video=video, ai_judged=bool(c.get("copy_at")), exported=bool(c.get("exported")),
        lo=c.get("fetch_start_s", c["start_s"]), hi=c.get("fetch_end_s", c["end_s"]),
        # Changes whenever the clip's data or its draft video changes, so the page knows to redraw it.
        mtime=int(max((d / n).stat().st_mtime for n in ("clip.json", video) if n) * 1000),
    )
    return out


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
    if isinstance(edit.get("tags"), str):  # the page sends hashtags as one line of text
        edit["tags"] = ["#" + t.lstrip("#") for t in re.split(r"[\s,]+", edit["tags"]) if t.strip("#")]
    start = float(edit.get("cut_start_s", state.get("cut_start_s", state["start_s"])))
    end = float(edit.get("cut_end_s", state.get("cut_end_s", state["end_s"])))
    lo = float(state.get("fetch_start_s", state["start_s"]))
    hi = float(state.get("fetch_end_s", state["end_s"]))
    if not (lo <= start < end <= hi):
        raise ValueError(f"cut must stay inside the fetched range {lo:g}-{hi:g} seconds and start before it ends")
    before = (state.get("hook"), state.get("layout"), state.get("disclosure"), state.get("cut_start_s"), state.get("cut_end_s"))
    text_before = {k: state.get(k) or ("" if k != "tags" else []) for k in TEXT_FIELDS}
    state.update(edit)
    # Text a person changed here is theirs: the automatic writer must not replace it later.
    typed = {k for k, v in text_before.items() if (state.get(k) or ("" if k != "tags" else [])) != v}
    if typed:
        state["copy_auto"] = [k for k in state.get("copy_auto") or [] if k not in typed]
    # A choice made by a person is final: automatic layout and cut snapping must not undo it.
    if "layout" in edit and edit["layout"] != before[1]:
        state["layout_auto"] = False
    if (state.get("cut_start_s"), state.get("cut_end_s")) != (before[3], before[4]):
        state["cut_auto"] = False
    after = (state.get("hook"), state.get("layout"), state.get("disclosure"), state.get("cut_start_s"), state.get("cut_end_s"))
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


def list_clips(works: list[Path]) -> list[Path]:
    """Clip folders of the given streams: waiting ones first (newest first), then approved, then rejected."""
    order = {"candidate": 0, "approved": 1, "rejected": 2}
    dirs = [d for w in works for d in clip_dirs(w) if read_json(d / "clip.json", {}).get("status") != "deleted"]

    def key(d: Path):
        return (order.get(read_json(d / "clip.json", {}).get("status", "candidate"), 0), -(d / "clip.json").stat().st_mtime)

    return sorted(dirs, key=key)


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
                views = []
                for d in list_clips(list(by_name.values())):
                    try:
                        views.append(clip_view(d))
                    except Exception as e:  # one unreadable clip must not blank the whole page
                        log.error("clip %s could not be shown: %s", d, e, exc_info=True)
                return self._send(200, json.dumps(views).encode("utf-8"), "application/json")
            m = re.fullmatch(r"/media/([A-Za-z0-9_-]+)/([0-9a-z]+)/(final|source)\.mp4", self.path.split("?")[0])
            if not m or m.group(1) not in by_name:
                return self._send(404, b"not found", "text/plain")
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
                       "/api/finish": "start_finish", "/api/run": "start_run", "/api/run/stop": "stop_run", "/api/ai/check": "start_ai_check",
                       "/api/health/check": "start_full_check", "/api/health/repair": "start_repair", "/api/health/update": "start_update"}
            if control and self.path in actions:
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                    name = actions[self.path]
                    result = getattr(control, name)(body) if name in ("save", "start_watch", "start_run") else getattr(control, name)()
                except (ValueError, TypeError) as e:
                    return self._send(400, str(e).encode(), "text/plain")
                return self._send(200, json.dumps(result).encode(), "application/json")
            refresh()
            if self.path == "/api/clips/delete-rejected":
                gone, mb = 0, 0.0
                for d in list_clips(list(by_name.values())):
                    if read_json(d / "clip.json", {}).get("status") == "rejected":
                        mb += delete_clip(d)
                        gone += 1
                log.info("deleted %d rejected clip(s), %.0f MB freed", gone, mb)
                return self._send(200, json.dumps({"deleted": gone, "mb": round(mb)}).encode(), "application/json")
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
