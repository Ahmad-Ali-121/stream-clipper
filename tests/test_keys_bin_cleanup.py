"""Ahmad, 2026-11-27: keys pasted on the Settings page; a bin for deleted clips; old downloads cleared by themselves;
a button instead of a typed command for the captions report."""

import json
import os
import time
from pathlib import Path

import pytest

import clipper
from clipper import config, control, pipeline, review
from clipper.config import load_config

PAGE = clipper.page_source()
SERVER = (Path(clipper.__file__).parent / "review.py").read_text(encoding="utf-8")


def _cfg(tmp_path):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    return cfg


def _clip(tmp_path, cid="0h10m00s", **extra):
    d = tmp_path / "work" / "twitch_1" / "clips" / cid
    d.mkdir(parents=True)
    state = {"id": cid, "start_s": 600.0, "end_s": 630.0, "status": "candidate", "fetch_start_s": 590.0, "source_width": 1920, "source_height": 1080, **extra}
    (d / "clip.json").write_text(json.dumps(state), encoding="utf-8")
    (d / "source.mp4").write_bytes(b"s" * 4000)
    (d / "final.mp4").write_bytes(b"f" * 1000)
    (d / "words.json").write_text("[]", encoding="utf-8")
    return d


# ---------------- keys ----------------

def test_a_key_pasted_on_the_page_goes_into_the_keys_file_and_nowhere_else(tmp_path, monkeypatch, caplog):
    env = tmp_path / ".env"
    env.write_text("# my keys\nGROQ_API_KEY=old-groq-key-0123456789\nOTHER=stays\n", encoding="utf-8")
    monkeypatch.setenv("CLIPPER_ENV", str(env))                       # never the real .env of this computer
    for name in config.KEY_NAMES.values():
        monkeypatch.delenv(name, raising=False)
    c = control.Control(_cfg(tmp_path))
    monkeypatch.setattr(c, "status", lambda: {"keys": {n: bool(os.environ.get(e)) for n, e in config.KEY_NAMES.items()}})
    secret = "sk_test_ABCdef0123456789-xyz"
    with caplog.at_level("INFO"):
        got = c.save_key({"name": "elevenlabs", "value": "  " + secret + "  "})
    assert got["keys"] == {"claude": False, "elevenlabs": True, "groq": True}
    text = env.read_text(encoding="utf-8")
    assert f"ELEVENLABS_API_KEY={secret}\n" in text and "# my keys" in text and "OTHER=stays" in text and "GROQ_API_KEY=old-groq-key-0123456789" in text
    assert os.environ["ELEVENLABS_API_KEY"] == secret                 # it works at once, without a restart
    assert secret not in caplog.text and secret not in json.dumps(got) # never logged, never sent back
    c.save_key({"name": "groq", "value": "new-groq-key-ABCDEFGHIJ"})   # replacing does not leave the old line behind
    assert env.read_text(encoding="utf-8").count("GROQ_API_KEY=") == 1 and "old-groq-key" not in env.read_text(encoding="utf-8")
    c.save_key({"name": "elevenlabs", "value": ""})                   # removing
    assert "ELEVENLABS_API_KEY" not in env.read_text(encoding="utf-8") and "ELEVENLABS_API_KEY" not in os.environ
    for bad in ("short", "has a space in the middle of it", "line\nbreak-in-the-key-0123456789", 'quote"in-the-key-0123456789'):
        with pytest.raises(ValueError, match="does not look like a key"):
            c.save_key({"name": "claude", "value": bad})
    with pytest.raises(ValueError, match="unknown service"):
        c.save_key({"name": "PATH", "value": secret})
    assert "ANTHROPIC_API_KEY" not in env.read_text(encoding="utf-8")


def test_the_page_never_shows_a_key_again():
    for n in ("claude", "elevenlabs", "groq"):
        assert f'<input type="password" id="key-{n}" autocomplete="off"' in PAGE and f"saveKey('{n}')" in PAGE
    js = PAGE[PAGE.index("async function saveKey(n,remove){"):PAGE.index("function mbText(mb){")]
    assert "inp.value='';" in js and "act('/api/keys/save',{name:n,value:v})" in js
    assert PAGE.count('data-yours onclick="saveKey(') == 3 and PAGE.count('data-yours hidden onclick="saveKey(') == 3   # the help assistant may not press these
    status = (Path(clipper.__file__).parent / "control.py").read_text(encoding="utf-8")
    assert '"keys": {name: bool(os.environ.get(env))' in status                    # only yes or no leaves the program


