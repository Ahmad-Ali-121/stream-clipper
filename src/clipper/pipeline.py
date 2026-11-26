"""The stages, in order: find -> fetch -> transcribe -> review -> render -> post.

Each stage is idempotent: it skips work whose output already exists, so any
stage can be re-run after a crash, a dropped connection or a reboot and it
continues from where it stopped.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from pathlib import Path

from . import progress
from .chat import load_chat
from .config import Config
from .fetch import NoChatError, fetch_chat, fetch_meta, fetch_range, probe
from .moments import find_moments, format_time
from .sources import Source, parse_source
from .tools import ToolError
from .work import clip_dir, clip_dirs, clip_id, done, free_gb, read_json, remove, save_clip, update_clip, work_dir, write_json

log = logging.getLogger(__name__)

MIN_FREE_GB = 2.0


class PipelineError(RuntimeError):
    pass


def open_work(url: str, cfg: Config) -> tuple[Source, Path]:
    source = parse_source(url)
    return source, work_dir(cfg.data_dir, source)


def _clip_state(d: Path) -> dict | None:
    """A clip's record, or None when it really is damaged. A file that only cannot be opened this instant (another
    program is putting its copy in place) is tried again: a good clip must never be called damaged."""
    for attempt in range(10):
        try:
            state = read_json(d / "clip.json")
            return state if isinstance(state, dict) and state else None
        except OSError:
            time.sleep(0.1)
        except ValueError:
            return None
    return None


def select_clips(work: Path, only: list[str] | None = None, status: str | None = None) -> list[Path]:
    """Clip folders ordered best-first, optionally limited to given ids and/or a status."""
    dirs = clip_dirs(work)
    if only:
        wanted = set(only)
        missing = wanted - {d.name for d in dirs}
        if missing:
            raise PipelineError(f"no such clip(s): {', '.join(sorted(missing))}. Known: {', '.join(d.name for d in dirs)}")
        dirs = [d for d in dirs if d.name in wanted]
    if status:
        dirs = [d for d in dirs if read_json(d / "clip.json", {}).get("status") == status]
    return sorted(dirs, key=lambda d: read_json(d / "clip.json", {}).get("rank", 999))


def stage_find(
    url: str,
    cfg: Config,
    top: int | None = None,
    mode: str | None = None,
    refresh_chat: bool = False,
    signals: str | None = None,
    ai: bool = False,
) -> tuple[Path, list[dict]]:
    """Download metadata and chat, score moments, and create a clip folder per moment.

    ``signals`` (chat, audio, video, all) also measures the stream's sound and/or picture and adds moments chat missed.
    """
    source, work = open_work(url, cfg)
    t0 = time.monotonic()
    no_chat = False
    # Details saved while the stream was live say "live" for ever. Ask again: it may have ended since.
    if (read_json(work / "meta.json", {}) or {}).get("is_live"):
        (work / "meta.json").unlink(missing_ok=True)
    meta = fetch_meta(source, work, cfg)
    chat_file = work / "chat.json"
    if not meta.get("is_live") and chat_file.exists() and chat_file.read_text(encoding="utf-8", errors="replace").lstrip().startswith('{"t"'):
        # This chat was collected by the live watcher, so it only covers the part that was watched.
        # The stream has ended: put it aside and download the whole chat replay.
        chat_file.replace(work / "chat.watched.json")
        log.info("the stream has ended; downloading its whole chat instead of the part collected while watching")
    if source.platform == "local":
        from .signals import MODES as _MODES, parse_mode as _parse_mode

        if not ai and not any(_MODES[_parse_mode(signals or cfg.moments.signals)]):
            raise PipelineError("a video file has no chat to find moments from. Choose sound, picture or AI reading, "
                                "or use \"Pick the part myself\" under New clips to choose the range yourself")
        messages = []
    else:
        progress.stage("chat")
        try:
            chat_path = fetch_chat(source, work, cfg, refresh=refresh_chat)
            messages = load_chat(chat_path)
            if not messages:
                raise PipelineError("the chat log is empty; nothing to score")
        except (NoChatError, PipelineError) as e:
            # A YouTube upload (not a stream) has no chat. Its words are free to read, so moments can still be
            # found from what is said, and from sound, picture or AI reading when those were chosen.
            from . import ytcaptions
            from .signals import MODES as _MODES, parse_mode as _parse_mode

            if source.platform == "kick":
                # An old Kick video can have lost its chat. Sound, picture and AI reading still work on it.
                if not ai and not any(_MODES[_parse_mode(signals or cfg.moments.signals)]):
                    raise PipelineError(f"{e}. Choose sound, picture or AI reading to find its moments, "
                                        "or use \"Pick the part myself\" under New clips to choose the range yourself") from e
                log.warning("%s. Moments are found from what you chose instead.", e)
                messages = []
            elif source.platform == "youtube" and not meta.get("is_live") and ytcaptions.video_words(source.url, work, cfg):
                log.warning("%s Moments are found from what is said instead%s.", e,
                            ", plus what you chose" if ai or (signals or cfg.moments.signals) not in ("chat", "A") else "")
                messages, no_chat = [], True
            elif ai or any(_MODES[_parse_mode(signals or cfg.moments.signals)]):
                # No chat to count (an empty one, or a video that never had any), but the person chose sound, picture
                # or AI reading: those do not need chat.
                log.warning("%s. Moments are found from what you chose instead.", e)
                messages = []
            else:
                raise
    overrides = {k: v for k, v in (("top", top), ("mode", mode)) if v is not None}
    settings = replace(cfg.moments, **overrides)
    settings.validate()
    # A stream that is still live reports a stale length, which would cut clips short; ignore it then.
    live = refresh_chat or meta.get("is_live")
    moments = find_moments(messages, settings, duration_s=None if live else meta.get("duration_s")) if messages else []
    log.info(
        "%s: %d chat messages over %s, %d moments, %.0fs",
        meta.get("channel") or source.key, len(messages), format_time(messages[-1].t if messages else 0), len(moments), time.monotonic() - t0,
    )
    found = [dict(m.to_dict(), trigger="chat") for m in moments]
    from .signals import MODES, add_signal_moments, measure_vod, parse_mode, signal_peaks

    audio, video = MODES[parse_mode(signals or cfg.moments.signals)]
    if audio or video:
        if live:
            log.warning("this stream is still live, so its sound and picture are not measured here; use watch for that")
        else:
            progress.stage("measure")
            path = measure_vod(source.url, work, cfg, audio, video)
            peaks = signal_peaks(path, cfg, since_s=settings.skip_start_s)
            peaks = {k: v for k, v in peaks.items() if (k == "audio" and audio) or (k == "video" and video)}
            extra = max(2, settings.top // 2)
            found = add_signal_moments(found, peaks, extra)
            agree = sum(1 for m in found if "+" in m["trigger"])
            log.info("sound and picture: %d unusual moment(s); %d agree with chat, %d added that chat missed (at most %d)",
                     sum(len(v) for v in peaks.values()), agree, sum(1 for m in found if not m["trigger"].startswith("chat")), extra)
    if source.platform == "youtube" and not live:
        # What is said costs nothing to read when YouTube has the captions: moments where exclamations pile up.
        from . import said, ytcaptions

        spoken = ytcaptions.video_words(source.url, work, cfg)
        if spoken:
            shouts = said.peaks(spoken, since_s=settings.skip_start_s)
            before = sum(1 for m in found if not m["trigger"].startswith("chat"))
            # With chat, words add a few moments chat missed. Without chat they are the main way: up to the number asked for.
            found = add_signal_moments(found, {"said": shouts}, (settings.top if no_chat else max(2, settings.top // 2)) + before)
            log.info("what is said: %d moment(s) with excited words; %d agree with ones already found, %d added",
                     len(shouts), sum(1 for m in found if "said" in m["trigger"] and m["trigger"] != "said"),
                     sum(1 for m in found if m["trigger"] == "said"))
    if ai:
        if live:
            log.warning("this stream is still live, so AI does not read it here; run it again when the stream has ended")
        else:
            from .streamread import add_ai_moments, ai_moments

            progress.stage("read")
            picks = ai_moments(source.url, work, meta, cfg, settings.top)
            before = len(found)
            found = add_ai_moments(found, picks)
            log.info("AI picked %d moment(s) from the speech: %d agree with ones already found, %d are new",
                     len(picks), len(picks) - (len(found) - before), len(found) - before)
    if no_chat and not found:
        raise PipelineError("this video has no chat, and no moment stood out in what is said. Run it again with sound, picture or "
                            "\"Let AI read the whole stream\", or pick the part yourself under New clips: Pick the part myself, or Search what was said")
    write_json(work / "moments.json", found)

    # Clips already reviewed keep their folder and status; ranks of the rest are refreshed.
    for d in clip_dirs(work):
        state = read_json(d / "clip.json", {})
        if state.get("status") == "candidate":
            state["rank"] = 999
            write_json(d / "clip.json", state)
    out: list[dict] = []
    for m in found:
        d = clip_dir(work, clip_id(m["peak_s"]))
        state = read_json(d / "clip.json", {})
        state.update(
            {
                "id": d.name,
                "rank": m["rank"],
                "score": m["score"],
                "activity": m["activity"],
                "messages": m["messages"],
                "trigger": m["trigger"],
                "signals": m.get("signals", {}),
                "ai_pick": m.get("ai_pick", ""),
                "ai_strength": m.get("ai_strength"),
                "peak_s": m["peak_s"],
                "start_s": m["start_s"],
                "end_s": m["end_s"],
                "vod_url": source.url,
                "platform": source.platform,
                "channel": meta.get("channel", ""),
                "channel_handle": meta.get("channel_id", "") if source.platform in ("youtube", "kick") else "",
                # Campaign briefs name the channel, and a Twitch VOD URL does not contain it.
                "source_url": meta.get("channel_url") or source.url,
                "stream_title": meta.get("title", ""),
            }
        )
        state.setdefault("status", "candidate")
        state.setdefault("made_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        write_json(d / "clip.json", state)
        out.append(state)
    return work, out


def _clip_failed(d: Path, step: str, e: Exception, failed: list[str] | None) -> None:
    """A clip that cannot go through a step keeps its reason, shown on the review page; the other clips carry on."""
    from .watch import note_error

    log.error("clip %s: %s failed: %s", d.name, step, e, exc_info=not isinstance(e, (ToolError, PipelineError, ValueError)))
    note_error(d, f"{step} failed: {e}")
    if failed is not None:
        failed.append(d.name)


MAX_PART_S = 300.0  # a single clip longer than five minutes is not a short any more; split it into parts instead
MIN_PART_S = 8.0


def parse_clock(text) -> float:
    """Seconds from "1:02:30", "2:30", "150" or 150."""
    if isinstance(text, (int, float)):
        return float(text)
    parts = str(text).strip().split(":")
    if not parts or len(parts) > 3 or any(not p.strip() for p in parts):
        raise ValueError(f"{text!r} is not a time; write it as hours:minutes:seconds, minutes:seconds or seconds")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"{text!r} is not a time; write it as hours:minutes:seconds, minutes:seconds or seconds") from None
    if any(n < 0 for n in nums):
        raise ValueError("a time cannot be negative")
    return sum(n * 60 ** i for i, n in enumerate(reversed(nums)))


def split_range(start_s: float, end_s: float, parts: int = 1, part_s: float | None = None) -> list[tuple[float, float]]:
    """The (start, end) of each part of a range: ``parts`` equal parts, or parts of ``part_s`` seconds each.

    With ``part_s`` a short leftover at the end is added to the last part, so no part is a few seconds long.
    """
    total = end_s - start_s
    if total <= 0:
        raise ValueError("the end must be after the start")
    if part_s:
        if part_s < MIN_PART_S:
            raise ValueError(f"each part must be at least {MIN_PART_S:g} seconds")
        count = max(1, int(total // part_s))
        edges = [start_s + i * part_s for i in range(count)] + [end_s]
        if total - count * part_s >= MIN_PART_S:  # enough left over for a part of its own
            edges = [start_s + i * part_s for i in range(count + 1)] + [end_s]
    else:
        if parts < 1:
            raise ValueError("the number of parts must be at least 1")
        edges = [start_s + total * i / parts for i in range(parts + 1)]
    segments = [(round(a, 2), round(b, 2)) for a, b in zip(edges, edges[1:])]
    longest = max(b - a for a, b in segments)
    if longest > MAX_PART_S:
        raise ValueError(f"a part would be {longest:.0f} seconds long; split it into more parts (at most {MAX_PART_S:.0f} seconds each)")
    if min(b - a for a, b in segments) < MIN_PART_S:
        raise ValueError(f"a part would be shorter than {MIN_PART_S:g} seconds; use fewer parts")
    return segments


def fresh_meta(source: Source, work: Path, cfg: Config) -> dict:
    """The video's details. Ones saved while it was still live carry the length it had then, so they are asked for again."""
    if (read_json(work / "meta.json", {}) or {}).get("is_live"):
        (work / "meta.json").unlink(missing_ok=True)
    return fetch_meta(source, work, cfg)


