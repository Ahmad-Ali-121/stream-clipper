"""Kick channels: names, chat pages, the chat replay, the live chat reader, and going live."""

import json

import pytest

from clipper import kick
from clipper.config import load_config


def _cfg(tmp_path):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    return cfg


def test_a_kick_channel_is_told_from_a_kick_video_and_from_twitch():
    from clipper.watch import channel_name

    assert channel_name("https://kick.com/XQC") == "kick.com/xqc" and channel_name("kick.com/ad-in_ross?x=1") == "kick.com/ad-in_ross"
    assert kick.slug_of("https://kick.com/video/61802db4-bbbd-4c96-a0a2-30db8bb54598") is None
    with pytest.raises(ValueError, match="not a Kick channel"):
        channel_name("kick.com/")
    assert channel_name("lacy") == "lacy"
    with pytest.raises(ValueError, match="kick.com/name"):
        channel_name("not a channel!")


def test_times_and_emotes_are_read_as_kick_writes_them():
    assert kick._epoch("2026-11-20 16:30:48") == kick._epoch("2026-11-20T16:30:48+00:00") == kick._epoch("2026-11-20T16:30:48Z")
    assert kick._epoch("nonsense") is None
    assert kick.chat_text("no way [emote:4147884:vibePls][emote:1:KEKW] bro") == "no way vibePls KEKW bro"


def _message(n, at):
    return {"id": f"m{n}", "content": f"hello {n}", "type": "message", "created_at": at, "sender": {"username": f"user{n}"}}


def test_a_chat_page_keeps_real_messages_and_the_way_to_older_ones(monkeypatch):
    asked = []
    page = {"data": {"cursor": "1795194301321825", "messages": [
        _message(1, "2026-11-20T17:00:05Z"), {**_message(2, "2026-11-20T17:00:04Z"), "content": ""},
        {**_message(3, "2026-11-20T17:00:03Z"), "type": "gift"}, {**_message(4, "bad time")}]}}
    monkeypatch.setattr(kick, "_get", lambda url, timeout=30: asked.append(url) or json.dumps(page).encode())
    got, older = kick.chat_page(668, 123)
    assert [m["id"] for m in got] == ["m1"] and got[0]["author"] == "user1" and older == 1795194301321825
    assert asked[0].endswith("/668/messages?cursor=123")


def _fake_chat(monkeypatch, stamps):
    """A Kick whose chat holds one message at each of ``stamps`` (epoch seconds), handed out 25 at a time backwards."""
    held = sorted(stamps, reverse=True)
    calls = []

    def page(channel_id, before=None, tries=3):
        calls.append(before)
        limit = before / 1e6 if before else float("inf")
        rows = [t for t in held if t < limit][: kick.PAGE]
        out = [{"id": f"m{t}", "at": float(int(t)), "author": "a", "text": "x"} for t in rows]
        return out, (int(rows[-1] * 1e6) if rows else None)

    monkeypatch.setattr(kick, "chat_page", page)
    return calls


def test_a_whole_chat_replay_is_read_in_slices_with_nothing_lost_or_doubled(tmp_path, monkeypatch):
    start = 1_000_000.0
    stamps = [start + i * 7 + 0.5 for i in range(400)] + [start - 50.5, start + 5000.5]  # two outside the video
    _fake_chat(monkeypatch, stamps)
    seen = []
    out = tmp_path / "chat.json"
    count = kick.save_chat(1, start, 2800.0, out, on_progress=seen.append)
    assert count == 400 and seen[-1] == 1.0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith('{"author"')  # not taken for a chat collected by the live watcher
    times = [json.loads(x)["t"] for x in lines]
    assert times == sorted(times) and times[0] == 0.0 and times[-1] <= 2800

    from clipper.chat import load_chat

    assert len(load_chat(out)) == 400