# ---------------- the bin ----------------

def test_a_deleted_clip_waits_in_the_bin_and_can_be_brought_back(tmp_path):
    cfg = _cfg(tmp_path)
    d = _clip(tmp_path, status="approved", held=False)
    work = d.parent.parent
    mb = review.bin_clip(d)
    state = json.loads((d / "clip.json").read_text(encoding="utf-8"))
    assert mb > 0 and state["status"] == "deleted" and state["was_status"] == "approved" and (d / "source.mp4").exists()   # nothing is removed yet
    assert review.in_bin(d, state) and review.list_clips([work]) == [d]            # still listed, so the page can show the bin
    seen = review.clip_view(d, cfg=cfg)
    assert seen["binned"] is True and seen["bin_days_left"] == cfg.cleanup.bin_days == 7
    assert review.empty_old_bin([work], cfg.cleanup.bin_days) == 0                 # its days are not over
    back = review.restore_clip(d)
    assert back["status"] == "approved" and "deleted_at" not in back and review.clip_view(d, cfg=cfg)["binned"] is False


def test_a_clip_that_was_still_to_review_comes_back_to_review(tmp_path):
    """Found in the browser on 2026-11-27: the guard that keeps a decided clip from sliding back to review also stopped Bring back,
    and the clip was left deleted, outside the bin and outside every list."""
    from clipper import work

    cfg = _cfg(tmp_path)
    d = _clip(tmp_path)                                                           # status: candidate
    review.bin_clip(d)
    assert work.decided_status(d / "clip.json") == "deleted"                        # the decisions record knows of the bin too
    back = review.restore_clip(d)
    assert back["status"] == "candidate" and "deleted_at" not in back
    assert json.loads((d / "clip.json").read_text(encoding="utf-8"))["status"] == "candidate"
    assert work.decided_status(d / "clip.json") is None                            # and it is not put back in the bin at the next start
    seen = review.clip_view(d, cfg=cfg)
    assert seen["binned"] is False and seen["status"] == "candidate"


def test_the_bin_is_emptied_when_its_days_are_over(tmp_path):
    d = _clip(tmp_path)
    work = d.parent.parent
    review.bin_clip(d)
    state = json.loads((d / "clip.json").read_text(encoding="utf-8"))
    state["deleted_at"] = time.time() - 8 * 86400
    (d / "clip.json").write_text(json.dumps(state), encoding="utf-8")
    assert review.empty_old_bin([work], 7) == 1
    assert not (d / "source.mp4").exists() and not (d / "final.mp4").exists()
    assert json.loads((d / "clip.json").read_text(encoding="utf-8"))["status"] == "deleted" and review.list_clips([work]) == []   # gone from every list
    with pytest.raises(ValueError, match="not in the bin any more"):
        review.restore_clip(d)
    d2 = _clip(tmp_path, cid="0h20m00s")
    review.bin_clip(d2)
    assert review.empty_old_bin([work], 7, everything=True) == 1 and not (d2 / "source.mp4").exists()   # "Empty the bin now"


def test_the_page_has_a_bin_and_delete_goes_there_first():
    for piece in ("function place(c){return c.binned?'bin':", ".concat(n.bin||filter==='bin'?[['bin','Bin']]:[])", "clips.length-n.bin",
                  ".clips:not([data-filter=bin]) .clip[data-status=bin]{display:none}", "async function restoreClip(key){", ">Bring back</button>",
                  "send('/api/clip/'+key+'/delete',{forever:gone})", "Moved to the bin. You can bring it back for '+r.days+' days",
                  "filter==='all'?place(c)!=='bin':place(c)===filter", "Move all rejected to the bin", "This clip is in the bin. Press Bring back to use it again."):
        assert piece in PAGE, piece
    assert "/(delete|clear-error|restore)\", self.path)" in SERVER and "mb += bin_clip(d)" in SERVER
    handler = SERVER[SERVER.index('/(delete|clear-error|restore)", self.path)'):SERVER.index('/rewrite", self.path)')]
    assert 'not body.get("forever")' in handler and "was = None  # a damaged record" in handler   # a damaged clip cannot wait in the bin: it goes for good


