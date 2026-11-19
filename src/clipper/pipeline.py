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

from .chat import load_chat
from .config import Config
from .fetch import fetch_chat, fetch_meta, fetch_range, probe
from .moments import find_moments, format_time
from .sources import Source, parse_source
from .tools import ToolError
from .work import clip_dir, clip_dirs, clip_id, done, free_gb, read_json, work_dir, write_json

log = logging.getLogger(__name__)

MIN_FREE_GB = 2.0


class PipelineError(RuntimeError):
    pass


def open_work(url: str, cfg: Config) -> tuple[Source, Path]:
    source = parse_source(url)
    return source, work_dir(cfg.data_dir, source)


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
    meta = fetch_meta(source, work, cfg)
    chat_path = fetch_chat(source, work, cfg, refresh=refresh_chat)
    messages = load_chat(chat_path)
    if not messages:
        raise PipelineError("the chat log is empty; nothing to score")
    overrides = {k: v for k, v in (("top", top), ("mode", mode)) if v is not None}
    settings = replace(cfg.moments, **overrides)
    settings.validate()
    # A stream that is still live reports a stale length, which would cut clips short; ignore it then.
    live = refresh_chat or meta.get("is_live")
    moments = find_moments(messages, settings, duration_s=None if live else meta.get("duration_s"))
    log.info(
        "%s: %d chat messages over %s, %d moments, %.0fs",
        meta.get("channel") or source.key, len(messages), format_time(messages[-1].t), len(moments), time.monotonic() - t0,
    )
    found = [dict(m.to_dict(), trigger="chat") for m in moments]
    from .signals import MODES, add_signal_moments, measure_vod, parse_mode, signal_peaks

    audio, video = MODES[parse_mode(signals or cfg.moments.signals)]
    if audio or video:
        if live:
            log.warning("this stream is still live, so its sound and picture are not measured here; use watch for that")
        else:
            path = measure_vod(source.url, work, cfg, audio, video)
            peaks = signal_peaks(path, cfg, since_s=settings.skip_start_s)
            peaks = {k: v for k, v in peaks.items() if (k == "audio" and audio) or (k == "video" and video)}
            extra = max(2, settings.top // 2)
            found = add_signal_moments(found, peaks, extra)
            agree = sum(1 for m in found if "+" in m["trigger"])
            log.info("sound and picture: %d unusual moment(s); %d agree with chat, %d added that chat missed (at most %d)",
                     sum(len(v) for v in peaks.values()), agree, sum(1 for m in found if not m["trigger"].startswith("chat")), extra)
    if ai:
        if live:
            log.warning("this stream is still live, so AI does not read it here; run it again when the stream has ended")
        else:
            from .streamread import add_ai_moments, ai_moments

            picks = ai_moments(source.url, work, meta, cfg, settings.top)
            before = len(found)
            found = add_ai_moments(found, picks)
            log.info("AI picked %d moment(s) from the speech: %d agree with ones already found, %d are new",
                     len(picks), len(picks) - (len(found) - before), len(found) - before)
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
                "peak_s": m["peak_s"],
                "start_s": m["start_s"],
                "end_s": m["end_s"],
                "vod_url": source.url,
                "platform": source.platform,
                "channel": meta.get("channel", ""),
                # Campaign briefs name the channel, and a Twitch VOD URL does not contain it.
                "source_url": meta.get("channel_url") or source.url,
                "stream_title": meta.get("title", ""),
            }
        )
        state.setdefault("status", "candidate")
        write_json(d / "clip.json", state)
        out.append(state)
    return work, out