def test_a_chat_download_that_stopped_continues_where_it_was(tmp_path, monkeypatch):
    start = 2_000_000.0
    stamps = [start + i * 5 + 2.5 for i in range(600)]  # fifty minutes: five slices of ten
    _fake_chat(monkeypatch, stamps)
    inner = kick.chat_page

    def flaky(channel_id, before=None, tries=3):
        if before and before / 1e6 > start + 1900:  # the last two slices cannot be read
            raise kick.ToolError("Kick did not answer for the chat (HTTP Error 429: Too Many Requests)")
        return inner(channel_id, before, tries)

    monkeypatch.setattr(kick, "chat_page", flaky)
    out = tmp_path / "chat.json"
    with pytest.raises(kick.ToolError, match="429"):
        kick.save_chat(1, start, 3000.0, out)
    assert not out.exists()
    kept = tmp_path / "chat.reading.jsonl"
    had = {json.loads(line)["slice"] for line in kept.read_text(encoding="utf-8").splitlines()} if kept.exists() else set()
    assert had <= {0, 1, 2}
    asked = []
    monkeypatch.setattr(kick, "chat_page", lambda cid, before=None, tries=3: asked.append(before) or inner(cid, before, tries))
    assert kick.save_chat(1, start, 3000.0, out) == 600 and not kept.exists()
    again = {int((b / 1e6 - start - 1.000001) // 600) for b in asked}
    assert again == {0, 1, 2, 3, 4} - had  # the parts already read were not asked for a second time


def test_kick_saying_too_many_requests_is_waited_out(monkeypatch):
    import io
    import urllib.error

    waits, calls = [], []

    def get(url, timeout=30):
        calls.append(url)
        if len(calls) < 3:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {"Retry-After": "7"} if len(calls) == 1 else {}, io.BytesIO(b""))
        return b'{"ok": 1}'

    monkeypatch.setattr(kick, "_get", get)
    monkeypatch.setattr(kick.time, "sleep", waits.append)
    assert kick._ask("https://kick.com/x", "the chat", tries=5) == {"ok": 1}
    assert waits == [7.0, 30.0]  # what Kick asked for, then our own longer wait


def test_the_live_chat_reader_writes_each_message_once_and_catches_up_when_chat_is_fast(tmp_path, monkeypatch):
    import time

    now = time.time()
    held = [now - 300 + i for i in range(30)]  # said before the watch began
    calls = _fake_chat(monkeypatch, held)
    r = kick.KickChatReader("name", 1, tmp_path / "chat.json", now - 1000)
    assert r.fresh() and len(calls) == 1  # the first look only notes what is already there; one question
    r.connected_at = now
    assert r.fresh() == []
    held.extend(now + 1 + i * 0.01 for i in range(60))  # sixty messages between two looks: more than two pages
    del calls[:]
    monkeypatch.setattr(kick, "chat_page", (lambda inner: lambda cid, before=None: inner(cid, before))(kick.chat_page))
    _fake_chat(monkeypatch, held)
    new = r.fresh()
    assert len(new) == 60 and [m["at"] for m in new] == sorted(m["at"] for m in new)
    assert r.fresh() == []


def test_a_kick_channel_that_is_live_and_recorded_is_watched_from_its_recording(tmp_path, monkeypatch):
    from clipper import watch

    cfg = _cfg(tmp_path)
    cfg.watch.mode = "chat"
    uuid = "dcf55294-0a1b-4c3b-a6d2-9a8fb4dd52e1"
    state = {"live": {"id": "77", "title": "WE ARE BACK", "started_at": 5000.0}}
    monkeypatch.setattr(kick, "channel", lambda slug: {"id": 9, "slug": slug, "name": "Name", "records": True, "feed": "f", "live": state["live"]})
    monkeypatch.setattr(kick, "live_recording", lambda slug: {"uuid": uuid, "playlist": "https://x/master.m3u8"})
    monkeypatch.setattr(kick, "playlist_times", lambda playlist: (4997.5, 600.0))
    monkeypatch.setattr(kick.KickChatReader, "start", lambda self: None)
    w = watch.start_channel("kick.com/name", cfg)
    assert w.source.platform == "kick" and w.source.video_id == uuid and w.buffer is None
    assert w.meta["started_at"] == 4997.5 and w.meta["chat_id"] == 9 and w.meta["playlist"].endswith("master.m3u8")  # the recording's own clock
    assert json.loads((w.work / "meta.json").read_text(encoding="utf-8"))["is_live"] is True
    assert watch.still_live(w, cfg)
    state["live"] = {"id": "78", "title": "next", "started_at": 9000.0}
    assert not watch.still_live(w, cfg)  # a new broadcast is not the one being watched
    state["live"] = None
    assert watch.start_channel("kick.com/name", cfg) is None and not watch.still_live(w, cfg)


def test_a_kick_stream_that_is_not_recorded_needs_the_live_copy_switched_on(tmp_path, monkeypatch):
    from clipper import watch

    cfg = _cfg(tmp_path)
    cfg.watch.buffer_when_no_vod = False
    monkeypatch.setattr(kick, "channel", lambda slug: {"id": 9, "slug": slug, "name": "N", "records": False, "feed": "f", "live": {"id": "1", "title": "", "started_at": 5.0}})
    assert watch.start_channel("kick.com/name", cfg) is None
    monkeypatch.setattr(kick, "channel", lambda slug: None)
    assert watch.start_channel("kick.com/nobody", cfg) is None


def test_a_finished_kick_stream_gets_its_chat_and_an_old_one_says_why_not(tmp_path, monkeypatch):
    from clipper import fetch
    from clipper.sources import parse_source

    cfg = _cfg(tmp_path)
    source = parse_source("https://kick.com/video/61802db4-bbbd-4c96-a0a2-30db8bb54598")
    work = tmp_path / "w"
    work.mkdir()
    (work / "meta.json").write_text(json.dumps({"chat_id": 668, "playlist": "https://x/master.m3u8", "started_at": 100.0, "duration_s": 900}), encoding="utf-8")
    monkeypatch.setattr(kick, "playlist_times", lambda playlist: (98.0, 900.0))
    got = {}

    def save(channel_id, start, length, out, on_progress=None):
        got.update(id=channel_id, start=start, length=length)
        out.write_text(json.dumps({"author": "a", "text": "x", "t": 1.0}) + "\n", encoding="utf-8")
        return 1

    monkeypatch.setattr(kick, "save_chat", save)
    out = fetch.fetch_chat(source, work, cfg)
    assert out.name == "chat.json" and got == {"id": 668, "start": 98.0, "length": 900.0}
    out.unlink()
    monkeypatch.setattr(kick, "save_chat", lambda *a, **k: 0)
    with pytest.raises(fetch.NoChatError, match="no chat for this video"):
        fetch.fetch_chat(source, work, cfg)


def test_entries_that_are_not_chat_do_not_end_the_reading(tmp_path, monkeypatch):
    """Kick's pages can hold gifts and deleted lines; a page of nothing else must not be taken for the end of the chat."""
    start = 3_000_000.0
    real = sorted([start + 100.5 + i for i in range(30)] + [start + 400.5 + i for i in range(30)], reverse=True)
    gap = (start + 200, start + 380)  # a stretch whose entries are all filtered out

    def page(channel_id, before=None, tries=3):
        limit = before / 1e6 if before else float("inf")
        if gap[0] < limit <= gap[1] + 1:  # inside the stretch: an empty page, but a cursor that leads on
            return [], int((limit - 60) * 1e6)
        rows = [t for t in real if t < limit and not (limit > gap[1] and t < gap[1])][: kick.PAGE]
        if not rows:
            return ([], int(gap[1] * 1e6)) if limit > gap[1] else ([], None)
        return [{"id": f"m{t}", "at": float(int(t)), "author": "a", "text": "x"} for t in rows], int(rows[-1] * 1e6)

    monkeypatch.setattr(kick, "chat_page", page)
    assert len(kick.chat_between(1, start, start + 600)) == 60  # the thirty before the stretch were reached


def test_when_one_part_of_the_chat_fails_the_others_stop_asking_and_what_was_read_is_kept(tmp_path, monkeypatch):
    import threading
    import time

    start = 4_000_000.0
    calls = []
    lock = threading.Lock()

    def page(channel_id, before=None, tries=3):
        t = before / 1e6
        with lock:
            calls.append(t)
        if t > start + 1800:
            raise kick.ToolError("Kick did not answer for the chat (HTTP Error 429: Too Many Requests)")
        time.sleep(0.02)
        if t <= start + 600.5 and t > start + 590:  # the first slice: one short page and done
            return [{"id": "a1", "at": start + 5, "author": "a", "text": "x"}], None
        return [{"id": f"m{t}", "at": t - 2, "author": "a", "text": "x"}], int((t - 1) * 1e6)  # the others would go on for 600 pages

    monkeypatch.setattr(kick, "chat_page", page)
    keep = tmp_path / "chat.reading.jsonl"
    with pytest.raises(kick.ToolError, match="429"):
        kick.chat_between(1, start, start + 2400, keep=keep)
    asked = len(calls)
    assert asked < 200  # the long slices stopped soon after the failure instead of reading on
    time.sleep(0.3)
    assert len(calls) <= asked + 4  # ... and nothing keeps asking Kick in the background


def test_a_recording_that_is_still_growing_keeps_its_full_parts_on_the_next_try(tmp_path, monkeypatch):
    start = 5_000_000.0
    _fake_chat(monkeypatch, [start + i * 10 + 2.5 for i in range(200)])
    keep = tmp_path / "chat.reading.jsonl"
    keep.write_text(json.dumps({"for": [9, 1.0, 600.0], "slice": 0, "span": [1.0, 601.0], "messages": []}) + "\nhalf a li", encoding="utf-8")
    inner = kick.chat_page
    assert len(kick.chat_between(1, start, start + 900, keep=keep)) == 90
    rows = [json.loads(x) for x in keep.read_text(encoding="utf-8").splitlines()]
    assert sorted(r["slice"] for r in rows) == [0, 1] and all(r["for"][0] == 1 for r in rows)  # the other video's row and the torn line are gone
    asked = []
    monkeypatch.setattr(kick, "chat_page", lambda cid, before=None, tries=3: asked.append(before / 1e6) or inner(cid, before, tries))
    assert len(kick.chat_between(1, start, start + 1500, keep=keep)) == 150  # ten minutes more were broadcast since
    assert min(asked) > start + 600  # the first ten minutes were not read again; the part that grew was


def test_a_kick_recording_that_is_not_listed_yet_is_waited_for(tmp_path, monkeypatch):
    import time

    from clipper import watch

    cfg = _cfg(tmp_path)
    cfg.watch.mode = "chat"
    began = {"at": time.time() - 30}
    monkeypatch.setattr(kick, "channel", lambda slug: {"id": 9, "slug": slug, "name": "N", "records": True, "feed": "f", "live": {"id": "1", "title": "", "started_at": began["at"]}})
    monkeypatch.setattr(kick, "live_recording", lambda slug: None)
    assert watch.start_channel("kick.com/name", cfg) is None  # half a minute in: Kick may list the recording any moment
    assert not list((tmp_path / "work").glob("kick_live*")) if (tmp_path / "work").exists() else True
    began["at"] = time.time() - 1000
    monkeypatch.setattr(kick.KickChatReader, "start", lambda self: None)
    monkeypatch.setattr("clipper.buffer.LiveBuffer.start", lambda self: None)
    w = watch.start_channel("kick.com/name", cfg)
    assert w is not None and w.buffer is not None and w.source.video_id == "live1"  # long after the start: the live copy it is


def test_the_live_reader_reads_past_a_page_with_entries_that_are_not_chat_and_says_when_it_cannot_catch_up(tmp_path, monkeypatch, caplog):
    import time

    now = time.time()
    held = [now - 100 + i * 0.01 for i in range(10)]
    r = kick.KickChatReader("name", 1, tmp_path / "chat.json", now - 1000)

    def page(cid, before=None, tries=3):
        limit = before / 1e6 if before else float("inf")
        rows = sorted((t for t in held if t < limit), reverse=True)[: kick.PAGE]
        out = [{"id": f"m{t}", "at": float(int(t)), "author": "a", "text": "x"} for t in rows[:-1 or None]]  # one entry of each page is not chat
        return out, (int(rows[-1] * 1e6) if rows else None)

    monkeypatch.setattr(kick, "chat_page", page)
    r.fresh()
    r.connected_at = now
    held.extend(now + i * 0.001 for i in range(70))
    assert len(r.fresh()) >= 66  # three pages back, though every page came one short
    held.extend(now + 1 + i * 0.0001 for i in range(kick.PAGE * (r.MAX_PAGES + 4)))
    with caplog.at_level("WARNING"):
        r.fresh()
    assert "faster than it can be read" in caplog.text


def test_a_finished_kick_stream_credits_the_channels_address(tmp_path):
    from pathlib import Path

    import clipper

    code = (Path(clipper.__file__).parent / "pipeline.py").read_text(encoding="utf-8")
    find = code[code.index("def stage_find"): code.index("def stage_find") + 9000]
    assert '"channel_handle": meta.get("channel_id", "") if source.platform in ("youtube", "kick") else ""' in find


def test_pages_that_overlap_do_not_count_a_message_twice_or_stop_the_catching_up(tmp_path, monkeypatch):
    """Kick pages are cut at a time, so the message on the cut can come on both pages. It is one message, and the
    reader must still go on to the older pages behind it."""
    r = kick.KickChatReader("name", 1, tmp_path / "chat.json", 0.0)
    r.connected_at = 1.0
    r._seen = {"m0": None}
    ids = [f"m{i}" for i in range(60, 0, -1)]  # newest first; m0 was read before
    pages = {None: ids[:25], 200: ids[24:49], 100: ids[48:] + ["m0"]}  # each page starts with the last one of the page before

    def page(cid, before=None):
        nxt = {None: 200, 200: 100, 100: None}[before]  # cursors go back in time
        return [{"id": i, "at": float(i[1:]), "author": "a", "text": "x"} for i in pages[before]], nxt

    monkeypatch.setattr(kick, "chat_page", page)
    new = r.fresh()
    assert [m["id"] for m in new] == [f"m{i}" for i in range(1, 61)]