# ---------------- clearing old downloads ----------------

def _age(d, days):
    old = time.time() - days * 86400
    os.utime(d / "clip.json", (old, old))


def test_old_downloads_of_rejected_clips_are_cleared_and_finished_ones_only_when_asked(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cfg.cleanup.rejected_days, cfg.cleanup.finished_days = 14, 0
    monkeypatch.setattr(pipeline, "all_works", lambda c: [tmp_path / "work" / "twitch_1"])
    old_rej = _clip(tmp_path, "0h01m00s", status="rejected")
    new_rej = _clip(tmp_path, "0h02m00s", status="rejected")
    done = _clip(tmp_path, "0h03m00s", status="approved", exported=str(tmp_path / "ready" / "a.mp4"))
    waiting = _clip(tmp_path, "0h04m00s", status="candidate")
    for d in (old_rej, done, waiting):
        _age(d, 20)
    got = pipeline.auto_clean(cfg)
    assert got["files"] == 2 and not (old_rej / "source.mp4").exists() and not (old_rej / "final.mp4").exists()
    assert (old_rej / "clip.json").exists() and (old_rej / "words.json").exists()            # its record and its words stay
    assert (new_rej / "source.mp4").exists() and (done / "source.mp4").exists() and (waiting / "source.mp4").exists()
    cfg.cleanup.finished_days = 14
    _age(done, 20)
    pipeline.auto_clean(cfg)
    assert not (done / "source.mp4").exists() and (done / "final.mp4").exists()               # the big download goes, the clip still plays
    assert (waiting / "source.mp4").exists()                                                  # a clip still to review is never touched
    assert pipeline.auto_clean(cfg, everything_rejected=True)["files"] == 2 and not (new_rej / "source.mp4").exists()   # "Clear rejected clips now"
    cfg.cleanup.rejected_days = 0
    again = _clip(tmp_path, "0h05m00s", status="rejected")
    _age(again, 400)
    assert pipeline.auto_clean(cfg)["files"] == 0 and (again / "source.mp4").exists()         # Never means never


def test_disk_space_is_counted_and_the_choices_are_checked(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(pipeline, "all_works", lambda c: [tmp_path / "work" / "twitch_1"])
    _clip(tmp_path, "0h01m00s", status="rejected")
    binned = _clip(tmp_path, "0h02m00s")
    review.bin_clip(binned)
    use = pipeline.disk_use(cfg)
    assert set(use) == {"work", "rejected", "finished", "bin", "ready"} and all(isinstance(v, int) for v in use.values())
    assert control.clean_options({"cleanup_rejected_days": 30, "cleanup_finished_days": 0}) == {"cleanup": {"rejected_days": 30, "finished_days": 0}}
    with pytest.raises(ValueError, match="one of the times"):
        control.clean_options({"cleanup_rejected_days": 5})
    for piece in ('id="cl-rej"', 'id="cl-fin"', 'id="disk-now"', "async function cleanNow(what){", "fetch('/api/disk')", 'data-yours onclick="cleanNow(\'rejected\')"'):
        assert piece in PAGE, piece
    assert 'if self.path == "/api/disk" and cfg is not None:' in SERVER and '"/api/clean": "clean_now"' in SERVER


# ---------------- a button instead of a typed command ----------------

def test_the_captions_report_is_a_button_and_no_fix_tells_people_to_type_a_command():
    src = (Path(clipper.__file__).parent / "health.py").read_text(encoding="utf-8")
    assert "uv run clipper doctor" not in src and "-DataDir" not in src and ".env file" not in src
    assert 'id="b-hdoctor" hidden' in PAGE and "$('b-hdoctor').hidden=!(h.items||[]).some(i=>i.id==='speech'&&i.state==='bad');" in PAGE
    assert '"/api/health/doctor": "start_doctor"' in SERVER
    ct = (Path(clipper.__file__).parent / "control.py").read_text(encoding="utf-8")
    report = ct[ct.index("    def start_doctor(self)"):ct.index("    def voice_status(self)")]
    assert "health.speech_model_there(self.cfg)" in report and "NEED_CAPTIONS" in report        # it never downloads the speech model behind the person's back