def stage_cut(url: str, cfg: Config, start_s: float = 0.0, end_s: float | None = None, parts: int = 1, part_s: float | None = None,
              part_label: bool = True) -> list[Path]:
    """Make draft clips from a range a person chose in a video (a link or a file), whole or split into numbered parts.

    No chat is needed and nothing is searched for: the range is the clip. Each part gets its own draft.
    """
    source, work = open_work(url, cfg)
    meta = fresh_meta(source, work, cfg)
    start_s, end_s = _chosen_range(meta, float(start_s), end_s)
    segments = split_range(start_s, end_s, parts, part_s)
    return _make_own(url, cfg, _own_drafts(source, work, meta, segments, part_label))


def stage_cut_list(url: str, cfg: Config, ranges) -> list[Path]:
    """Make a draft from each of several stretches a person picked in one video (from its transcript, or from a
    chatbot's suggestions). Each is a clip of its own: no "Part 1, Part 2" labels."""
    source, work = open_work(url, cfg)
    meta = fresh_meta(source, work, cfg)
    length = float(meta.get("duration_s") or 0)
    picked: list[tuple[float, float]] = []
    for item in ranges or []:
        try:
            a, b = round(float(item[0]), 2), round(float(item[1]), 2)
        except (TypeError, ValueError, IndexError):
            raise ValueError("each stretch needs a start and an end in seconds") from None
        if not MIN_PART_S - 0.05 <= b - a <= MAX_PART_S + 0.05:
            raise ValueError(f"the stretch {format_time(a)} to {format_time(b)} is {b - a:.0f} seconds long; a clip is {MIN_PART_S:g} to {MAX_PART_S:.0f} seconds")
        if a < 0 or (length and b > length + 1):
            raise ValueError(f"the stretch {format_time(a)} to {format_time(b)} is outside the video ({format_time(length)} long)")
        picked.append((a, min(b, length) if length else b))
    if not picked:
        raise ValueError("no stretch was chosen, so there is nothing to make")
    picked.sort()
    if any(n[0] < p[1] - 0.01 for p, n in zip(picked, picked[1:])):
        raise ValueError("two of the chosen stretches overlap")
    ids: list[str] = []
    for one in picked:  # one at a time, so none is numbered as a part of the others
        ids += _own_drafts(source, work, meta, [one], False)
    return _make_own(url, cfg, ids)


def range_id(start_s: float, end_s: float) -> str:
    return f"{clip_id(start_s)}-{clip_id(end_s)}"


def _chosen_range(meta: dict, start_s: float, end_s: float | None) -> tuple[float, float]:
    length = float(meta.get("duration_s") or 0)
    if end_s is None:
        if not length:
            raise PipelineError("this video's length is not known, so give an end time")
        end_s = length
    if length and end_s > length + 1:
        raise PipelineError(f"the end ({format_time(end_s)}) is after the end of the video ({format_time(length)})")
    end_s = min(float(end_s), length) if length else float(end_s)
    if end_s <= start_s:
        raise PipelineError("the end must be after the start")
    return float(start_s), end_s


