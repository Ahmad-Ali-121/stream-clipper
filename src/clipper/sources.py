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


def parse_source(url: str) -> Source:
    u = url.strip()
    m = _TWITCH_VOD.search(u)
    if m:
        return Source("twitch", m.group(1), f"https://www.twitch.tv/videos/{m.group(1)}")
    if re.fullmatch(r"\d{8,12}", u):  # a bare Twitch VOD id
        return Source("twitch", u, f"https://www.twitch.tv/videos/{u}")
    m = _YOUTUBE.search(u)
    if m:
        return Source("youtube", m.group(1), f"https://www.youtube.com/watch?v={m.group(1)}")
    raise SourceError(
        f"cannot tell what {url!r} is. Give a Twitch VOD URL (twitch.tv/videos/123...) or a YouTube video URL. "
        "A channel URL is not enough: open the stream's VOD and copy that link."
    )