def stage_fetch(url: str, cfg: Config, only: list[str] | None = None) -> list[Path]:
    """Download each selected clip's range (plus padding) as source.mp4."""
    source, work = open_work(url, cfg)
    fetched: list[Path] = []
    for d in select_clips(work, only):
        state = read_json(d / "clip.json")
        if state.get("status") == "rejected":
            continue
        out = d / "source.mp4"
        if not done(out):
            if free_gb(cfg.data_dir) < MIN_FREE_GB:
                raise PipelineError(
                    f"less than {MIN_FREE_GB:.0f} GB free on {cfg.data_dir.anchor}; delete old work folders before fetching more"
                )
            fetch_start = max(0.0, state["start_s"] - cfg.fetch.pad_s)
            fetch_end = state["end_s"] + cfg.fetch.pad_s
            t0 = time.monotonic()
            fetch_range(source, fetch_start, fetch_end, out, cfg)
            state["fetch_start_s"] = fetch_start
            state["fetch_end_s"] = fetch_end
            state["fetch_seconds"] = round(time.monotonic() - t0, 1)
            log.info("clip %s: fetched %.0fs of video in %.0fs (%.1f MB)", d.name, fetch_end - fetch_start, state["fetch_seconds"], out.stat().st_size / 1e6)
        info = probe(out, cfg)
        state.update({"source_width": info["width"], "source_height": info["height"], "source_duration_s": round(info["duration_s"], 2), "source_fps": round(info["fps"], 2)})
        from .layout import auto_layout

        state = auto_layout(d, state, cfg)
        write_json(d / "clip.json", state)
        fetched.append(out)
    return fetched


def stage_transcribe(url: str, cfg: Config, only: list[str] | None = None, force: bool = False) -> int:
    """Write words.json (word timestamps relative to source.mp4) for each fetched clip."""
    from .transcribe import transcribe_file

    _source, work = open_work(url, cfg)
    count = 0
    for d in select_clips(work, only):
        state = read_json(d / "clip.json")
        if state.get("status") == "rejected" or not done(d / "source.mp4"):
            continue
        out = d / "words.json"
        if out.exists() and not force:
            continue
        t0 = time.monotonic()
        words = transcribe_file(d / "source.mp4", cfg)
        write_json(out, words)
        state["transcribe_seconds"] = round(time.monotonic() - t0, 1)
        state["transcript"] = " ".join(w["word"] for w in words)
        from .cuts import apply_snap

        state = apply_snap(state, words, max_shift=cfg.fetch.pad_s)
        (d / "final.mp4").unlink(missing_ok=True)  # the cut may have moved; an older render is stale
        write_json(d / "clip.json", state)
        log.info("clip %s: %d words in %.0fs", d.name, len(words), state["transcribe_seconds"])
        count += 1
    return count


def stage_render(url: str, cfg: Config, only: list[str] | None = None, force: bool = False, approved_only: bool = False) -> list[Path]:
    """Render final.mp4 for each selected clip that has a source."""
    from .render import render_clip

    _source, work = open_work(url, cfg)
    rendered: list[Path] = []
    for d in select_clips(work, only, status="approved" if approved_only else None):
        state = read_json(d / "clip.json")
        if state.get("status") == "rejected" or not done(d / "source.mp4"):
            continue
        rendered.append(render_clip(d, cfg, force=force))
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


def stage_finish(cfg: Config, url: str | None = None, brief_path: str | None = None,
                 problems: list[str] | None = None) -> list[tuple[Path, bool]]:
    """Render every approved clip that lacks an up-to-date render, then export it with a post sheet.

    Works across every stream, or one if ``url`` is given. Clips already exported
    and unchanged since are skipped, so this is safe to run after every review.
    A clip that cannot be made is described in ``problems`` and the rest carry on.
    """
    from .brief import load_brief

    works = [open_work(url, cfg)[1]] if url else all_works(cfg)
    brief = load_brief(brief_path) if brief_path else None
    results: list[tuple[Path, bool]] = []
    for work in works:
        for d in select_clips(work, status="approved"):
            from .watch import note_error

            try:
                _finish_clip(work, d, cfg, brief, results)
                note_error(d, None)
            except Exception as e:  # one bad clip must not stop the others
                log.exception("clip %s/%s could not be finished", work.name, d.name)
                note_error(d, f"finish failed: {e}")
                if problems is not None:
                    problems.append(f"{work.name}/{d.name}: {e}")
    return results