def stage_range(url: str, cfg: Config, start_s: float = 0.0, end_s: float | None = None) -> Path:
    """Download one chosen part of a video once, for the cutting board, where a person marks the cuts on a timeline.

    Only this part is downloaded, never the whole video, and at most ``fetch.max_range_minutes`` of it.
    """
    source, work = open_work(url, cfg)
    meta = fresh_meta(source, work, cfg)
    a, b = _chosen_range(meta, start_s, end_s)
    if b - a < MIN_PART_S:
        raise PipelineError(f"this part is {b - a:.0f} seconds long; a clip needs at least {MIN_PART_S:g} seconds")
    most = cfg.fetch.max_range_minutes * 60
    if b - a > most + 1:
        raise PipelineError(
            f"this part is {format_time(b - a)} long; the cutting board takes at most {cfg.fetch.max_range_minutes:g} minutes at a time "
            "(fetch.max_range_minutes in config.yaml). Choose a shorter part, or do it in two goes."
        )
    d = work / "ranges" / range_id(a, b)
    d.mkdir(parents=True, exist_ok=True)
    out, note = d / "range.mp4", d / "range.json"
    info = read_json(note, {})
    if not done(out) or info.get("range") != [a, b]:
        if free_gb(cfg.data_dir) < MIN_FREE_GB:
            raise PipelineError(f"less than {MIN_FREE_GB:.0f} GB free on {cfg.data_dir.anchor}; delete old work folders before fetching more")
        for f in d.iterdir():
            if f.is_file():
                f.unlink()
        progress.stage("download", f"{format_time(b - a)} of video")
        log.info("downloading %s to %s of %s (%s) for the cutting board", format_time(a), format_time(b), meta.get("title") or source.url, format_time(b - a))
        t0 = time.monotonic()
        fetch_range(source, a, b, out, cfg)
        log.info("downloaded in %.0fs (%.0f MB)", time.monotonic() - t0, out.stat().st_size / 1e6)
    pr = probe(out, cfg)
    write_json(note, {
        "id": d.name, "range": [a, b], "start_s": a, "end_s": b, "duration_s": round(pr["duration_s"], 2), "width": pr["width"], "height": pr["height"],
        "url": source.url, "platform": source.platform, "title": meta.get("title", ""), "channel": meta.get("channel", ""), "ready": True,
    })
    log.info("ready on the cutting board: %s", d.name)
    return out


def check_segments(segments, length: float) -> list[tuple[float, float]]:
    """The pieces marked on the cutting board (seconds from the start of the downloaded part), checked and in order."""
    out: list[tuple[float, float]] = []
    for seg in segments or []:
        try:
            a, b = round(float(seg[0]), 2), round(float(seg[1]), 2)
        except (TypeError, ValueError, IndexError):
            raise ValueError("each piece needs a start and an end in seconds") from None
        if not (0 <= a < b <= length + 0.5):
            raise ValueError(f"a piece ({a:g} to {b:g} seconds) is outside the downloaded part ({length:g} seconds long)")
        if b - a < MIN_PART_S - 0.05:
            raise ValueError(f"piece {len(out) + 1} is {b - a:.1f} seconds long; a clip needs at least {MIN_PART_S:g} seconds. Move or remove a cut.")
        if b - a > MAX_PART_S + 0.05:
            raise ValueError(f"piece {len(out) + 1} is {b - a:.0f} seconds long; a clip can be at most {MAX_PART_S:.0f} seconds. Add a cut inside it.")
        out.append((a, min(b, length)))
    if not out:
        raise ValueError("no pieces are chosen, so there is nothing to make")
    out.sort()
    if any(n[0] < p[1] - 0.01 for p, n in zip(out, out[1:])):
        raise ValueError("two pieces overlap")
    return out


def stage_cut_range(url: str, cfg: Config, rid: str, segments, part_label: bool = True) -> list[Path]:
    """Make draft clips from the pieces marked on the cutting board, cut out of the part that is already downloaded."""
    source, work = open_work(url, cfg)
    d = work / "ranges" / rid
    info = read_json(d / "range.json", {})
    if not info.get("ready") or not done(d / "range.mp4"):
        raise PipelineError(f"the downloaded part {rid} is not there any more; download it again under New clips, Pick the part myself")
    meta = fresh_meta(source, work, cfg)
    base = float(info["start_s"])
    pieces = [(round(base + a, 2), round(base + b, 2)) for a, b in check_segments(segments, float(info["duration_s"]))]
    return _make_own(url, cfg, _own_drafts(source, work, meta, pieces, part_label, {"range_id": rid}))


