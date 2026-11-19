"""Local review page: watch each candidate, approve or reject it, edit its hook and cut.

Runs a small web server on this machine only (127.0.0.1). Nothing is posted
anywhere from here; it only edits each clip's clip.json.
"""

from __future__ import annotations

import html
import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .moments import format_time
from .work import clip_dirs, read_json, write_json

log = logging.getLogger(__name__)

EDITABLE = {"status", "hook", "hook_b", "intro", "intro_b", "explainer", "disclosure", "layout", "cut_start_s", "cut_end_s", "title", "description", "tags", "notes"}

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Review clips</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#14161a;color:#e8e8e8}
header{padding:14px 20px;background:#1d2026;position:sticky;top:0;z-index:2}
h1{font-size:18px;margin:0}.sub{color:#9aa3ad;font-size:13px}
.clip{display:flex;gap:18px;padding:18px 20px;border-bottom:1px solid #2a2e36;flex-wrap:wrap}
video{width:270px;max-height:480px;background:#000;border-radius:6px}
.info{flex:1;min-width:300px}.meta{color:#9aa3ad;font-size:13px;margin:4px 0 10px}
.tr{background:#1d2026;padding:10px;border-radius:6px;font-size:14px;line-height:1.45;margin-bottom:10px}
label{display:block;font-size:12px;color:#9aa3ad;margin-top:8px}
input[type=text],input[type=number],select{width:100%;box-sizing:border-box;padding:7px;background:#0f1114;color:#e8e8e8;border:1px solid #333a45;border-radius:5px}
.row{display:flex;gap:10px}.row>div{flex:1}
button{padding:9px 16px;border:0;border-radius:5px;font-weight:600;cursor:pointer;margin:12px 8px 0 0}
.ok{background:#2e9e5b;color:#fff}.no{background:#c0473f;color:#fff}.save{background:#3a4250;color:#fff}
.badge{display:inline-block;padding:2px 9px;border-radius:10px;font-size:12px;font-weight:600}
.approved{background:#2e9e5b}.rejected{background:#c0473f}.candidate{background:#59606c}
.clip.rejected-row{opacity:.45}
.panel{padding:14px 20px;background:#191c21;border-bottom:1px solid #2a2e36}
.panel h2{font-size:14px;margin:0 0 8px;color:#cfd6de}.cols{display:flex;gap:26px;flex-wrap:wrap}.cols>div{flex:1;min-width:280px}
.panel label{color:#e8e8e8;font-size:13px;margin-top:5px}.hint{color:#9aa3ad;font-size:12px;margin-top:6px}
pre{background:#0f1114;color:#b8c0c9;padding:8px;border-radius:5px;font-size:11px;max-height:320px;overflow:auto;white-space:pre-wrap;margin:8px 0 0}
.lerr{color:#ff6b61;font-weight:600}.lwarn{color:#e5b84a}
#errbox{display:none;background:#5c1f1b;color:#fff;border:1px solid #c0473f;padding:10px 14px;margin:0 0 12px;border-radius:6px;font-size:13px;white-space:pre-wrap}
.cliperr{background:#5c1f1b;color:#fff;border:1px solid #c0473f;padding:8px 10px;border-radius:6px;font-size:13px;margin-bottom:10px;white-space:pre-wrap}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:#59606c;margin-right:6px}.dot.on{background:#2e9e5b}
</style></head><body><header><h1>__TITLE__</h1><div class="sub">__SUB__</div></header>
__PANEL__
<div id="clips">__CLIPS__</div>
<script>
const val=id=>document.getElementById(id).value, mode=()=>(document.querySelector('[name=o-mode]:checked')||{}).value;
let drawn=false;
function show(s){
  if(!drawn){
    document.getElementById('o-modes').innerHTML=Object.entries(s.modes).map(([k,t],i)=>
      '<label><input type="radio" name="o-mode" value="'+k+'" onchange="saveOpts()"> '+'ABCD'[i]+' &nbsp;'+t+'</label>').join('');
    document.getElementById('o-channels').value=s.channels; drawn=true;
  }
  const r=document.querySelector('[name=o-mode][value='+s.mode+']'); if(r&&!document.querySelector('[name=o-mode]:focus'))r.checked=true;
  document.getElementById('o-ab').checked=s.ab_versions; document.getElementById('o-opening').checked=s.opening;
  document.getElementById('wdot').className='dot'+(s.watching?' on':'');
  logTo('wlog',[s.watching?'WATCHING':'not watching'].concat(s.watch_log));
  logTo('flog',(s.finishing?['WORKING...']:[]).concat(s.finish_log.length?s.finish_log:['nothing made yet in this session']));
  const bad=s.watch_log.concat(s.finish_log).filter(isBad);
  const eb=document.getElementById('errbox');
  eb.style.display=(bad.length||s.watch_problem)?'block':'none';
  eb.textContent=(s.watch_problem?s.watch_problem+String.fromCharCode(10):'')+(bad.length?bad.length+' error or warning line(s) below, shown in colour. Latest: '+bad[bad.length-1]:'');
}
async function act(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){alert(await r.text());return} show(await r.json());
}
function saveOpts(){act('/api/options',{mode:mode(),ab_versions:document.getElementById('o-ab').checked,opening:document.getElementById('o-opening').checked})}
function isBad(l){return /ERROR|FAILED|NOT MADE|Traceback|Error:|fatal exception|STOPPED BY ITSELF/.test(l)}
function logTo(id,lines){const el=document.getElementById(id);const stick=el.scrollTop+el.clientHeight>=el.scrollHeight-30;el.textContent='';
  for(const l of lines){const d=document.createElement('div');d.textContent=l||' ';if(isBad(l))d.className='lerr';else if(/WARNING/.test(l))d.className='lwarn';el.appendChild(d)}
  if(stick)el.scrollTop=el.scrollHeight}
async function poll(){const eb=document.getElementById('errbox');
  try{const r=await fetch('/api/options');if(r.ok)show(await r.json());else{eb.style.display='block';eb.textContent='The page could not read the program state: '+r.status+' '+await r.text()}}
  catch(e){eb.style.display='block';eb.textContent='THE PROGRAM IS NOT ANSWERING ('+e+'). It has stopped or crashed: look at the terminal window where you started it; the reason is printed there.'}
  setTimeout(poll,5000)}
if(document.getElementById('panel'))poll();

async function save(id, extra){
  const el=document.getElementById('c-'+id.replace('/','--')), g=n=>el.querySelector('[name='+n+']');
  const body=Object.assign({hook:g('hook').value, hook_b:g('hook_b').value, intro:g('intro').value, intro_b:g('intro_b').value, explainer:g('explainer').checked, layout:g('layout').value, disclosure:g('disclosure').checked,
    cut_start_s:parseFloat(g('cut_start_s').value), cut_end_s:parseFloat(g('cut_end_s').value)}, extra||{});
  const r=await fetch('/api/clip/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){alert('Save failed: '+await r.text());return}
  const s=await r.json(), b=el.querySelector('.badge');
  b.textContent=s.status; b.className='badge '+s.status; el.classList.toggle('rejected-row', s.status==='rejected');
}
</script></body></html>"""


PANEL = """<div class="panel" id="panel"><div id="errbox"></div><div class="cols">
<div><h2><span class="dot" id="wdot"></span>Watch live streams</h2>
<label>Twitch channels (names, separated by spaces)</label><input type="text" id="o-channels" placeholder="lacy xqc clix">
<div id="o-modes"></div>
<button class="ok" onclick="act('/api/watch/start',{channels:val('o-channels'),mode:mode()})">Start watching</button>
<button class="no" onclick="act('/api/watch/stop',{})">Stop</button>
<pre id="wlog">not watching</pre></div>
<div><h2>Versions to make</h2>
<label><input type="checkbox" id="o-ab" onchange="saveOpts()"> Make an A and a B version of every approved clip (different opening text and title)</label>
<label><input type="checkbox" id="o-opening" onchange="saveOpts()"> Eye-catching first seconds (zoom-in, pop-in text, whoosh, progress bar)</label>
<div class="hint">The explainer is chosen per clip with its own tick box below. With A/B on, the explainer also gets an A and a B.</div>
<button class="ok" onclick="act('/api/finish',{})">Finish: make the videos for approved clips</button>
<button class="save" onclick="location.reload()">Reload clip list</button>
<pre id="flog">nothing made yet in this session</pre></div>
</div></div>"""


def _clip_html(d: Path) -> str:
    c = read_json(d / "clip.json")
    cid = c["id"]
    key = f"{d.parent.parent.name}/{cid}"  # work folder / clip id: unique across streams
    dom = key.replace("/", "--")
    video = "final.mp4" if (d / "final.mp4").exists() else ("source.mp4" if (d / "source.mp4").exists() else "")
    player = (
        f'<video controls preload="metadata" src="/media/{key}/{video}"></video>'
        if video
        else '<div class="tr">not fetched yet</div>'
    )
    e = lambda v: html.escape(str(v if v is not None else ""), quote=True)  # noqa: E731
    layout = c.get("layout") or ""
    opts = "".join(
        f'<option value="{v}"{" selected" if layout == v else ""}>{t}</option>'
        for v, t in (("", "default for this streamer"), ("split", "split: facecam over gameplay"), ("crop", "centre crop"))
    )
    status = c.get("status", "candidate")
    return f"""<div class="clip{' rejected-row' if status == 'rejected' else ''}" id="c-{dom}">{player}
<div class="info"><b>{e(c.get('channel'))} &nbsp; {cid}</b> <span class="badge {status}">{status}</span>
<div class="meta">{format_time(c['start_s'])} - {format_time(c['end_s'])} &middot; found by {e(c.get('trigger') or 'chat')} (strength {c.get('score')}) &middot;
{c.get('activity')} chatters &middot; layout: {e(c.get('layout') or 'default')} ({e(c.get('layout_reason') or 'not checked')}) &middot; showing {video or 'nothing'}</div>
{f'<div class="meta"><b>Note:</b> {e(c.get("notes"))}</div>' if c.get('notes') else ''}
{f'<div class="meta"><b>{"AI suggests keeping" if c.get("ai_keep") else "AI suggests rejecting"}:</b> {e(c.get("ai_reason"))} &middot; tags: {e(" ".join(c.get("tags") or []))}</div>' if c.get('copy_at') else ''}
{f'<div class="cliperr"><b>This clip has an error:</b> {e(c.get("error"))}</div>' if c.get('error') else ''}
<div class="tr">{e(c.get('transcript') or '(no transcript yet)')}</div>
<label>Hook text, version A (shown for the first seconds)</label><input type="text" name="hook" value="{e(c.get('hook'))}">
<label>Hook text, version B (the second version made for A/B testing; empty = same as A)</label><input type="text" name="hook_b" value="{e(c.get('hook_b'))}">
<label>Narration line, version A (spoken before the clip in the explainer version)</label><input type="text" name="intro" value="{e(c.get('intro'))}">
<label>Narration line, version B (empty = same as A)</label><input type="text" name="intro_b" value="{e(c.get('intro_b'))}">
<div class="row"><div><label>Cut start (seconds into the stream)</label><input type="number" step="0.5" name="cut_start_s" value="{e(c.get('cut_start_s', c['start_s']))}"></div>
<div><label>Cut end</label><input type="number" step="0.5" name="cut_end_s" value="{e(c.get('cut_end_s', c['end_s']))}"></div>
<div><label>Layout</label><select name="layout">{opts}</select></div></div>
<label><input type="checkbox" name="explainer"{' checked' if c.get('explainer') else ''}> Also make an explainer version (narrated intro, then the clip)</label>
<label><input type="checkbox" name="disclosure"{' checked' if c.get('disclosure') else ''}> Show on-screen ad disclosure (campaign clips)</label>
<button class="ok" onclick="save('{key}',{{status:'approved'}})">Approve</button>
<button class="no" onclick="save('{key}',{{status:'rejected'}})">Reject</button>
<button class="save" onclick="save('{key}')">Save edits only</button>
</div></div>"""


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
    start = float(edit.get("cut_start_s", state.get("cut_start_s", state["start_s"])))
    end = float(edit.get("cut_end_s", state.get("cut_end_s", state["end_s"])))
    lo = float(state.get("fetch_start_s", state["start_s"]))
    hi = float(state.get("fetch_end_s", state["end_s"]))
    if not (lo <= start < end <= hi):
        raise ValueError(f"cut must stay inside the fetched range {lo:g}-{hi:g} seconds and start before it ends")
    before = (state.get("hook"), state.get("layout"), state.get("disclosure"), state.get("cut_start_s"), state.get("cut_end_s"))
    text_before = {k: state.get(k) or "" for k in ("hook", "hook_b", "intro", "intro_b")}
    state.update(edit)
    # Text a person changed here is theirs: the automatic writer must not replace it later.
    typed = {k for k, v in text_before.items() if (state.get(k) or "") != v}
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


def list_clips(works: list[Path]) -> list[Path]:
    """Clip folders of the given streams: waiting ones first (newest first), then approved, then rejected."""
    order = {"candidate": 0, "approved": 1, "rejected": 2}
    dirs = [d for w in works for d in clip_dirs(w)]

    def key(d: Path):
        return (order.get(read_json(d / "clip.json", {}).get("status", "candidate"), 0), -(d / "clip.json").stat().st_mtime)

    return sorted(dirs, key=key)


def serve(works: list[Path], title: str, port: int = 8765, cfg=None, every_stream: bool = False) -> None:
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
                return self._send(200, json.dumps(control.status()).encode(), "application/json")
            refresh()
            if self.path == "/":
                dirs = list_clips(list(by_name.values()))
                page = (
                    PAGE.replace("__TITLE__", html.escape(title))
                    .replace("__PANEL__", PANEL if control else "")
                    .replace("__SUB__", f"{len(dirs)} clips &middot; edits save to each clip.json &middot; press Ctrl+C in the terminal when done")
                    .replace("__CLIPS__", "".join(_clip_html(d) for d in dirs) or "<p style='padding:20px'>No clips yet. Run find first.</p>")
                )
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
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
            actions = {"/api/options": "save", "/api/watch/start": "start_watch", "/api/watch/stop": "stop_watch", "/api/finish": "start_finish"}
            if control and self.path in actions:
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                    name = actions[self.path]
                    result = getattr(control, name)(body) if name in ("save", "start_watch") else getattr(control, name)()
                except (ValueError, TypeError) as e:
                    return self._send(400, str(e).encode(), "text/plain")
                return self._send(200, json.dumps(result).encode(), "application/json")
            refresh()
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

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Review page: http://127.0.0.1:{port}/   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if control:
            control.stop_watch()
