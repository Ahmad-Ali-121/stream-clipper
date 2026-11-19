"""Command line entry point. Run ``clipper --help``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .brief import ClipInfo, check_clip, load_brief, passes
from .captions import build_ass, words_from_json
from .chat import ChatFormatError, load_chat
from .tools import ToolError
from .config import ConfigError, load_config
from .moments import find_moments, format_time

log = logging.getLogger("clipper")


def _cmd_moments(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    overrides = {k: v for k, v in (("top", args.top), ("mode", args.mode), ("gap_s", args.gap)) if v is not None}
    settings = replace(cfg.moments, **overrides)
    settings.validate()
    messages = load_chat(args.chat)
    if not messages:
        log.error("no chat messages found in %s; use the audio/transcript path for chatless streams", args.chat)
        return 2
    moments = find_moments(messages, settings, duration_s=args.duration)
    log.info("%d messages over %s, %d moments (mode=%s)", len(messages), format_time(messages[-1].t), len(moments), settings.mode)
    for m in moments:
        print(
            f"#{m.rank}  {format_time(m.start_s)} - {format_time(m.end_s)}  "
            f"{m.score:.1f}x normal  ({m.activity} {settings.count_mode}, {m.messages} msgs, {m.hype} hype)"
        )
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps([m.to_dict() for m in moments], indent=2), encoding="utf-8")
        log.info("wrote %s", out)
    return 0


def _cmd_captions(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    data = json.loads(Path(args.words).read_text(encoding="utf-8"))
    if isinstance(data, dict):  # allow {"words": [...]} as well as a bare list
        data = data.get("words", [])
    words = words_from_json(data, offset_s=args.offset)
    if not words:
        log.error("no words found in %s", args.words)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_ass(words, cfg.captions), encoding="utf-8")
    log.info("wrote %s (%d words)", out, len(words))
    return 0


def _cmd_check_brief(args: argparse.Namespace) -> int:
    brief = load_brief(args.brief)
    clip = ClipInfo(**json.loads(Path(args.clip).read_text(encoding="utf-8")))
    issues = check_clip(clip, brief)
    for issue in issues:
        print(f"[{'FAIL' if issue.level == 'fail' else 'CHECK BY HAND'}] {issue.message}")
    ok = passes(issues)
    print(f"{'PASS' if ok else 'REJECTED'}: {brief.name}")
    return 0 if ok else 1


def _ids(value: str | None) -> list[str] | None:
    return [x.strip() for x in value.split(",") if x.strip()] if value else None


def _print_clips(clips: list[dict]) -> None:
    for c in clips:
        print(
            f"#{c['rank']:<2} {c['id']:<10} {format_time(c['start_s'])} - {format_time(c['end_s'])}  "
            f"{c['score']:.1f}x normal  ({c['activity']} chatters, {c['messages']} msgs)  [{c.get('status', '')}]"
        )


def _cmd_find(args: argparse.Namespace) -> int:
    from .pipeline import stage_find

    work, clips = stage_find(args.url, load_config(args.config), top=args.top, mode=args.mode, refresh_chat=args.refresh, signals=args.signals, ai=args.ai)
    _print_clips(clips)
    print(f"work folder: {work}")
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    from .pipeline import stage_fetch

    files = stage_fetch(args.url, load_config(args.config), only=_ids(args.clips))
    print(f"{len(files)} clip source(s) ready")
    return 0


def _cmd_transcribe(args: argparse.Namespace) -> int:
    from .pipeline import stage_transcribe

    n = stage_transcribe(args.url, load_config(args.config), only=_ids(args.clips), force=args.force)
    print(f"{n} clip(s) transcribed")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    from .pipeline import stage_render

    files = stage_render(args.url, load_config(args.config), only=_ids(args.clips), force=args.force, approved_only=args.approved)
    for f in files:
        print(f)
    print(f"{len(files)} clip(s) rendered")
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    from .pipeline import open_work
    from .review import serve
    from .work import read_json

    cfg = load_config(args.config)
    if args.url:
        _source, work = open_work(args.url, cfg)
        meta = read_json(work / "meta.json", {})
        return serve([work], f"{meta.get('channel', '')}: {meta.get('title', work.name)}"[:120], port=args.port, cfg=cfg, open_browser=args.open)
    from .pipeline import all_works

    return serve(all_works(cfg), "All streams", port=args.port, cfg=cfg, every_stream=True, open_browser=args.open)


def _cmd_clean(args: argparse.Namespace) -> int:
    from .pipeline import stage_clean

    count, mb = stage_clean(load_config(args.config), dry_run=args.dry_run)
    print(f"{'would remove' if args.dry_run else 'removed'} {count} file(s) of rejected clips, {mb:.0f} MB")
    return 0


def _cmd_write(args: argparse.Namespace) -> int:
    from .pipeline import stage_write

    n = stage_write(load_config(args.config), url=args.url, only=_ids(args.clips), force=args.force)
    print(f"text written for {n} clip(s)")
    return 0


def _cmd_tags(args: argparse.Namespace) -> int:
    from .trends import trending

    for r in trending(args.seeds, load_config(args.config), top=args.top):
        print(f"{r['tag']:<28} used by {r['videos']:>3} top Shorts   score {r['score']}")
    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    from .pipeline import stage_explain

    for f in stage_explain(args.url, load_config(args.config), only=_ids(args.clips), force=args.force):
        print(f)
    return 0


def _cmd_top(args: argparse.Namespace) -> int:
    from .pipeline import stage_top

    print(stage_top(load_config(args.config), url=args.url, count=args.count, opener=args.opener or "", include_unapproved=args.include_unapproved))
    return 0


def _cmd_hardware(args: argparse.Namespace) -> int:
    from . import hw

    cfg = load_config(args.config)
    info = hw.detect(cfg, refresh=args.refresh)
    if info["whisper_device"] == "cuda" and cfg.transcribe.device == "auto":
        # Seeing the card is not proof: load the model on it once, in a separate process, and keep the answer.
        hw.confirm_cuda(cfg, cfg.transcribe.model, info["whisper_compute_type"], str(cfg.data_dir / "models"))
        info = hw.detect(cfg)
        if info.get("cuda_problem"):
            print("NVIDIA card found, but speech-to-text cannot use it:\n  " + info["cuda_problem"].replace("\n", "\n  "))
    print(f"video encoder: {info['encoder']}")
    print(f"speech model runs on: {info['whisper_device']} ({info['whisper_compute_type']})" + (f", GPU memory {info['vram_mb']} MB" if info["vram_mb"] else ""))
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from . import hw

    return 0 if hw.doctor(load_config(args.config)) else 1


def _cmd_finish(args: argparse.Namespace) -> int:
    from .pipeline import stage_finish

    problems: list[str] = []
    results = stage_finish(load_config(args.config), url=args.url, brief_path=args.brief, problems=problems)
    for video, ok in results:
        print(f"{'READY ' if ok else 'BRIEF FAILS'}  {video}")
    for p in problems:
        print(f"NOT MADE  {p}")
    print(f"{len(results)} file(s) rendered and exported")
    return 0 if all(ok for _v, ok in results) and not problems else 1


def _cmd_post(args: argparse.Namespace) -> int:
    from .pipeline import stage_post

    results = stage_post(args.url, load_config(args.config), only=_ids(args.clips), brief_path=args.brief)
    for video, ok in results:
        print(f"{'READY ' if ok else 'BRIEF FAILS'}  {video}")
    print(f"{len(results)} approved clip(s) exported with post sheets")
    return 0 if all(ok for _v, ok in results) else 1


def _cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import stage_run

    work = stage_run(args.url, load_config(args.config), top=args.top, mode=args.mode, refresh_chat=args.refresh, signals=args.signals, ai=args.ai)
    print(f"drafts ready in {work}. Next: clipper review {args.url}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from .pipeline import open_work, select_clips
    from .work import read_json

    _source, work = open_work(args.url, load_config(args.config))
    for d in select_clips(work):
        c = read_json(d / "clip.json")
        have = [n for n, f in (("source", "source.mp4"), ("words", "words.json"), ("final", "final.mp4"), ("post", "post.txt")) if (d / f).exists()]
        print(f"#{c.get('rank'):<3} {c['id']:<10} {c.get('status', ''):<10} {c.get('score')}x  [{', '.join(have)}]  {c.get('hook', '')}")
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    from .watch import watch

    import sys

    from .signals import ask_mode, parse_mode

    cfg = load_config(args.config)
    if args.mode:
        cfg.watch.mode = parse_mode(args.mode)
    elif sys.stdin.isatty():
        cfg.watch.mode = ask_mode(parse_mode(cfg.watch.mode))
    else:
        cfg.watch.mode = parse_mode(cfg.watch.mode)
    watch(args.channels, cfg, max_minutes=args.minutes)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clipper", description="Stream clipping pipeline")
    p.add_argument("--version", action="version", version=f"clipper {__version__}")
    p.add_argument("--config", help="path to config.yaml (default: project config.yaml)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    f = sub.add_parser("find", help="download a VOD's chat and find its best moments")
    f.add_argument("url", help="Twitch VOD URL or YouTube video URL")
    f.add_argument("--top", type=int)
    f.add_argument("--mode", choices=["symmetric", "trailing"])
    f.add_argument("--signals", help="what finds moments: A or chat, B or audio (chat + sound), C or video (chat + picture), D or all")
    f.add_argument("--ai", action="store_true", help="also let AI read the whole stream's speech and pick moments (slow; needs a Claude key)")
    f.add_argument("--refresh", action="store_true", help="re-download chat (for a stream that is still live)")
    f.set_defaults(func=_cmd_find)

    fe = sub.add_parser("fetch", help="download only the clip ranges of the found moments")
    fe.add_argument("url")
    fe.add_argument("--clips", help="comma-separated clip ids (default: all not rejected)")
    fe.set_defaults(func=_cmd_fetch)

    tr = sub.add_parser("transcribe", help="word timestamps for each fetched clip")
    tr.add_argument("url")
    tr.add_argument("--clips")
    tr.add_argument("--force", action="store_true", help="redo clips that already have a transcript")
    tr.set_defaults(func=_cmd_transcribe)

    rn = sub.add_parser("render", help="render the final 9:16 clips")
    rn.add_argument("url")
    rn.add_argument("--clips")
    rn.add_argument("--force", action="store_true", help="re-render clips that already have a final.mp4")
    rn.add_argument("--approved", action="store_true", help="only clips approved at review")
    rn.set_defaults(func=_cmd_render)

    rv = sub.add_parser("review", help="open the local review page to approve, reject and edit clips")
    rv.add_argument("url", nargs="?", help="one stream's VOD URL; leave out to review clips from every stream")
    rv.add_argument("--port", type=int, default=8765)
    rv.add_argument("--open", action="store_true", help="open the page in the browser when it is ready")
    rv.set_defaults(func=_cmd_review)

    cl = sub.add_parser("clean", help="free disk space: delete the video files of rejected clips")
    cl.add_argument("--dry-run", action="store_true", help="only report what would be removed")
    cl.set_defaults(func=_cmd_clean)

    wr = sub.add_parser("write", help="write hook, title, description, hashtags and narration for clips")
    wr.add_argument("url", nargs="?", help="one stream; leave out for every stream")
    wr.add_argument("--clips")
    wr.add_argument("--force", action="store_true", help="rewrite text that was written automatically before")
    wr.set_defaults(func=_cmd_write)

    tg = sub.add_parser("tags", help="hashtags that well-viewed YouTube Shorts use for a topic, right now")
    tg.add_argument("seeds", nargs="+", help="topics without the #, e.g. gta6 lacy")
    tg.add_argument("--top", type=int, default=15)
    tg.set_defaults(func=_cmd_tags)

    ex = sub.add_parser("explain", help="make the narrated 'explained' version of approved clips")
    ex.add_argument("url")
    ex.add_argument("--clips", help="clip ids (default: all approved)")
    ex.add_argument("--force", action="store_true")
    ex.set_defaults(func=_cmd_explain)

    tp = sub.add_parser("top", help="make a 'top N moments' countdown video from the best clips")
    tp.add_argument("url", nargs="?", help="one stream; leave out to pick from every stream")
    tp.add_argument("--count", type=int, default=5)
    tp.add_argument("--opener", help="a sentence the narrator says first")
    tp.add_argument("--include-unapproved", action="store_true", help="also use drafts that are not rejected")
    tp.set_defaults(func=_cmd_top)

    hwp = sub.add_parser("hardware", help="show which GPU or CPU paths are used; --refresh re-detects")
    hwp.add_argument("--refresh", action="store_true")
    hwp.set_defaults(func=_cmd_hardware)

    doc = sub.add_parser("doctor", help="describe this computer and try speech-to-text several ways, to find why it fails")
    doc.set_defaults(func=_cmd_doctor)

    fi = sub.add_parser("finish", help="render and export every approved clip, across all streams")
    fi.add_argument("url", nargs="?", help="limit to one stream")
    fi.add_argument("--brief", help="campaign brief YAML to check each clip against")
    fi.set_defaults(func=_cmd_finish)

    po = sub.add_parser("post", help="export approved clips with post sheets for manual posting")
    po.add_argument("url")
    po.add_argument("--clips")
    po.add_argument("--brief", help="campaign brief YAML to check each clip against")
    po.set_defaults(func=_cmd_post)

    ru = sub.add_parser("run", help="find, fetch, transcribe and render drafts in one go")
    ru.add_argument("url")
    ru.add_argument("--top", type=int)
    ru.add_argument("--mode", choices=["symmetric", "trailing"])
    ru.add_argument("--signals", help="what finds moments: A or chat, B or audio (chat + sound), C or video (chat + picture), D or all")
    ru.add_argument("--ai", action="store_true", help="also let AI read the whole stream's speech and pick moments (slow; needs a Claude key)")
    ru.add_argument("--refresh", action="store_true", help="re-download chat (stream still live)")
    ru.set_defaults(func=_cmd_run)

    st = sub.add_parser("status", help="list a stream's clips and what exists for each")
    st.add_argument("url")
    st.set_defaults(func=_cmd_status)

    wa = sub.add_parser("watch", help="watch live Twitch channels and make draft clips as moments happen")
    wa.add_argument("channels", nargs="+", help="channel names or channel URLs, e.g. lacy kaicenat")
    wa.add_argument("--minutes", type=float, help="stop after this many minutes (default: run until Ctrl+C)")
    wa.add_argument("--mode", help="A or chat, B or audio, C or video, D or all. Asked in the terminal when left out")
    wa.set_defaults(func=_cmd_watch)

    m = sub.add_parser("moments", help="find chat-spike moments in a chat log")
    m.add_argument("chat", help="TwitchDownloader JSON, yt-dlp live_chat JSON, or native JSON lines")
    m.add_argument("--top", type=int)
    m.add_argument("--mode", choices=["symmetric", "trailing"], help="symmetric = VOD, trailing = live")
    m.add_argument("--gap", type=float, help="minimum seconds between moments")
    m.add_argument("--duration", type=float, help="stream length in seconds, if known")
    m.add_argument("--out", help="write moments as JSON to this file")
    m.set_defaults(func=_cmd_moments)

    c = sub.add_parser("captions", help="build an ASS caption file from word timestamps")
    c.add_argument("words", help="JSON list of {start, end, word}")
    c.add_argument("--offset", type=float, default=0.0, help="clip start within the source, in seconds")
    c.add_argument("--out", required=True)
    c.set_defaults(func=_cmd_captions)

    b = sub.add_parser("check-brief", help="check a clip against a campaign brief")
    b.add_argument("brief", help="brief YAML file (see briefs/example.yaml)")
    b.add_argument("clip", help="clip JSON: source_url, duration_s, width, height, caption, has_disclosure_overlay")
    b.set_defaults(func=_cmd_check_brief)
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    import faulthandler

    try:
        faulthandler.enable()  # a crash inside a native library prints where it happened instead of exiting silently
    except (OSError, ValueError) as e:  # no real error stream to write to (output captured by another program)
        log.debug("crash traces are off: %s", e)
    try:
        return args.func(args)
    except (ConfigError, ChatFormatError, FileNotFoundError, ValueError, ToolError) as e:
        log.error("%s", e)  # expected kinds of failure: the message says what to do
        if args.verbose:
            log.exception("details")
        return 2
    except Exception:
        log.exception("unexpected error; this is a bug, please report the lines below")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
