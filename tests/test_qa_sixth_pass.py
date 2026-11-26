"""The sixth quality pass: faults found by using the new layout, the setup guide and the installer, and by a
second reading of the Python side. Each test holds one of them shut."""

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import clipper
from clipper import cli, health, plain, setupguide, thumbs
from clipper.config import Config, ConfigError, load_config

SRC = Path(clipper.__file__).parent
PAGE = (SRC / "review_page.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- the page

def test_the_page_keeps_what_was_found_by_using_it():
    for piece in (
        # a card that was not redrawn keeps the state it was drawn from, so unchanged boxes are not sent as typed
        "el._c=c;el.innerHTML=cardHtml(c)}\n    /* the clock",
        # the cutting board's keys only on the cutting board
        "if(curPage!=='new'||curSub!=='board'||openKey||",
        # a clip that goes away while open closes its editor
        "if(el){if(openKey===key)closeClip(true);el.remove()}",
        "if(el.classList.contains('open')){closeClip(true);",
        # a job that ends does not pull the person away
        "const here=curPage==='new'&&!openKey;",
        # nothing plays where it cannot be seen
        "for(const o of el.querySelectorAll('video'))o.pause();",
        "for(const o of document.querySelectorAll('#clips video'))o.pause();\n  curPage=p;",
        # a problem is seen from inside the editor
        "body.editing #errbox{position:fixed;",
        # one Esc closes one thing
        "let escTop='';", "escTop==='stop')stopAsk(false)", "if(escTop==='drawer')actToggle(false);else if(escTop==='clip'&&openKey)",
        # the assistant points inside the open clip and leaves the list alone
        "const firstClip=(openKey&&$(domId(openKey)))||", "clip&&!clip.classList.contains('open')&&clip.offsetParent===null",
        # a wave drawn while hidden is drawn again
        "function redrawSoon(){",
        # Your videos: real order, one video's clips
        "g.t=Math.max(g.t,Number(c.made_at)||0)", "let onlyVid='';", "if(a.vid&&String(c.key).split('/')[0]!==a.vid)return false;",
        # a clip starts from its beginning
        "(openKey===k?'':'#t=1.5')", "function playTile(v){",
        # the setup guide
        "nb.classList.toggle('attn',left>0)", "Checking this computer", "if(await act('/api/setup/finish',{skipped:[...SU.skipped]}))go('new')",
        "if(lg.textContent!==t&&!(sel&&!sel.isCollapsed&&lg.contains(sel.anchorNode)))lg.textContent=t",
        "if(!w||w.state==='done'){SU.wait='';SU.fastUntil=0}",
        # the Activity log opens at its newest lines
        "p.scrollTop=p.scrollHeight",
    ):
        assert piece in PAGE, piece
    assert "else el._c=c;" not in PAGE


# ---------------------------------------------------------------- the setup guide

def test_a_signed_in_tool_is_seen_through_notices_and_a_tool_that_was_not_asked_is_not_called_signed_out(monkeypatch):
    monkeypatch.setattr(setupguide, "which", lambda name: name + ".exe")
    noisy = 'warning: a newer version is available\n{"loggedIn": true, "email": "someone@example.com"}\nnote: telemetry is off\n'
    monkeypatch.setattr(setupguide, "_ask", lambda cmd, timeout=40: (0, noisy))
    assert setupguide.claude_state() == {"installed": True, "signed_in": True}
    for odd in ("[1, 2]", "5", "null", "", "something else entirely"):   # not an object, or nothing: never an error, never a yes
        monkeypatch.setattr(setupguide, "_ask", lambda cmd, timeout=40, odd=odd: (0, odd))
        got = setupguide.claude_state()
        assert got["signed_in"] is False and "problem" in got and "example" not in repr(got)
    monkeypatch.setattr(setupguide, "_ask", lambda cmd, timeout=40: (None, "it did not answer within 40 seconds"))
    for state in (setupguide.claude_state(), setupguide.codex_state()):
        assert state["installed"] and state["signed_in"] is False and "did not answer within 40 seconds" in state["problem"]
        assert "could not be checked" in setupguide._unsure(state) and "Check again" in setupguide._unsure(state)
    assert setupguide._unsure({"installed": True, "signed_in": False}) == ""


def test_asking_a_tool_ends_everything_it_started_and_gives_claude_no_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    code, said = setupguide._ask([sys.executable, "-c", "import os;print(os.environ.get('ANTHROPIC_API_KEY','none'))"])
    assert code == 0 and said.strip() == "none"
    # a tool that starts a helper and then hangs (Codex is a script that starts Node): the limit ends both
    hang = ("import subprocess,sys,time;subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)']);time.sleep(90)")
    t0 = time.monotonic()
    code, said = setupguide._ask([sys.executable, "-c", hang], timeout=2)
    assert code is None and "did not answer within 2 seconds" in said and time.monotonic() - t0 < 25
    assert setupguide._ask(["no-such-program-anywhere-xyz"])[0] is None


def test_the_guide_never_says_all_there_on_no_evidence_and_offers_captions_only_after_the_tools(tmp_path, monkeypatch):
    cfg = Config()
    cfg.paths.data_dir = str(tmp_path)
    tools_none = {"claude": {"installed": False, "signed_in": False}, "codex": {"installed": False, "signed_in": False}, "node": False, "winget": True}
    monkeypatch.setattr(setupguide, "_ask_tools", lambda: tools_none)
    setupguide._cache.update(at=0.0, value=None, asking=False)
    broken = [{"id": "check", "name": "The setup check itself", "state": "bad", "detail": "KeyError: 'x'", "fix": "Report this line."}]
    steps = {s["id"]: s for s in setupguide.status(cfg, broken, {"ready": False, "supported": False}, fresh=True)["steps"]}
    assert steps["tools"]["state"] == "todo" and "could not be checked" in steps["tools"]["detail"] and "KeyError" in steps["tools"]["detail"]
    assert steps["tools"]["actions"] == []
    assert steps["speech"]["state"] == "wait" and steps["speech"]["actions"] == [] and "come first" in steps["speech"]["detail"]


def test_the_sign_in_window_runs_a_tool_in_any_folder_and_says_how_it_ended(tmp_path):
    script = setupguide.signin_script("Sign in to Claude", [r"C:\Users\R&D(x86)\bin\claude.exe", "auth", "login"])
    assert ' & "C:\\Users\\R&D(x86)\\bin\\claude.exe" auth login && (' in script and ") || (" in script and script.endswith("& pause")
    if sys.platform != "win32":
        return
    folder = tmp_path / "R&D(x86)^1"
    folder.mkdir()
    for code in (0, 7):
        tool = folder / f"tool{code}.cmd"
        tool.write_text(f"@echo off\r\necho ran %1\r\nexit /b {code}\r\n", encoding="ascii")
        line = setupguide.signin_script("T", [str(tool), "login"]).replace("& pause", "")
        got = subprocess.run(setupguide.signin_line(line), capture_output=True, text=True, timeout=30).stdout
        assert "ran login" in got, got
        assert ("Finished." in got) == (code == 0) and ("did not finish" in got) == (code != 0), got


def test_what_codex_says_about_the_account_is_never_kept(monkeypatch):
    monkeypatch.setattr(thumbs.shutil, "which", lambda name: "codex.cmd")
    said = SimpleNamespace(stdout="Logged in using an API key - sk-proj-***ABCDE\n", stderr="")
    monkeypatch.setattr(thumbs, "run", lambda *a, **k: said)
    got = thumbs.codex_status(fresh=True)
    assert got["signed_in"] is True and "sk-" not in repr(got) and "ABCDE" not in repr(got)

    def fails(*a, **k):
        raise thumbs.ToolError("asking Codex whether it is signed in failed: exit code 1: Not logged in (was someone@example.com)")

    monkeypatch.setattr(thumbs, "run", fails)
    got = thumbs.codex_status(fresh=True)
    assert got["signed_in"] is False and "example.com" not in repr(got) and "Set up" in got["text"]
    thumbs._status.update(at=0.0, value=None)


def test_the_speech_model_is_only_loaded_when_it_is_on_this_computer(tmp_path, monkeypatch):
    from clipper import hw

    cfg = Config()
    cfg.paths.data_dir = str(tmp_path)
    assert health.speech_model_there(cfg) is False
    monkeypatch.setattr(hw, "detect", lambda cfg, refresh=False: {})
    monkeypatch.setattr(hw, "trial_load", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the model must not be loaded (that downloads it)")))
    monkeypatch.setattr(health, "update_state", lambda fetch=False: {"can": False, "behind": 0, "why": ""})
    health.full_check(cfg, speech=False)                     # what Update does on a computer without the model
    saved = json.loads((tmp_path / "health.json").read_text(encoding="utf-8"))
    assert "speech" not in saved and saved["checked_at"] > 0
    d = tmp_path / "models" / f"models--x--faster-whisper-{cfg.transcribe.model}" / "snapshots" / "1"
    d.mkdir(parents=True)
    assert health.speech_model_there(cfg) is False           # a folder without the model file is a download that never finished
    (d / "model.bin").write_bytes(b"x")
    assert health.speech_model_there(cfg) is True
    code = (SRC / "control.py").read_text(encoding="utf-8")
    assert code.count("speech=health.speech_model_there(") + code.count("health.full_check(cfg, speech=there)") == 2
    assert 'saved.get("speech") and health.speech_model_there(self.cfg)' in code


def test_an_installer_that_waits_for_ever_is_ended_and_said():
    from clipper.control import Control

    said = []
    t0 = time.monotonic()
    code = Control._run_logged([sys.executable, "-c", "import time;print('asking a question nobody sees',flush=True);time.sleep(120)"], said.append, str(Path.cwd()),
                               quiet_s=2)
    assert code != 0 and time.monotonic() - t0 < 40
    assert said[0] == "asking a question nobody sees" and "ENDED BY THE PROGRAM" in said[-1] and "printed nothing" in said[-1]
    said.clear()
    assert Control._run_logged([sys.executable, "-c", "print('fine')"], said.append, str(Path.cwd())) == 0 and said == ["fine"]


# ---------------------------------------------------------------- the live watch

def _watched(tmp_path, cfg, began_ago=1030, first=1000.0, pieces=12):
    from clipper import buffer

    held = buffer.LiveBuffer("chan", tmp_path / "buffer", 0.0, cfg)
    held.pieces = [{"t": first + 2 * i, "d": 2.0, "file": "x", "init": ""} for i in range(pieces)]
    held.first_t = first
    return SimpleNamespace(meta={"started_at": time.time() - began_ago}, buffer=held, channel="x", work=tmp_path / "work", asked=[])


def test_two_requests_that_reach_back_to_the_same_second_get_a_clip_each(tmp_path):
    from clipper.watch import asked_moment
    from clipper.work import clip_id

    cfg = Config()
    cfg.paths.data_dir = str(tmp_path)
    w = _watched(tmp_path, cfg)
    a = asked_moment(w, {"at": time.time(), "seconds": 60}, cfg)
    w.asked.append(a)
    b = asked_moment(w, {"at": time.time() + 20, "seconds": 120}, cfg)
    assert a["start_s"] == b["start_s"] and clip_id(a["peak_s"]) != clip_id(b["peak_s"])   # same start, two folders
    w.asked.clear()
    (w.work / "clips" / clip_id(a["peak_s"])).mkdir(parents=True)                           # the first is made (maybe approved) already
    c = asked_moment(w, {"at": time.time() + 20, "seconds": 120}, cfg)
    assert clip_id(c["peak_s"]) != clip_id(a["peak_s"]) and c["start_s"] <= c["peak_s"] < c["end_s"]


def test_a_request_longer_than_the_copy_keeps_is_shortened_and_said(tmp_path, caplog):
    from clipper.watch import asked_moment

    cfg = Config()
    cfg.paths.data_dir = str(tmp_path)
    cfg.watch.buffer_minutes = 2
    w = _watched(tmp_path, cfg, began_ago=5000, first=100.0, pieces=60)
    w.buffer.pieces = [{"t": 4880.0 + 2 * i, "d": 2.0, "file": "x", "init": ""} for i in range(60)]   # long into the watch: the oldest piece is recent
    with caplog.at_level("WARNING"):
        m = asked_moment(w, {"at": time.time(), "seconds": 300}, cfg)
    assert m["end_s"] - m["start_s"] < 120 and "only the last 2 minutes" in caplog.text
    assert "the watch began after" not in caplog.text


def test_a_stretch_from_before_the_watch_is_not_tried_again_and_leaves_no_empty_folder(tmp_path, monkeypatch):
    from clipper import watch

    cfg = Config()
    cfg.paths.data_dir = str(tmp_path)
    w = _watched(tmp_path, cfg)

    def gone(w, moment, cfg):
        watch.clip_dir(w.work, watch.clip_id(moment["peak_s"]))   # as make_draft does before it can refuse
        raise watch.NothingKept("the watch began after the stretch you asked for")

    monkeypatch.setattr(watch, "make_draft", gone)
    moment = {"peak_s": 950.0, "start_s": 950.0, "end_s": 990.0, "asked": True}
    watch.draft_job(w, moment, cfg)
    assert w.asked == [] and not (w.work / "clips" / watch.clip_id(950.0)).exists()
    monkeypatch.setattr(watch, "make_draft", lambda w, m, c: (_ for _ in ()).throw(watch.ToolError("the recording has not reached it yet")))
    watch.draft_job(w, moment, cfg)
    assert len(w.asked) == 1 and w.asked[0]["tries"] == 1    # any other failure of an asked clip is still tried again
    assert issubclass(watch.NothingKept, watch.ToolError)
    assert "w.asked.append({**moment, \"not_before\": time.time() + 10})" in (SRC / "watch.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- the settings file and the command line

@pytest.mark.parametrize("text, says", [
    ("moments:\n  bucket_s: ten\n", "moments.bucket_s must be a number"),
    ("moments:\n  before_s:\n", "moments.before_s must be a number"),
    ("moments:\n  window_s: [300]\n", "moments.window_s must be a number"),
    ("watch:\n  poll_s: fast\n", "watch.poll_s must be a number"),
    ("llm:\n  help_act_at_once: never\n", "llm.help_act_at_once must be true or false"),
    ("channels: [lacy]\n", "channels must be a list of 'name: value' lines"),
    ("paths:\n  data_dir: 5\n", "must be the path of a folder"),
    ("paths:\n  data_dir:\n", "must be the path of a folder"),
    ("moments:\n  top: [1, 2\n", "has a writing mistake"),
    ("moments:\n\ttop: 3\n", "has a writing mistake"),
])
def test_a_wrong_value_in_the_settings_file_is_named_with_its_key_and_file(tmp_path, monkeypatch, text, says):
    monkeypatch.delenv("CLIPPER_DATA_DIR", raising=False)
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(f)
    assert says in str(e.value) and "config.yaml" in str(e.value)


def test_a_settings_file_that_cannot_be_read_or_found_says_so(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPPER_DATA_DIR", raising=False)
    f = tmp_path / "config.yaml"
    f.write_text("moments:\n  top: 3\n", encoding="utf-16")
    with pytest.raises(ConfigError, match="not saved as plain UTF-8"):
        load_config(f)
    monkeypatch.setenv("CLIPPER_CONFIG", str(tmp_path / "nowhere.yaml"))
    with pytest.raises(ConfigError, match="CLIPPER_CONFIG"):
        load_config()                                         # not: built-in settings and another data folder without a word
    monkeypatch.delenv("CLIPPER_CONFIG")
    a_file = tmp_path / "a-file"
    a_file.write_text("x", encoding="utf-8")
    f.write_text(f"paths:\n  data_dir: {a_file.as_posix()}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="names a file, not a folder"):
        load_config(f)
    f.write_text("llm:\n  help_act_at_once: off\nmoments:\n  top: 4\n", encoding="utf-8")
    cfg = load_config(f)                                      # the plain ways of writing it still work
    assert cfg.llm.help_act_at_once is False and cfg.moments.top == 4
    f.write_text("llm:\n  help_act_at_once: \"false\"\nmoments:\n  top: \"6\"\n  bucket_s: \"2.5\"\n", encoding="utf-8")
    cfg = load_config(f)                                      # in quotes it means what it says: "false" used to switch it ON
    assert cfg.llm.help_act_at_once is False and cfg.moments.top == 6 and cfg.moments.bucket_s == 2.5
    assert load_config().data_dir                             # the shipped config.yaml passes its own checks


def test_a_persons_mistake_on_the_command_line_gets_a_plain_line(tmp_path, capsys, caplog):
    out = tmp_path / "x.ass"
    for bad in ("5", '["a", "b"]', '[{"word": "hi"}]'):
        words = tmp_path / "words.json"
        words.write_text(bad, encoding="utf-8")
        caplog.clear()
        assert cli.main(["captions", str(words), "--out", str(out)]) == 2
        assert "is not a list of words" in caplog.text and "Traceback" not in caplog.text
    brief = Path(clipper.__file__).parents[2] / "briefs" / "example.yaml"
    clip = tmp_path / "clip.json"
    for bad in ('{"source_url": "x", "extra": 1}', "[1]", '{"source_url": "x", "duration_s": "long", "width": 1, "height": 1}'):
        clip.write_text(bad, encoding="utf-8")
        caplog.clear()
        assert cli.main(["check-brief", str(brief), str(clip)]) == 2
        assert "is not a clip description" in caplog.text
    caplog.clear()
    assert cli.main(["captions", str(tmp_path), "--out", str(out)]) == 2          # a folder where a file belongs
    assert "a file or folder could not be used" in caplog.text
    broken = tmp_path / "brief.yaml"
    broken.write_text("name: [unclosed\n", encoding="utf-8")
    clip.write_text('{"source_url": "x", "duration_s": 30, "width": 1080, "height": 1920}', encoding="utf-8")
    caplog.clear()
    assert cli.main(["check-brief", str(broken), str(clip)]) == 2
    assert "writing mistake" in caplog.text


def test_a_damaged_file_is_named_and_a_countdown_needs_two(tmp_path):
    from clipper import pipeline
    from clipper.work import read_json

    bad = tmp_path / "clip.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError) as e:
        read_json(bad)
    assert "clip.json is damaged" in str(e.value) and str(tmp_path) in str(e.value)
    assert read_json(bad, {}) == {}                            # with a default it is still treated as empty
    cfg = Config()
    cfg.paths.data_dir = str(tmp_path)
    for count in (1, 0, -1):
        with pytest.raises(pipeline.PipelineError, match="--count must be 2 or more"):
            pipeline.stage_top(cfg, count=count)


def test_a_countdown_is_made_fresh_and_leaves_no_working_files_among_the_videos(tmp_path, monkeypatch):
    from clipper import compose

    forced = []
    monkeypatch.setattr(compose, "render_clip", lambda d, cfg, out_name="", hook=True, force=False: forced.append(force) or d / out_name)
    monkeypatch.setattr(compose, "still_frame", lambda d, cfg: d / "still.jpg")
    def written(out):
        out.write_bytes(b"x")
        return out

    monkeypatch.setattr(compose, "make_card", lambda still, line, out, cfg, **k: written(out))
    monkeypatch.setattr(compose, "concat", lambda parts, out, cfg: written(out))
    dirs = []
    for n in range(2):
        d = tmp_path / f"c{n}"
        d.mkdir()
        (d / "clip.json").write_text(json.dumps({"hook": f"moment {n}"}), encoding="utf-8")
        dirs.append(d)
    ready = tmp_path / "ready"
    ready.mkdir()
    out = compose.top_moments(dirs, ready / "top.mp4", Config(), opener="Top two")
    assert forced == [True, True] and out.exists() and [p.name for p in ready.iterdir()] == ["top.mp4"]


# ---------------------------------------------------------------- plain words

def test_four_messages_that_were_explained_as_something_else():
    cases = {
        "Twitch chat download failed: exit code 1: System.UnauthorizedAccessException: Access to the path 'D:\\data\\chat.part.json' is denied.": "another program has it open",
        "setup failed: ... + FullyQualifiedErrorId : UnauthorizedAccess": "another program has it open",
        'Claude Code reported a problem: API Error: 403 {"error":{"type":"forbidden","message":"Request not allowed"}}': "AI service refused the request",
        "fetch failed: exit code 1: Failed to resolve hostname stream.kick.com: No such host is known. | Error opening input: Input/output error": "internet connection dropped",
        "fetch failed: [tls @ 000] Error in the pull function. | Error opening input": "internet connection dropped",
        "@OpenAI live details failed: yt-dlp: HTTP Error 429: Too Many Requests": "video site is refusing for a while",
        "kick.com/claude chat failed (HTTP Error 429: Too Many Requests)": "video site is refusing for a while",
        "ChatGPT (Codex) making a thumbnail failed: exit code 1: 429 Too Many Requests": "limit of your plan",
        "Claude Code reported a problem: 429 too many requests": "limit of your plan",
        "the API said HTTP Error 401: Unauthorized": "did not accept the key",
        "ERROR: [twitch:vod] 123: HTTP Error 403: Forbidden": "video site refused the request",
    }
    for text, want in cases.items():
        got = plain.explain(text)
        assert want in (got.get("what") or ""), (text, got)


# ---------------------------------------------------------------- the assistant

def test_with_acting_switched_off_the_answer_does_not_claim_to_be_doing_it(monkeypatch):
    from clipper import help as help_mod
    from clipper import llm

    targets = [{"key": "o-channels", "kind": "box", "label": "Channels, separated by spaces", "where": "Live channels", "now": "(empty)"},
               {"key": "b-watch", "kind": "button", "label": "Start watching", "where": "Live channels"}]
    reply = {"answer": "I am typing lacy into Channels and starting the watch.", "follow_ups": [], "run": True,
             "steps": [{"text": "Type the channel", "target": "o-channels", "act": "type", "value": "lacy"}, {"text": "Start", "target": "b-watch", "act": "press", "value": ""}]}
    monkeypatch.setattr(llm, "ask", lambda *a, **k: dict(reply))
    cfg = load_config()
    cfg.llm.help_act_at_once = False
    r = help_mod.answer(cfg, "start watching lacy", [], targets, {})
    assert r["run"] is False and "I am typing" not in r["answer"] and "Nothing has been done yet" in r["answer"] and "Do it for me" in r["answer"]
    asked = dict(reply, run=False, answer="Type the channel into Channels, then press Start watching.")
    monkeypatch.setattr(llm, "ask", lambda *a, **k: dict(asked))
    assert help_mod.answer(cfg, "how do I watch lacy?", [], targets, {})["answer"] == asked["answer"]   # an explanation is left as it is


# ---------------------------------------------------------------- YouTube live

@pytest.mark.parametrize("newer", [False, True])
def test_a_youtube_clip_from_the_newer_kind_of_feed_gets_its_header_piece(tmp_path, monkeypatch, newer):
    from clipper import youtube
    from clipper.buffer import Piece

    cfg = Config()
    cfg.paths.data_dir = str(tmp_path)
    w = youtube.YouTubeWindow("https://www.youtube.com/watch?v=x", 0.0, cfg)
    head = ("https://v/init.mp4", "https://a/init.mp4") if newer else ("", "")
    rows = [{"t": 100.0 + 2 * i, "d": 2.0, "key": i, "piece": Piece(f"https://v/{i}", 100.0 + 2 * i, 2.0, False, head[0])} for i in range(3)]
    sound = {i: Piece(f"https://a/{i}", 100.0 + 2 * i, 2.0, False, head[1]) for i in range(3)}
    monkeypatch.setattr(w, "_rows", lambda: (rows, sound))
    asked, seen = [], {}
    monkeypatch.setattr(youtube, "_get", lambda url, timeout=30: asked.append(url) or b"x")
    monkeypatch.setattr(youtube, "find_tool", lambda name, data: "ffmpeg")

    def join(cmd, timeout_s=0, what="", cwd=None):
        for kind in ("v", "a"):
            seen[kind] = (cwd / f"{kind}.m3u8").read_text(encoding="utf-8")
        seen["files"] = sorted(p.name for p in cwd.iterdir())
        Path(cmd[-1]).write_bytes(b"mp4")

    monkeypatch.setattr(youtube, "run", join)
    assert w.cut(100.0, 105.0, tmp_path / "clip" / "source.mp4") == 100.0
    if newer:
        assert seen["v"].count('#EXT-X-MAP:URI="vinit0.mp4"') == 1 and seen["a"].count('#EXT-X-MAP:URI="ainit0.mp4"') == 1   # once, in front of the pieces
        assert seen["v"].index("#EXT-X-MAP") < seen["v"].index("v0000.m4s") and "vinit0.mp4" in seen["files"] and "a0002.m4s" in seen["files"]
        assert asked.count("https://v/init.mp4") == 1 and asked.count("https://a/init.mp4") == 1                          # fetched once, not per piece
    else:
        assert "#EXT-X-MAP" not in seen["v"] + seen["a"] and "v0000.ts" in seen["files"] and "a0000.aac" in seen["files"]   # the usual feed, as before
