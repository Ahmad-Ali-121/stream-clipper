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
            timeout_s=cfg.fetch.timeout_s, what="cutting the range out of the video file", on_line=progress.from_tool(end_s - start_s))
        if not done(part):
            raise ToolError("cutting the range out of the video file produced nothing")
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
    run(cmd, timeout_s=cfg.fetch.timeout_s, retries=cfg.fetch.retries, retry_wait_s=cfg.fetch.retry_wait_s, what=what,
        on_line=progress.from_tool(end_s - start_s))
    if not done(part):
        raise ToolError(f"{what} produced no file")
    os.replace(part, out)
    return out


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
