"""Download what the pipeline needs from a VOD: metadata, chat, and only the clip ranges.

Never downloads a whole stream. On an unstable connection, a 40-second range
is a few megabytes and retries cheaply; a 6-hour VOD is neither.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

from . import progress
from .config import Config
from .sources import Source
from .tools import ToolError, find_tool, run
from .work import done, write_json

log = logging.getLogger(__name__)


class NoChatError(RuntimeError):
    pass


def _ytdlp(cfg: Config) -> list[str]:
    cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-progress"]
    try:
        cmd += ["--ffmpeg-location", str(Path(find_tool("ffmpeg", cfg.data_dir)).parent)]
    except ToolError:
        pass  # metadata and chat downloads work without FFmpeg; range downloads will report it
    node = shutil.which("node")
    if node:  # YouTube needs a JavaScript runtime; Node is already installed
        cmd += ["--js-runtimes", f"node:{node}"]
    return cmd


def fetch_meta(source: Source, work: Path, cfg: Config) -> dict:
    out = work / "meta.json"
    if done(out):
        return json.loads(out.read_text(encoding="utf-8"))
    if source.platform == "local":
        info = probe(Path(source.url), cfg)
        meta = {
            "url": source.url, "platform": "local", "video_id": source.video_id, "title": Path(source.url).stem,
            "channel": "", "channel_id": "", "channel_url": "", "duration_s": info["duration_s"], "is_live": False,
            "started_at": None, "width": info["width"], "height": info["height"],
        }
        write_json(out, meta)
        return meta
    if source.platform == "kick":
        from . import kick

        info = kick.details(source.video_id)
        meta = {
            "url": source.url, "platform": "kick", "video_id": source.video_id, "title": info["title"],
            "channel": info["channel_name"], "channel_id": info["channel"], "channel_url": f"https://kick.com/{info['channel']}" if info["channel"] else "",
            "duration_s": info["duration_s"] or None, "is_live": info["is_live"], "started_at": info["started_at"], "width": None, "height": None,
            "playlist": info["playlist"], "chat_id": info["chat_id"],
        }
        write_json(out, meta)
        return meta
    proc = run(
        _ytdlp(cfg) + ["-J", "--skip-download", source.url],
        timeout_s=180,
        retries=cfg.fetch.retries,
        retry_wait_s=cfg.fetch.retry_wait_s,
        what="metadata download",
    )
    info = json.loads(proc.stdout)
    meta = {
        "url": source.url,
        "platform": source.platform,
        "video_id": source.video_id,
        "title": info.get("title") or "",
        "channel": info.get("uploader") or info.get("channel") or "",
        "channel_id": info.get("uploader_id") or info.get("channel_id") or "",
        "channel_url": info.get("uploader_url") or info.get("channel_url") or "",
        "duration_s": info.get("duration"),
        "is_live": bool(info.get("is_live")),
        "started_at": info.get("timestamp"),  # epoch seconds when the broadcast began
        "width": info.get("width"),
        "height": info.get("height"),
    }
    if source.platform == "twitch" and meta["channel_id"] and not meta["channel_url"]:
        meta["channel_url"] = f"https://www.twitch.tv/{meta['channel_id']}"
    write_json(out, meta)
    return meta


def fetch_chat(source: Source, work: Path, cfg: Config, refresh: bool = False) -> Path:
    """Download the full chat log to chat.json. ``refresh`` re-downloads (use while a stream is still live)."""
    out = work / "chat.json"
    if source.platform == "local":
        raise NoChatError("a video file on this computer has no chat")
    if done(out) and not refresh:
        return out
    if source.platform == "kick":
        return _kick_chat(source, work, out)
    tmp_dir = cfg.data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    part = work / "chat.part.json"
    if source.platform == "twitch":
        run(
            [
                find_tool("TwitchDownloaderCLI", cfg.data_dir), "chatdownload",
                "-u", source.video_id, "-o", str(part),
                "--collision", "Overwrite", "--banner", "false", "--temp-path", str(tmp_dir),
            ],
            timeout_s=cfg.fetch.timeout_s,
            retries=cfg.fetch.retries,
            retry_wait_s=cfg.fetch.retry_wait_s,
            what="Twitch chat download",
            on_line=progress.from_tool(label="{pct}% of the chat"),
        )
    else:
        stem = work / "chat_dl"
        for old in work.glob("chat_dl*"):
            old.unlink()
        run(
            _ytdlp(cfg) + ["--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o", str(stem), source.url],
            timeout_s=cfg.fetch.timeout_s,
            retries=cfg.fetch.retries,
            retry_wait_s=cfg.fetch.retry_wait_s,
            what="YouTube chat download",
        )
        found = sorted(work.glob("chat_dl*live_chat*.json"))
        if not found:
            raise NoChatError(
                "this YouTube video has no chat replay (disabled, still processing, or not a past livestream). "
                "Moments cannot be found from chat for it."
            )
        os.replace(found[0], part)
    if not done(part):
        raise NoChatError("chat download produced an empty file")
    os.replace(part, out)
    return out


def _kick_chat(source: Source, work: Path, out: Path) -> Path:
    """A Kick stream's chat replay, with times counted from the start of its video."""
    from . import kick

    meta = json.loads((work / "meta.json").read_text(encoding="utf-8")) if done(work / "meta.json") else {}
    if not meta.get("chat_id"):  # details saved by an older version of the program did not hold it: ask again
        info = kick.details(source.video_id)
        meta.update(chat_id=info["chat_id"], started_at=info["started_at"], playlist=info["playlist"])
        if meta.get("chat_id"):
            write_json(work / "meta.json", meta)
    if not meta.get("chat_id"):
        raise NoChatError("Kick did not say which channel this video belongs to, so its chat cannot be read")
    # The video's own list says when its first second was broadcast and how long it is (also while still live).
    start, length = kick.playlist_times(meta["playlist"])
    start = start or meta.get("started_at")
    length = length or float(meta.get("duration_s") or 0)
    if not start or not length:
        raise NoChatError("Kick did not say when this video was broadcast, so its chat cannot be lined up with it")
    part = work / "chat.part.json"
    count = kick.save_chat(int(meta["chat_id"]), float(start), length, part, on_progress=lambda f: progress.sub(f, f"{round(100 * f)}% of the chat"))
    if not count:
        part.unlink(missing_ok=True)
        raise NoChatError("Kick has no chat for this video (nobody wrote, or the video is too old for Kick to still keep its chat)")
    os.replace(part, out)
    log.info("Kick chat: %d messages", count)
    return out


