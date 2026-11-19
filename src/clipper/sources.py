"""Turn a stream/VOD URL into a Source the rest of the pipeline can work with."""

from __future__ import annotations

import re
from dataclasses import dataclass


class SourceError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    platform: str  # "twitch" or "youtube"
    video_id: str
    url: str

    @property
    def key(self) -> str:
        """Folder-safe unique name for this source's work folder."""
        return f"{self.platform}_{self.video_id}"


_TWITCH_VOD = re.compile(r"twitch\.tv/(?:[^/]+/)?v(?:ideos)?/(\d+)", re.IGNORECASE)
_YOUTUBE = re.compile(r"(?:youtu\.be/|[?&]v=|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")


def local_source(path) -> Source:
    """A video file on this computer (a screen recording, a downloaded video) as a source."""
    import hashlib
    from pathlib import Path

    p = Path(path).resolve()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", p.stem).strip("-")[:40] or "video"
    return Source("local", f"{slug}-{hashlib.sha1(str(p).lower().encode()).hexdigest()[:8]}", str(p))


def parse_source(url: str) -> Source:
    u = url.strip().strip('"')
    if not re.match(r"https?://", u, re.IGNORECASE):
        from pathlib import Path

        try:
            if Path(u).is_file():
                return local_source(u)
        except OSError:
            pass
    m = _TWITCH_VOD.search(u)
    if m:
        return Source("twitch", m.group(1), f"https://www.twitch.tv/videos/{m.group(1)}")
    if re.fullmatch(r"\d{8,12}", u):  # a bare Twitch VOD id
        return Source("twitch", u, f"https://www.twitch.tv/videos/{u}")
    m = _YOUTUBE.search(u)
    if m:
        return Source("youtube", m.group(1), f"https://www.youtube.com/watch?v={m.group(1)}")
    raise SourceError(
        f"cannot tell what {url!r} is. Give a Twitch video link (twitch.tv/videos/123...), a YouTube video link, "
        "or the full path of a video file on this computer. A channel link is not enough: open the video and copy its link."
    )