def _own_drafts(source: Source, work: Path, meta: dict, segments: list[tuple[float, float]], part_label: bool, extra: dict | None = None) -> list[str]:
    """Write the clip.json of each piece a person chose. Returns the clip ids, in order."""
    ids: list[str] = []
    for n, (a, b) in enumerate(segments, 1):
        d = clip_dir(work, clip_id(a))
        state = read_json(d / "clip.json", {})
        if state and state.get("trigger") != "manual" and not state.get("source_range"):
            # That folder belongs to a clip the program found at the very same second: yours gets a folder of its own.
            d = clip_dir(work, clip_id(a) + "x")
            state = read_json(d / "clip.json", {})
        before_range = state.get("source_range")
        if state.get("source_range") != [a, b]:  # another range under the same name: its old files do not belong to it
            for f in d.iterdir() if d.exists() else []:
                if f.is_file() and f.name != "clip.json":
                    try:
                        f.unlink()
                    except PermissionError:
                        raise PipelineError(f"an older clip that starts at the same second ({d.name}) is in use: its file {f.name} is open, "
                                            "probably playing on the review page. Pause it or reload the page, then try again.") from None
            state = {}  # a different clip: it has not been reviewed, whatever the old one was
        reopen = False
        if state.get("status") == "deleted":
            # Deleted, then asked for again by a person: it comes back for review instead of being skipped without a word.
            state.pop("status")
            state.pop("exported", None)
            reopen = True
        elif state.get("status") == "rejected":
            # A rejection is a decision about this very stretch: it stays, and the log says why nothing is made.
            log.warning("clip %s (%s to %s) was rejected before, so it is left rejected. Press \"Back to review\" on it in the Rejected list if you want it again.",
                        d.name, format_time(a), format_time(b))
        # The very same piece asked for again: a trim the person made on it since is kept (its video is cut that way).
        trim = {} if reopen else {k: state[k] for k in ("cut_start_s", "cut_end_s", "cut_auto") if k in state}
        state.update({
            "id": d.name, "rank": n, "score": 0, "activity": 0, "messages": 0, "trigger": "manual", "signals": {},
            "peak_s": a, "start_s": a, "end_s": b, "cut_start_s": a, "cut_end_s": b, "cut_auto": False, "source_range": [a, b],
            "part": n if len(segments) > 1 else None, "parts": len(segments) if len(segments) > 1 else None, "part_label": bool(part_label),
            "vod_url": source.url, "platform": source.platform, "channel": meta.get("channel", ""),
            "channel_handle": meta.get("channel_id", "") if source.platform in ("youtube", "kick") else "",
            "source_url": meta.get("channel_url") or source.url, "stream_title": meta.get("title", ""),
            "range_id": None, **(extra or {}),
        })
        state.update(trim)
        state.setdefault("status", "candidate")
        state.setdefault("made_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        write_json(d / "clip.json", state, reopen=reopen or state.get("source_range") != before_range)
        ids.append(d.name)
    log.info("%d clip(s) to make from %s to %s of %s", len(ids), format_time(segments[0][0]), format_time(segments[-1][1]), meta.get("title") or source.url)
    return ids


def _make_own(url: str, cfg: Config, ids: list[str]) -> list[Path]:
    failed: list[str] = []
    stage_fetch(url, cfg, only=ids, failed=failed)
    stage_transcribe(url, cfg, only=ids, failed=failed)
    stage_write(cfg, url, only=ids)
    made = stage_render(url, cfg, only=ids, failed=failed)
    if failed:
        log.warning("%d draft(s) made; %d could not be made and show their reason on the review page: %s", len(made), len(set(failed)), ", ".join(sorted(set(failed))))
    return made


def stage_fetch(url: str, cfg: Config, only: list[str] | None = None, failed: list[str] | None = None) -> list[Path]:
    """Download each selected clip's range (plus padding) as source.mp4."""
    source, work = open_work(url, cfg)
    fetched: list[Path] = []
    dirs = select_clips(work, only)
    progress.stage("download")
    for n, d in enumerate(dirs):
        progress.item(n, len(dirs), f"clip {n + 1} of {len(dirs)}")
        try:
            out = _fetch_one(source, d, cfg)
        except PipelineError:
            raise  # the disk is full: no point trying the rest
        except Exception as e:
            _clip_failed(d, "download", e, failed)
            continue
        if out:
            fetched.append(out)
    return fetched


def _fetch_one(source: Source, d: Path, cfg: Config) -> Path | None:
    state = read_json(d / "clip.json")
    if state.get("status") in ("rejected", "deleted"):
        return None
    out = d / "source.mp4"
    if done(out) and "fetch_start_s" not in state:
        # A video with no record of where in the stream it starts (an older version stopped at the wrong moment):
        # every cut made from it would be wrong, so it is downloaded again.
        log.warning("clip %s: its downloaded video has no start time recorded; downloading it again", d.name)
        out.unlink()
    if not done(out):
        if free_gb(cfg.data_dir) < MIN_FREE_GB:
            raise PipelineError(
                f"less than {MIN_FREE_GB:.0f} GB free on {cfg.data_dir.anchor}; delete old work folders before fetching more"
            )
        fetch_start = max(0.0, state["start_s"] - cfg.fetch.pad_s)
        fetch_end = state["end_s"] + cfg.fetch.pad_s
        t0 = time.monotonic()
        part = d.parent.parent / "ranges" / str(state.get("range_id") or "-") / "range.mp4"
        info = read_json(part.with_name("range.json"), {}) if state.get("range_id") else {}
        if info.get("ready") and done(part):
            # Already on this computer: cut it out of the downloaded part instead of downloading it again.
            from .sources import local_source

            base = float(info["start_s"])
            fetch_start, fetch_end = max(base, fetch_start), min(base + float(info["duration_s"]), fetch_end)
            fetch_range(local_source(part), fetch_start - base, fetch_end - base, out, cfg)
        else:
            fetch_range(source, fetch_start, fetch_end, out, cfg)
        # Now: a stop during the next steps must not leave a video with no start time. Only these three are written,
        # onto the clip as it is on disk now: the download took a while and the person may have edited the clip meanwhile.
        state = update_clip(d / "clip.json", {"fetch_start_s": fetch_start, "fetch_end_s": fetch_end, "fetch_seconds": round(time.monotonic() - t0, 1)})
        log.info("clip %s: fetched %.0fs of video in %.0fs (%.1f MB)", d.name, fetch_end - fetch_start, state["fetch_seconds"], out.stat().st_size / 1e6)
    info = probe(out, cfg)
    state.update({"source_width": info["width"], "source_height": info["height"], "source_duration_s": round(info["duration_s"], 2), "source_fps": round(info["fps"], 2)})
    from .layout import auto_layout

    state = auto_layout(d, state, cfg)
    save_clip(d / "clip.json", state)
    return out


def stage_transcribe(url: str, cfg: Config, only: list[str] | None = None, force: bool = False, failed: list[str] | None = None) -> int:
    """Write words.json (word timestamps relative to source.mp4) for each fetched clip."""
    from .transcribe import transcribe_file

    _source, work = open_work(url, cfg)
    count = 0
    dirs = select_clips(work, only)
    progress.stage("captions")
    for n, d in enumerate(dirs):
        progress.item(n, len(dirs), f"clip {n + 1} of {len(dirs)}")
        state = _clip_state(d)
        if not state:
            log.error("clip %s: its own record is damaged, so it is left out; delete it on the review page", d.name)
            continue
        if state.get("status") in ("rejected", "deleted") or not done(d / "source.mp4"):
            continue
        out = d / "words.json"
        if out.exists() and not force:
            continue
        t0 = time.monotonic()
        try:
            words, where = None, "speech model"
            if _source.platform == "youtube" and "fetch_start_s" in state:
                # YouTube has usually written the words down already: a second or two instead of minutes of listening.
                from . import ytcaptions
                from .transcribe import caption_script

                whole = ytcaptions.video_words(_source.url, work, cfg)
                if whole:
                    a = float(state["fetch_start_s"])
                    b = a + float(state.get("source_duration_s") or (float(state.get("fetch_end_s", state["end_s"])) - a))
                    here = ytcaptions.window(whole, a, b)
                    if here:  # none in this stretch (often only "[Music]" over loud gameplay): the speech model listens instead
                        words, where = caption_script(here, cfg), "YouTube's captions"
            if words is None:
                from .fetch import ensure_sound

                ensure_sound(d / "source.mp4", cfg)  # a video with no sound track cannot be listened to at all
                words = transcribe_file(d / "source.mp4", cfg)
            state = read_json(d / "clip.json")  # as it is now: a person may have edited the clip while it was transcribed
            state["words_from"] = where
            state["transcribe_seconds"] = round(time.monotonic() - t0, 1)
            state["transcript"] = " ".join(w["word"] for w in words)
            from . import said
            from .cuts import apply_snap

            heard = said.strength([{**w, "word": w.get("native") or w["word"]} for w in words], float(state.get("source_duration_s") or 0) or 60.0)
            if heard:
                state.setdefault("signals", {})["said"] = max(heard, float((state.get("signals") or {}).get("said") or 0))
            state = apply_snap(state, words, max_shift=cfg.fetch.pad_s)
            (d / "final.mp4").unlink(missing_ok=True)  # the cut may have moved; an older render is stale
            save_clip(d / "clip.json", state)
            write_json(out, words)  # last: its presence is what marks this clip's captions as done
        except Exception as e:
            _clip_failed(d, "captions", e, failed)
            continue
        log.info("clip %s: %d words in %.0fs", d.name, len(words), state["transcribe_seconds"])
        count += 1
    return count


MAX_FETCH_S = 600.0  # the most of a stream one clip keeps on disk: ten minutes
WIDEN_MAX_S = 120.0  # the most that is added to one side at a time


def merge_words(old: list[dict], new: list[dict], shift: float, old_len: float) -> list[dict]:
    """The captions of a clip whose video was made longer.

    ``old`` are the words it had (with whatever the person corrected), ``new`` the words of the longer video. The old
    video now starts ``shift`` seconds into the new one and lasts ``old_len``. Inside that stretch the old words are
    kept, moved to their new times; only before and after it the new words are taken.
    """
    a, b = shift, shift + old_len
    kept = [{**w, "start": round(float(w["start"]) + shift, 3), "end": round(float(w["end"]) + shift, 3)} for w in old]
    if not kept:
        return list(new)
    first, last = kept[0]["start"], kept[-1]["end"]
    head = [w for w in new if float(w["end"]) <= min(a + 0.05, first)]
    tail = [w for w in new if float(w["start"]) >= max(b - 0.05, last)]
    return head + kept + tail


def widen_clip(d: Path, cfg: Config, before: float = 0.0, after: float = 0.0) -> dict:
    """Download more of the stream around one clip: ``before`` seconds earlier and ``after`` seconds later than it has.

    The clip itself does not move: its start and end stay where they are, there is only more room to drag them into.
    The captions already there are kept (with any corrections); the added seconds are listened to and their words joined on.
    The new video replaces the old one only when everything worked, so a failed download costs nothing.
    """
    import os

    from .transcribe import caption_script, transcribe_file

    try:
        before, after = max(0.0, float(before or 0)), max(0.0, float(after or 0))
    except (TypeError, ValueError):
        raise ValueError("how many seconds to add must be a number") from None
    if before <= 0 and after <= 0:
        raise ValueError("say how many seconds to add before or after")
    if before > WIDEN_MAX_S or after > WIDEN_MAX_S:
        raise ValueError(f"at most {WIDEN_MAX_S:.0f} seconds can be added to one side at a time")
    state = read_json(d / "clip.json")
    work = d.parent.parent
    if not done(d / "source.mp4") or "fetch_start_s" not in state:
        raise ValueError("this clip has no downloaded video yet, so there is nothing to add to")
    meta = read_json(work / "meta.json", {}) or {}
    old_a = float(state["fetch_start_s"])
    old_len = float(state.get("source_duration_s") or (float(state.get("fetch_end_s", state["end_s"])) - old_a))
    old_b = old_a + old_len
    part = work / "ranges" / str(state.get("range_id") or "-") / "range.mp4"
    info = read_json(part.with_name("range.json"), {}) if state.get("range_id") else {}
    from_part = bool(info.get("ready")) and done(part)
    if meta.get("no_vod") and not from_part:
        raise ValueError("this clip comes from a live stream that is not being recorded. The seconds around it were only kept for a few "
                         "minutes while it was live, so there is nothing left to add")
    new_a, new_b = max(0.0, old_a - before), old_b + after
    total = float(meta.get("duration_s") or 0)
    if total and not meta.get("is_live"):
        new_b = min(new_b, total)
    if from_part:
        base = float(info["start_s"])
        new_a, new_b = max(base, new_a), min(base + float(info["duration_s"]), new_b)
    if new_a >= old_a - 0.5 and new_b <= old_b + 0.5:
        raise ValueError("there is nothing more to add on that side: the clip's video already reaches the "
                         + ("edge of the part you downloaded for the cutting board" if from_part else "start or the end of the video"))
    if new_b - new_a > MAX_FETCH_S:
        raise ValueError(f"a clip keeps at most {MAX_FETCH_S / 60:.0f} minutes of the stream; this would be {(new_b - new_a) / 60:.1f}. "
                         "Make a new clip of the part you want instead (New clips, Pick the part myself)")
    if free_gb(cfg.data_dir) < MIN_FREE_GB:
        raise PipelineError(f"less than {MIN_FREE_GB:.0f} GB free on {cfg.data_dir.anchor}; delete old work folders before fetching more")
    source = parse_source(str(state.get("vod_url") or meta.get("url") or ""))
    tmp = d / "source.wide.mp4"
    remove(tmp)
    t0 = time.monotonic()
    log.info("clip %s: fetching more of the stream: %.0fs before and %.0fs after what it has", d.name, old_a - new_a, new_b - old_b)
    try:
        if from_part:
            from .sources import local_source

            fetch_range(local_source(part), new_a - base, new_b - base, tmp, cfg)
        else:
            fetch_range(source, new_a, new_b, tmp, cfg)
        seen = probe(tmp, cfg)
        new_len = float(seen["duration_s"])
        if new_len < old_len + 1:
            raise ToolError(f"the download came back no longer than before ({new_len:.0f} s): that part of the stream cannot be fetched (any more)")
        # the words of the longer video: YouTube's own captions when it has them, else the speech model listens
        words = None
        if source.platform == "youtube" and not from_part:
            from . import ytcaptions

            whole = ytcaptions.video_words(source.url, work, cfg)
            here = ytcaptions.window(whole, new_a, new_a + new_len) if whole else None
            if here:
                words = caption_script(here, cfg)
        if words is None:
            from .fetch import ensure_sound

            ensure_sound(tmp, cfg)
            words = transcribe_file(tmp, cfg)
        had = read_json(d / "words.json", []) if (d / "words.json").exists() else []
        words = merge_words(had, words, old_a - new_a, old_len)
        # only now is anything of the clip touched
        for attempt in range(8):
            try:
                os.replace(tmp, d / "source.mp4")
                break
            except PermissionError:
                if attempt == 7:
                    raise ValueError("the clip's video is open somewhere (playing on the page, or another job is using it). "
                                     "Pause it, wait a moment and press the button again; nothing was changed") from None
                time.sleep(0.4)
    except BaseException:
        remove(tmp)
        raise
    write_json(d / "words.json", words)
    remove(d / "wave.json")  # the sound wave of the trimmer belongs to the old video
    state = update_clip(d / "clip.json", {"fetch_start_s": new_a, "fetch_end_s": new_a + new_len, "source_duration_s": round(new_len, 2),
                                          "fetch_seconds": round(time.monotonic() - t0, 1), "transcript": " ".join(w["word"] for w in words)})
    log.info("clip %s: its video now covers %.0f to %.0f seconds of the stream (was %.0f to %.0f), in %.0fs",
             d.name, new_a, new_a + new_len, old_a, old_b, state["fetch_seconds"])
    return state


def stage_render(url: str, cfg: Config, only: list[str] | None = None, force: bool = False, approved_only: bool = False,
                 failed: list[str] | None = None) -> list[Path]:
    """Render final.mp4 for each selected clip that has a source."""
    from .render import render_clip

    _source, work = open_work(url, cfg)
    rendered: list[Path] = []
    dirs = select_clips(work, only, status="approved" if approved_only else None)
    progress.stage("draft")
    for n, d in enumerate(dirs):
        progress.item(n, len(dirs), f"clip {n + 1} of {len(dirs)}")
        state = _clip_state(d)
        if not state:
            log.error("clip %s: its own record is damaged, so it is left out; delete it on the review page", d.name)
            continue
        if state.get("status") in ("rejected", "deleted") or not done(d / "source.mp4"):
            continue
        try:
            rendered.append(render_clip(d, cfg, force=force))
        except Exception as e:
            _clip_failed(d, "making the draft video", e, failed)
    return rendered


def stage_post(url: str, cfg: Config, only: list[str] | None = None, brief_path: str | None = None) -> list[tuple[Path, bool]]:
    """Copy approved, rendered clips and their post sheets to <data_dir>/ready/<date>/."""
    from .brief import load_brief
    from .post import export_clip

    _source, work = open_work(url, cfg)
    brief = load_brief(brief_path) if brief_path else None
    results = []
    for d in select_clips(work, only, status="approved"):
        results.append(export_clip(d, cfg.data_dir / "ready", brief))
    return results


def all_works(cfg: Config) -> list[Path]:
    root = cfg.data_dir / "work"
    return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def clip_label(state: dict, clip_id: str) -> str:
    """A clip in a few words a person recognises: its title, or the streamer and the clip's number when it has none."""
    from .post import export_stem

    stem, titled = export_stem(state, clip_id)
    return stem if titled else f"{state.get('channel', '')} {clip_id}".strip()


def stage_finish(cfg: Config, url: str | None = None, brief_path: str | None = None,
                 problems: list[str] | None = None, progress=None, only: tuple[str, str] | None = None,
                 stop=None, left: list[str] | None = None) -> list[tuple[Path, bool]]:
    """Render every approved clip that lacks an up-to-date render, then export it with a post sheet.

    Works across every stream, or one if ``url`` is given. Clips already exported
    and unchanged since are skipped, so this is safe to run after every review.
    A clip that cannot be made is described in ``problems`` and the rest carry on.
    ``only`` (work folder name, clip id) makes one clip's videos and leaves the others.
    ``stop()`` is asked before each clip; once it says yes the rest are left as they are, approved, and named in
    ``left``. A tool ended by Stop (``tools.Stopped``) ends the run the same way, in the middle of a clip.
    """
    from .brief import load_brief
    from .tools import Stopped

    works = [open_work(url, cfg)[1]] if url else all_works(cfg)
    brief = load_brief(brief_path) if brief_path else None
    results: list[tuple[Path, bool]] = []
    from .review import videos_made

    todo = [(work, d) for work in works for d in select_clips(work, status="approved")]
    for _work, d in todo:
        st = read_json(d / "clip.json", {})
        if not isinstance(st.get("exports"), dict) and (known := known_exports(st, wants_ab(st, cfg))):
            update_clip(d / "clip.json", {"exports": known})  # exported by an older version: note where its files are
    if only:
        todo = [(work, d) for work, d in todo if (work.name, d.name) == tuple(only)]
    else:
        # The green button makes what is waiting under Approved. A clip the page shows under Done is finished and is
        # left alone: it used to be made again whenever its file had been moved out of the ready folder, the caption
        # look or the opening effects had been changed since, or version B was switched on later (reported by a
        # tester on 2026-11-25). To make a Done clip once more there is a button on its own card.
        todo = [(work, d) for work, d in todo if not videos_made(d, read_json(d / "clip.json", {}))]
    redo = {d for _work, d in todo if only and videos_made(d, read_json(d / "clip.json", {}))}  # asked for again, by its own button
    # What there is to do, counted before anything is made, so the bar on the page moves by real work:
    # one step per video to render, two per explainer (the narrator's card, then joining it to the clip).
    # A clip whose videos are all there already is not counted and not mentioned.
    sizes = []
    for _work, d in todo:
        try:
            st = read_json(d / "clip.json")
            if not isinstance(st.get("exports"), dict) and (known := known_exports(st, wants_ab(st, cfg))):
                update_clip(d / "clip.json", {"exports": known})  # exported by an older version: note where its files are
            jobs = plan_clip(d, st, cfg, brief, again=d in redo)[1]
            if not jobs and st.get("made_all") is False:
                # Marked "stopped half-way", yet nothing of it is left to make: it is finished. Without this it would
                # sit under Approved for ever, with the green button finding nothing to do for it.
                update_clip(d / "clip.json", {}, drop=("made_all",))
            # copying a finished video to the ready folder takes a moment, so only the real work is counted;
            # a clip that only needs copying still counts as one step
            sizes.append(max(1, sum(1 for job in jobs if job[0] != "copy")) if jobs else 0)
        except Exception:  # it will fail again below, where the reason is reported; it still counts as one thing to do
            sizes.append(1)
    todo, sizes = [pair for pair, size in zip(todo, sizes) if size], [size for size in sizes if size]
    from . import progress as bar

    steps = {"done": 0, "total": max(1, sum(sizes)), "clip": ""}

    def step(text: str) -> None:
        """One piece of work starts: the bar moves on and says what is being made."""
        steps["total"] = max(steps["total"], steps["done"] + 1)  # more turned up than was counted: never past 100%
        bar.item(steps["done"], steps["total"], f"{steps['clip']} · {text}")
        steps["done"] += 1

    for n, (work, d) in enumerate(todo):
        label = clip_label(read_json(d / "clip.json", {}), d.name)
        steps["clip"] = f"clip {n + 1} of {len(todo)}: {label}"
        before = steps["done"]
        if read_json(d / "clip.json", {}).get("status") != "approved":
            # rejected, deleted or moved to Pending while the clips before it were being made: that decision counts
            log.info("clip %s/%s is no longer approved; its videos are not made", work.name, d.name)
            steps["done"] = before + sizes[n]
            continue
        if stop and stop():
            if left is not None:
                from .review import videos_made

                left += [clip_label(st, o.name) for _w, o in todo[n:] if not videos_made(o, st := read_json(o / "clip.json", {}))]
            break
        if progress:
            progress(n, len(todo), label)
        if True:
            from .watch import note_error

            try:
                _finish_clip(work, d, cfg, brief, results, step, problems, again=d in redo)
                note_error(d, None)
                steps["done"] = max(steps["done"], before + sizes[n])
            except Stopped:  # Stop now: this clip is left unfinished, with the ones after it
                if left is not None:
                    from .review import videos_made

                    left += [clip_label(st, o.name) for _w, o in todo[n:] if not videos_made(o, st := read_json(o / "clip.json", {}))]
                for half in list(d.glob("*.part.mp4")) + list(d.glob("*.part")) + list(d.glob("*.part.jpg")) + list(d.glob("cover_try*.png")):  # the video that was being written when it ended
                    try:
                        half.unlink(missing_ok=True)
                    except OSError as e:
                        log.warning("clip %s: the half-made file %s could not be removed (%s); the next run replaces it", d.name, half.name, e)
                log.info("making the videos was stopped during clip %s/%s", work.name, d.name)
                return results
            except Exception as e:  # one bad clip must not stop the others
                # an expected failure says everything in its message; only a fault of the program gets its traceback
                log.error("clip %s/%s could not be finished: %s", work.name, d.name, e, exc_info=not isinstance(e, (ToolError, PipelineError)))
                note_error(d, f"finish failed: {e}")
                if problems is not None:
                    problems.append(f"{work.name}/{d.name}: {e}")
                steps["done"] = max(steps["done"], before + 1)  # the steps it did not get to are not left hanging in the count
    else:  # every clip was gone through (not stopped part-way)
        if progress:
            progress(len(todo), len(todo), "")
    return results


def _look_changed(state: dict, cfg: Config) -> bool:
    """Was this clip's draft made with another caption look, template or branding than the ones chosen now?
    Judged by the newest record the draft carries, so older drafts are not all remade because the record changed."""
    from .render import look_signature, look_signature_v1

    if state.get("look2_used"):
        return state["look2_used"] != look_signature(cfg)
    if state.get("look_used"):
        return state["look_used"] != look_signature_v1(cfg)
    return state.get("style_used", "bold") != cfg.captions.preset


VERSION_TEXT = {"a": "the video", "b": "version B", "xa": "the explainer", "xb": "explainer B"}


def plan_clip(d: Path, state: dict, cfg: Config, brief, again: bool = False) -> tuple[bool, list[tuple[str, str, str, str]]]:
    """What "Make videos" has to do for one approved clip, worked out without making anything.

    Returns whether the main video must be rendered again, and the steps in order as (work, version, file, name ending):
    work is "render" (a video is encoded), "card" and "join" (the two halves of an explainer) or "copy" (only put in the
    ready folder). An empty list means every wanted version is made and in the ready folder already.
    """
    if not done(d / "source.mp4"):
        raise ToolError("approved, but its video was never downloaded (the draft failed). Reject it on the review page")
    ab = wants_ab(state, cfg)
    # A render made before the opening effects were switched on (or off) no longer matches the settings.
    fresh = (not done(d / "final.mp4") or bool(state.get("opening_used")) != bool(cfg.render.opening)
             # a render with another caption look or other branding is stale too (older drafts only recorded the style's name)
             or _look_changed(state, cfg))
    # Which versions this clip is to have: the plain video (ticked unless the person unticked it) and the explainer.
    wanted: list[tuple[str, str, str]] = []
    if state.get("simple", True):
        wanted.append(("a", "final.mp4", "_A" if ab else ""))
        if ab:
            wanted.append(("b", "final_b.mp4", "_B"))
    if state.get("explainer"):
        if not (state.get("intro") or "").strip():
            raise ToolError("explainer ticked but there is no narration line; type one on the review page")
        wanted.append(("xa", "explained.mp4", "_explained_A" if ab else "_explained"))
        if ab:
            wanted.append(("xb", "explained_b.mp4", "_explained_B"))
    if not wanted:
        raise ToolError("neither the normal video nor the explainer is ticked for this clip; tick one under 'Versions to make' on the review page")
    known = known_exports(state, ab)
    again = again or bool(brief) or not state.get("exported") or not known  # ``again``: the person asked for this clip once more
    jobs: list[tuple[str, str, str, str]] = []
    b_planned = False
    for key, name, suffix in wanted:
        make = fresh or not done(d / name)
        if not (make or again or not known.get(key) or not Path(known[key]).exists()):
            continue  # made, unchanged and still in the ready folder
        if key in ("b", "xb") and not b_planned and (fresh or not done(d / "final_b.mp4")):
            jobs.append(("render", "b", "final_b.mp4", ""))
            b_planned = True
        if key.startswith("x") and make:
            jobs += [("card", key, name, ""), ("join", key, name, "")]
        jobs.append(("copy", key, name, suffix))
    if fresh and jobs:
        jobs.insert(0, ("render", "a", "final.mp4", ""))
    if (cfg.render.thumbnail != "off" and jobs and not state.get("exported_at") and not done(d / "cover.jpg")
            and not state.get("cover_error_mode") == cfg.render.thumbnail):
        # Thumbnails are switched on and this clip, whose videos are being made for the first time, has none yet
        # (a clip made before and made again, for a campaign, a new caption look or a moved file, is left alone). One made by hand on the
        # clip's card is kept as it is. Clips finished earlier are left alone: switching thumbnails on must not start
        # drawing one for every old clip (with ChatGPT that would spend the plan's allowance by surprise).
        jobs.append(("cover", "a", "cover.jpg", ""))
    return fresh, jobs


def _finish_clip(work: Path, d: Path, cfg: Config, brief, results: list[tuple[Path, bool]], step=None, problems: list[str] | None = None,
                 again: bool = False) -> None:
    from .compose import explained
    from .post import export_clip
    from .render import render_clip

    step = step or (lambda text: None)
    state = read_json(d / "clip.json")
    if not done(d / "source.mp4"):
        raise ToolError("approved, but its video was never downloaded (the draft failed). Reject it on the review page")
    if "source_width" not in state or not done(d / "words.json"):
        # The draft stopped part-way while it was being made; do the missing steps now.
        from .watch import prepare_clip

        log.info("clip %s/%s: the draft was never completed; completing it now", work.name, d.name)
        step("completing the draft")
        state = prepare_clip(d, state, cfg)
    state = write_clip_copy(d, state, cfg)  # no-op unless its text was written without AI and AI is reachable now
    ab = wants_ab(state, cfg)
    fresh, jobs = plan_clip(d, state, cfg, brief, again=again)
    known = known_exports(state, ab)
    if not isinstance(state.get("exports"), dict) and known:
        update_clip(d / "clip.json", {"exports": known})  # a clip exported by an older version: note where its files are
    ready = cfg.data_dir / "ready"
    first = "a" if state.get("simple", True) else "xa"  # the version the clip's own record and post sheet describe
    if jobs:
        # Until every step below has ended the clip is not finished, whatever has been exported of it so far:
        # stopped or failed part-way, it stays under Approved and the green button completes it.
        update_clip(d / "clip.json", {"made_all": False})
    narrated: set[str] = set()
    if jobs and all(job[0] == "copy" for job in jobs):
        step("putting it in the ready folder")
    for kind, key, name, suffix in jobs:
        if kind == "render":
            step(f"rendering {VERSION_TEXT[key]}")
            if key == "a":
                render_clip(d, cfg, force=True)
                for old in ("explained.mp4", "explained_b.mp4", "final_b.mp4"):  # made with the old settings too
                    (d / old).unlink(missing_ok=True)
            else:
                render_clip(d, cfg, out_name=name, variant="b")
        elif kind == "card":
            if key not in narrated:  # the card and the join are one call; it says itself when the second half starts
                narrated.add(key)
                step(f"{VERSION_TEXT[key]}: the narrator's card")
                explained(d, cfg, variant=key[1], on_step=lambda: step(f"{VERSION_TEXT[key]}: joining it to the clip"))
        elif kind == "copy":
            if key.startswith("x") and key not in narrated:
                explained(d, cfg, variant=key[1])  # made earlier: this only gives the file back
            results.append(export_clip(d, ready, brief, video_name=name, suffix=suffix, main=key == first))
        elif kind == "cover":
            # Optional extra: a thumbnail that cannot be made never costs the clip its videos. The reason is kept on the
            # clip and reported; it is tried again when the person presses the button on the clip's card.
            from . import thumbs
            from .tools import Stopped

            # The videos are all made at this point; the thumbnail is an extra. A stop or a crash while it is being
            # drawn must not leave the clip marked as half-made.
            update_clip(d / "clip.json", {}, drop=("made_all",))
            step("making the thumbnail" + (" with ChatGPT (one to three minutes)" if cfg.render.thumbnail == "chatgpt" else ""))
            try:
                thumbs.make_cover(d, cfg, cfg.render.thumbnail)
            except Stopped:
                raise
            except thumbs.CoverBusy as e:  # the person is making this clip's thumbnail by hand right now: theirs is the one
                log.info("clip %s/%s: %s", work.name, d.name, e)
            except Exception as e:
                log.error("clip %s/%s: the thumbnail could not be made: %s", work.name, d.name, e, exc_info=not isinstance(e, (ToolError, ValueError)))
                update_clip(d / "clip.json", {"cover_error": f"{e}", "cover_error_mode": cfg.render.thumbnail})
                if problems is not None:
                    problems.append(f"the thumbnail of {clip_label(state, d.name)}: {e}")
    now = update_clip(d / "clip.json", {}, drop=("made_all",)) if jobs else read_json(d / "clip.json", {})
    if now.get("exported") and done(d / "cover.jpg"):
        from .thumbs import export_cover

        export_cover(d, Path(now["exported"]))  # the thumbnail sits beside the main video, under the same name


def wants_ab(state: dict, cfg: Config) -> bool:
    """Whether this clip comes out twice, as versions A and B. Each clip chooses for itself (``ab`` in its clip.json,
    the tick box in its editor); a clip that never chose follows render.ab_versions."""
    own = state.get("ab")
    return bool(cfg.render.ab_versions if own is None else own)


def known_exports(state: dict, ab: bool) -> dict[str, str]:
    """Where each version of a clip was exported: {"a": path, "b": ..., "xa": ..., "xb": ...}.

    Clips exported before this was recorded only kept the main file's path; the others are looked for beside it under
    the names they were given then. Empty when the main file was made without A/B and A/B is wanted now (as before:
    everything is exported again).
    """
    if isinstance(state.get("exports"), dict):
        return dict(state["exports"])
    if not state.get("exported"):
        return {}
    old = Path(state["exported"])
    if ab and not old.stem.endswith("_A"):
        return {}
    out = {"a": str(old)}
    base = old.stem[:-2] if old.stem.endswith("_A") else old.stem
    for key, suffix in (("b", "_B"), ("xa", "_explained_A" if old.stem.endswith("_A") else "_explained"), ("xb", "_explained_B")):
        other = old.with_name(f"{base}{suffix}.mp4")
        if other.exists():
            out[key] = str(other)
    return out


COPY_FIELDS = ("hook", "hook_b", "title", "title_b", "description", "tags", "intro", "intro_b", "rank_line")
# Files that show a piece of text, so they are stale once that text changes.
SHOWS = {"hook": ("final.mp4", "explained.mp4"), "hook_b": ("final_b.mp4", "explained_b.mp4"),
         "intro": ("explained.mp4", "intro_card.mp4", "explained_b.mp4", "intro_card_b.mp4"), "intro_b": ("explained_b.mp4", "intro_card_b.mp4")}


def write_clip_copy(d: Path, state: dict, cfg: Config, force: bool = False) -> dict:
    """Fill a clip's hook, title, description, hashtags and narration lines.

    Uses Claude when a key is configured, otherwise plain text from the
    transcript. Hashtags come from live YouTube data when that lookup works.
    Text a person typed (on the review page or in clip.json) is never replaced:
    only fields that are empty, or that this function wrote earlier, are filled.
    """
    from . import llm
    from .trends import trending

    # Text written without AI (no key then, or the call failed) is redone once AI can be reached.
    # ... and text written by the small local AI is redone once Claude can be reached.
    redo_plain = ((state.get("copy_source") == "plain" and llm.text_available(cfg))
                  or (str(state.get("copy_source") or "").startswith("local") and llm.available(cfg)))
    if state.get("copy_at") and not force and not redo_plain:
        return state
    disk_before = read_json(d / "clip.json", {})  # to tell, after the slow AI call, what a person changed meanwhile
    tags: list[str] = []
    if cfg.trends.enabled:
        try:
            ranked = trending([state.get("channel", "")] + list(cfg.trends.seeds), cfg, top=12)
            tags = [r["tag"] for r in ranked]
            state["trend_tags"] = tags
        except Exception as e:  # research is a nice-to-have; a clip must not fail because of it
            log.warning("clip %s: hashtag research failed (%s)", d.name, e)
    frames: list = []
    if llm.available(cfg) and cfg.llm.look_at_frames:
        try:
            from .render import clip_frames

            frames = clip_frames(d, cfg)
        except Exception as e:  # the pictures help the rating; the text must not fail because of them
            log.warning("clip %s: no frames for AI to look at (%s); it will judge from the words only", d.name, e)
    try:
        copy = llm.write_copy(state, tags, cfg, frames)
        state["copy_source"] = f"local: {cfg.llm.local_model}" if llm.used_last() == "local" else cfg.llm.model
        state["ai_saw_frames"] = len(frames)
        state.pop("copy_problem", None)
    except llm.LlmUnavailable as e:
        log.warning("clip %s: text written WITHOUT AI: %s", d.name, e)
        copy = llm.fallback_copy(state, tags, cfg.trends.max_tags)
        state["copy_source"] = "plain"
        state["copy_problem"] = str(e)  # shown on the review page
    # The call took a while. Text a person typed and saved meanwhile is theirs: carry on from the clip as it is now.
    now = read_json(d / "clip.json", {})
    for key in set(now) | set(disk_before):
        if now.get(key) != disk_before.get(key):  # changed on disk during the call: that is the newer truth
            if key in now:
                state[key] = now[key]
            else:
                state.pop(key, None)
    auto = set(state.get("copy_auto") or [])
    before = {k: state.get(k) for k in SHOWS}
    for key in COPY_FIELDS:
        if not state.get(key) or key in auto:
            state[key] = copy[key]
            auto.add(key)
    state["copy_auto"] = sorted(auto)
    state["ai_keep"], state["ai_reason"], state["ai_rating"] = copy["keep"], copy["reason"], copy.get("rating")
    state["copy_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    for key, files in SHOWS.items():
        if state.get(key) != before[key]:  # the text is burned in, so the old render is stale
            for name in files:
                remove(d / name)
    save_clip(d / "clip.json", state)
    return state


def stage_write(cfg: Config, url: str | None = None, only: list[str] | None = None, force: bool = False) -> int:
    """Write text for every clip that has a transcript and is not rejected."""
    works = [open_work(url, cfg)[1]] if url else all_works(cfg)
    count = 0
    todo = [d for work in works for d in select_clips(work, only if url else None)]
    progress.stage("text")
    for n, d in enumerate(todo):
        if True:
            progress.item(n, len(todo), f"clip {n + 1} of {len(todo)}")
            state = _clip_state(d)
            if not state:
                log.error("clip %s: its own record is damaged, so it is left out; delete it on the review page", d.name)
                continue
            if state.get("status") in ("rejected", "deleted") or "transcript" not in state:
                continue
            if state.get("copy_at") and not force:
                continue
            try:
                write_clip_copy(d, state, cfg, force=force)
            except Exception as e:  # one clip's text must not stop the drafts of the others
                _clip_failed(d, "writing the text", e, None)
                continue
            count += 1
    return count


def stage_explain(url: str, cfg: Config, only: list[str] | None = None, force: bool = False) -> list[Path]:
    """Make the narrated 'explained' version of approved clips and copy it to the ready folder."""
    import shutil

    from .compose import explained

    _source, work = open_work(url, cfg)
    out = []
    for d in select_clips(work, only, status=None if only else "approved"):
        state = read_json(d / "clip.json")
        if state.get("status") in ("rejected", "deleted"):
            continue
        video = explained(d, cfg, force=force)
        day = cfg.data_dir / "ready" / time.strftime("%Y-%m-%d")
        day.mkdir(parents=True, exist_ok=True)
        target = day / f"{''.join(c for c in state.get('channel', 'clip') if c.isalnum())}_{d.name}_explained.mp4"
        shutil.copyfile(video, target)
        out.append(target)
    return out


def stage_top(cfg: Config, url: str | None = None, count: int = 5, opener: str = "", include_unapproved: bool = False) -> Path:
    """Build a 'top N moments' countdown from the best clips of one stream or of every stream."""
    from .compose import top_moments

    if count < 2:
        raise PipelineError(f"--count must be 2 or more (a countdown needs at least two clips); it is {count}")
    works = [open_work(url, cfg)[1]] if url else all_works(cfg)
    pool = []
    for work in works:
        for d in clip_dirs(work):
            state = read_json(d / "clip.json", {})
            if not state:
                log.warning("clip %s/%s is left out: its clip.json cannot be read", work.name, d.name)
                continue
            ok = state.get("status") == "approved" or (include_unapproved and state.get("status") == "candidate" and state.get("ai_keep", True))
            if ok and done(d / "source.mp4"):
                pool.append((float(state.get("score") or 0), d))
    pool.sort(key=lambda x: -x[0])
    chosen = [d for _s, d in pool[:count]]
    if len(chosen) < 2:
        raise PipelineError(
            f"only {len(chosen)} usable clip(s). Approve at least 2 on the review page first"
            + ("" if include_unapproved else ", or add --include-unapproved to use drafts")
        )
    day = cfg.data_dir / "ready" / time.strftime("%Y-%m-%d")
    name = works[0].name if url else "all"
    out = day / f"top{len(chosen)}_{name}_{time.strftime('%H%M')}.mp4"
    video = top_moments(chosen, out, cfg, opener=opener)
    states = [read_json(d / "clip.json") for d in chosen]
    channels = sorted({s.get("channel", "") for s in states if s.get("channel")})
    tags, seen = [], set()
    for s in states:
        for t in s.get("tags") or []:
            if t.lower() not in seen:
                seen.add(t.lower())
                tags.append(t)
    lines = [
        f"VIDEO FILE: {video.name}",
        f"FORMAT: top {len(chosen)} moments countdown ({', '.join(channels)})",
        "",
        f"Title idea: Top {len(chosen)} {' / '.join(channels)} moments",
        "Moments, in the order shown:",
    ] + [f"  #{len(chosen) - i}  {s.get('title') or s.get('hook') or s['id']}  ({s.get('vod_url')})" for i, s in enumerate(reversed(states))] + [
        "",
        "Hashtags: " + " ".join(tags[:8]),
        "Credit: " + ", ".join(sorted({s.get('source_url', '') for s in states})),
    ]
    from .work import write_text

    write_text(video.with_suffix(".txt"), "\n".join(lines) + "\n")
    return video


def stage_clean(cfg: Config, dry_run: bool = False) -> tuple[int, float]:
    """Free disk space by deleting the video files of rejected clips.

    Only rejected clips are touched, and only their media (source.mp4,
    final.mp4, captions): clip.json and the transcript stay, so the record of
    what was rejected and why is kept. Returns (files removed, megabytes freed).
    """
    count, freed = 0, 0.0
    for work in all_works(cfg):
        for d in clip_dirs(work):
            if read_json(d / "clip.json", {}).get("status") not in ("rejected", "deleted"):
                continue
            for name in ("source.mp4", "final.mp4", "captions.ass", "source.part.mp4", "final.part.mp4"):
                f = d / name
                if f.exists():
                    freed += f.stat().st_size / 1e6
                    count += 1
                    if not dry_run:
                        f.unlink()
    return count, freed


def stage_run(url: str, cfg: Config, top: int | None = None, mode: str | None = None, refresh_chat: bool = False,
              signals: str | None = None, ai: bool = False) -> Path:
    """find -> fetch -> transcribe -> render drafts, ready for review."""
    work, _clips = stage_find(url, cfg, top=top, mode=mode, refresh_chat=refresh_chat, signals=signals, ai=ai)
    failed: list[str] = []
    stage_fetch(url, cfg, failed=failed)
    stage_transcribe(url, cfg, failed=failed)
    stage_write(cfg, url)  # opening text, titles and the AI rating, before rendering: the opening text is burned into the draft
    made = stage_render(url, cfg, failed=failed)
    if failed:
        log.warning("%d draft(s) made; %d clip(s) could not be made and show their reason on the review page: %s",
                    len(made), len(set(failed)), ", ".join(sorted(set(failed))))
    return work