def _finish_clip(work: Path, d: Path, cfg: Config, brief, results: list[tuple[Path, bool]]) -> None:
    from .compose import explained
    from .post import export_clip
    from .render import render_clip

    state = read_json(d / "clip.json")
    if not done(d / "source.mp4"):
        raise ToolError("approved, but its video was never downloaded (the draft failed). Reject it on the review page")
    if "source_width" not in state or not done(d / "words.json"):
        # The draft stopped part-way while it was being made; do the missing steps now.
        from .watch import prepare_clip

        log.info("clip %s/%s: the draft was never completed; completing it now", work.name, d.name)
        state = prepare_clip(d, state, cfg)
    state = write_clip_copy(d, state, cfg)  # no-op unless its text was written without AI and AI is reachable now
    ab = cfg.render.ab_versions
    # A render made before the opening effects were switched on (or off) no longer matches the settings.
    fresh = not done(d / "final.mp4") or bool(state.get("opening_used")) != bool(cfg.render.opening)
    if fresh:
        render_clip(d, cfg, force=True)
        for name in ("explained.mp4", "explained_b.mp4", "final_b.mp4"):  # made with the old settings too
            (d / name).unlink(missing_ok=True)
    exported = Path(state["exported"]) if state.get("exported") else None
    again = bool(brief) or exported is None or not exported.exists() or (ab and "_A." not in exported.name)
    ready = cfg.data_dir / "ready"
    if fresh or again:
        results.append(export_clip(d, ready, brief, suffix="_A" if ab else ""))
    if ab:
        fresh_b = not done(d / "final_b.mp4")
        if fresh_b:
            render_clip(d, cfg, out_name="final_b.mp4", variant="b")
        if fresh_b or again:
            results.append(export_clip(d, ready, brief, video_name="final_b.mp4", suffix="_B", main=False))
    if state.get("explainer"):
        if not (state.get("intro") or "").strip():
            raise ToolError("explainer ticked but there is no narration line; type one on the review page")
        for variant, name, suffix in (("a", "explained.mp4", "_explained_A" if ab else "_explained"), ("b", "explained_b.mp4", "_explained_B")):
            if variant == "b" and not ab:
                continue
            if not done(d / name) or again:
                explained(d, cfg, variant=variant)
                results.append(export_clip(d, ready, brief, video_name=name, suffix=suffix, main=False))


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
    redo_plain = state.get("copy_source") == "plain" and llm.available(cfg)
    if state.get("copy_at") and not force and not redo_plain:
        return state
    tags: list[str] = []
    if cfg.trends.enabled:
        try:
            ranked = trending([state.get("channel", "")] + list(cfg.trends.seeds), cfg, top=12)
            tags = [r["tag"] for r in ranked]
            state["trend_tags"] = tags
        except Exception as e:  # research is a nice-to-have; a clip must not fail because of it
            log.warning("clip %s: hashtag research failed (%s)", d.name, e)
    try:
        copy = llm.write_copy(state, tags, cfg)
        state["copy_source"] = cfg.llm.model
        state.pop("copy_problem", None)
    except llm.LlmUnavailable as e:
        log.warning("clip %s: text written WITHOUT AI: %s", d.name, e)
        copy = llm.fallback_copy(state, tags, cfg.trends.max_tags)
        state["copy_source"] = "plain"
        state["copy_problem"] = str(e)  # shown on the review page
    auto = set(state.get("copy_auto") or [])
    before = {k: state.get(k) for k in SHOWS}
    for key in COPY_FIELDS:
        if not state.get(key) or key in auto:
            state[key] = copy[key]
            auto.add(key)
    state["copy_auto"] = sorted(auto)
    state["ai_keep"], state["ai_reason"] = copy["keep"], copy["reason"]
    state["copy_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    for key, files in SHOWS.items():
        if state.get(key) != before[key]:  # the text is burned in, so the old render is stale
            for name in files:
                (d / name).unlink(missing_ok=True)
    write_json(d / "clip.json", state)
    return state


def stage_write(cfg: Config, url: str | None = None, only: list[str] | None = None, force: bool = False) -> int:
    """Write text for every clip that has a transcript and is not rejected."""
    works = [open_work(url, cfg)[1]] if url else all_works(cfg)
    count = 0
    for work in works:
        for d in select_clips(work, only if url else None):
            state = read_json(d / "clip.json")
            if state.get("status") == "rejected" or "transcript" not in state:
                continue
            if state.get("copy_at") and not force:
                continue
            write_clip_copy(d, state, cfg, force=force)
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
        if state.get("status") == "rejected":
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

    works = [open_work(url, cfg)[1]] if url else all_works(cfg)
    pool = []
    for work in works:
        for d in clip_dirs(work):
            state = read_json(d / "clip.json")
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
            if read_json(d / "clip.json", {}).get("status") != "rejected":
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
    stage_fetch(url, cfg)
    stage_transcribe(url, cfg)
    stage_render(url, cfg)
    return work