def fetch_range(source: Source, start_s: float, end_s: float, out: Path, cfg: Config) -> Path:
    """Download just [start_s, end_s] of the VOD to ``out`` (mp4). Skips if already there."""
    if done(out):
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.stem + ".part.mp4")
    if part.exists():
        part.unlink()
    start_s = max(0.0, start_s)
    ffmpeg = find_tool("ffmpeg", cfg.data_dir)
    if source.platform == "local":
        # Re-encoded rather than copied: screen recordings have few key frames, and a copy could start seconds off.
        run([ffmpeg, "-hide_banner", "-v", "error", "-stats", "-y", "-ss", f"{start_s:.3f}", "-i", source.url, "-t", f"{end_s - start_s:.3f}",
             "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(part)],
            timeout_s=max(cfg.fetch.timeout_s, 600 + 4 * (end_s - start_s)), what="cutting the range out of the video file",
            on_line=progress.from_tool(end_s - start_s))
        if not done(part):
            raise ToolError("cutting the range out of the video file produced nothing")
        os.replace(part, out)
        return out
    if source.platform == "kick":
        from . import kick

        kick.fetch_range(kick.details(source.video_id)["playlist"], start_s, end_s, part, cfg, on_line=progress.from_tool(end_s - start_s))
        if not done(part):
            raise ToolError("the Kick download produced no file")
        os.replace(part, out)
        return out
    if source.platform == "twitch":
        cmd = [
            find_tool("TwitchDownloaderCLI", cfg.data_dir), "videodownload",
            "-u", source.video_id, "-o", str(part),
            "-b", f"{int(start_s * 1000)}ms", "-e", f"{int(end_s * 1000)}ms",
            "--ffmpeg-path", ffmpeg, "--collision", "Overwrite", "--banner", "false",
            "--temp-path", str(cfg.data_dir / "tmp"),
        ]
        if cfg.fetch.max_height < 1080:
            cmd += ["-q", f"{cfg.fetch.max_height}p"]
        what = "Twitch range download"
    else:
        h = cfg.fetch.max_height
        cmd = _ytdlp(cfg) + [
            "-f", f"bv*[height<={h}]+ba/b[height<={h}]/b",
            "--download-sections", f"*{start_s:.2f}-{end_s:.2f}",
            "--force-keyframes-at-cuts",  # exact cut points, so captions line up with the audio
            "--merge-output-format", "mp4",
            "-o", str(part), source.url,
        ]
        what = "YouTube range download"
    (cfg.data_dir / "tmp").mkdir(parents=True, exist_ok=True)
    # The usual limit fits a clip. A long part for the cutting board gets four times its own length on top.
    limit = max(cfg.fetch.timeout_s, 600 + 4 * (end_s - start_s))
    run(cmd, timeout_s=limit, retries=cfg.fetch.retries, retry_wait_s=cfg.fetch.retry_wait_s, what=what,
        on_line=progress.from_tool(end_s - start_s))
    if not done(part):
        raise ToolError(f"{what} produced no file")
    os.replace(part, out)
    return out


def ensure_sound(path: Path, cfg: Config) -> bool:
    """Give a video that has no sound track a silent one (True when it had none). A screen recording without sound is
    a perfectly good video, but every later step (loudness, captions, joining) expects a sound track to be there."""
    proc = run([find_tool("ffprobe", cfg.data_dir), "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
               timeout_s=60, what="ffprobe")
    if proc.stdout.strip():
        return False
    part = path.with_name(path.stem + ".sound.part.mp4")
    try:
        run([find_tool("ffmpeg", cfg.data_dir), "-y", "-hide_banner", "-v", "error", "-i", str(path), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(part)],
            timeout_s=600, what="adding a silent sound track")
        from .work import _replace

        try:
            _replace(part, path)  # tried again for two seconds: the file may be open for an instant
        except PermissionError:
            raise ToolError(f"the video of clip {path.parent.name} has no sound and needs a silent sound track added, but the file is in use "
                            "right now (it is probably playing on the review page). Pause it and try again") from None
    finally:
        part.unlink(missing_ok=True)
    log.warning("%s has no sound; a silent sound track was added so that it can be made into a clip (it will have no captions)", path.parent.name)
    return True


def probe(path: Path, cfg: Config) -> dict:
    """Width, height, duration and frame rate of a video file."""
    proc = run(
        [
            find_tool("ffprobe", cfg.data_dir), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path),
        ],
        timeout_s=60,
        what="ffprobe",
    )
    data = json.loads(proc.stdout)
    stream = (data.get("streams") or [{}])[0]
    num, _, den = str(stream.get("r_frame_rate") or "30/1").partition("/")
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration_s": float((data.get("format") or {}).get("duration") or 0.0),
        "fps": float(num) / float(den or 1) if float(den or 1) else 30.0,
    }
