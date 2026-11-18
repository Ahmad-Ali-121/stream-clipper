"""Hashtags that are working on YouTube Shorts right now, from YouTube itself.

For a seed tag (say "gta6") this reads YouTube's own page of top Shorts for
that hashtag, and counts which other hashtags those videos use, weighted by
their view counts. No API key: it uses the yt-dlp already installed.

This is real data about what popular Shorts are tagged with. It is not a
forecast, and it only covers YouTube; TikTok and Instagram publish no
equivalent that can be read without an account.

Results are cached per seed for ``trends.cache_hours`` so a busy session does
not hit YouTube again for every clip.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections import defaultdict

from .config import Config
from .tools import ToolError, run
from .work import read_json, write_json

log = logging.getLogger(__name__)

_HASHTAG = re.compile(r"#([0-9A-Za-z_]{2,40})")
GENERIC = {"shorts", "short", "fyp", "foryou", "foryoupage", "viral", "trending", "youtubeshorts", "ytshorts", "explore", "reels"}


def clean_seed(text: str) -> str:
    """'GTA 6' -> 'gta6', '#Lacy' -> 'lacy'."""
    return re.sub(r"[^0-9a-z_]", "", text.lower())


def rank_hashtags(videos: list[dict], seed: str = "") -> list[dict]:
    """Rank the hashtags used by a list of {"title", "views"} videos.

    Score = sum over videos of log10(views): a tag used by many well-viewed
    videos beats a tag used once by a single huge video.
    """
    score: dict[str, float] = defaultdict(float)
    count: dict[str, int] = defaultdict(int)
    for v in videos:
        tags = {t.lower() for t in _HASHTAG.findall(v.get("title") or "")}
        weight = math.log10(max(float(v.get("views") or 0), 10.0))
        for t in tags:
            score[t] += weight
            count[t] += 1
    out = [
        {"tag": f"#{t}", "videos": count[t], "score": round(score[t], 1)}
        for t in score
        if t not in GENERIC and t != seed
    ]
    return sorted(out, key=lambda x: (-x["score"], x["tag"]))


def fetch_hashtag_videos(seed: str, cfg: Config, limit: int = 40) -> list[dict]:
    from .fetch import _ytdlp

    proc = run(
        _ytdlp(cfg) + ["--flat-playlist", "--playlist-end", str(limit), "--print", "%(view_count)s\t%(title)s",
                       f"https://www.youtube.com/hashtag/{seed}/shorts"],
        timeout_s=120, retries=1, retry_wait_s=5, what=f"YouTube hashtag #{seed}",
    )
    videos = []
    for line in proc.stdout.splitlines():
        views, _, title = line.partition("\t")
        if title:
            videos.append({"title": title, "views": int(views) if views.isdigit() else 0})
    return videos


def trending(seeds: list[str], cfg: Config, top: int = 12) -> list[dict]:
    """Ranked related hashtags for several seeds, merged. Seeds that fail are skipped, never fatal."""
    cache_path = cfg.data_dir / "trends_cache.json"
    cache = read_json(cache_path, {}) or {}
    merged: dict[str, dict] = {}
    for raw in seeds:
        seed = clean_seed(raw)
        if not seed:
            continue
        entry = cache.get(seed)
        if not entry or time.time() - entry.get("at", 0) > cfg.trends.cache_hours * 3600:
            try:
                videos = fetch_hashtag_videos(seed, cfg)
            except ToolError as e:
                log.warning("hashtag research for #%s failed (%s); skipping it", seed, e)
                continue
            entry = {"at": time.time(), "videos": len(videos), "ranked": rank_hashtags(videos, seed)[:30]}
            cache[seed] = entry
            write_json(cache_path, cache)
        for r in entry["ranked"]:
            m = merged.setdefault(r["tag"], {"tag": r["tag"], "videos": 0, "score": 0.0, "seeds": []})
            m["videos"] += r["videos"]
            m["score"] = round(m["score"] + r["score"], 1)
            m["seeds"].append(seed)
    # A tag seen on a single video is noise, not a trend.
    solid = [m for m in merged.values() if m["videos"] >= 2]
    return sorted(solid, key=lambda x: (-x["score"], x["tag"]))[:top]
