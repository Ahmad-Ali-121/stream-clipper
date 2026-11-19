"""A record of what was posted where, and what came of it: views, payout, rejections.

Nothing here posts anything. You post by hand, then log the link; the tracker keeps the numbers
together, warns before the same clip goes to the same campaign twice (campaigns reject duplicates),
and adds up views and money per campaign. Everything is in <data_dir>/tracker.json.
"""

from __future__ import annotations

import csv
import io
import logging
import time
import uuid
from pathlib import Path

from .work import read_json, write_json

log = logging.getLogger(__name__)

PLATFORMS = ("YouTube Shorts", "TikTok", "Instagram Reels", "Facebook Reels", "Other")
STATUSES = ("posted", "submitted", "accepted", "rejected", "paid")
FIELDS = ("clip", "channel", "title", "version", "campaign", "platform", "url", "posted_at", "status", "views", "payout", "reason", "notes")
EDITABLE = {"campaign", "platform", "url", "posted_at", "status", "views", "payout", "reason", "notes", "version", "title"}


def _path(data_dir: Path) -> Path:
    return data_dir / "tracker.json"


def load(data_dir: Path) -> list[dict]:
    return read_json(_path(data_dir), []) or []


def _clean(field: str, value):
    if field in ("views",):
        if value in ("", None):
            return None
        n = int(float(value))
        if n < 0:
            raise ValueError("views cannot be negative")
        return n
    if field == "payout":
        if value in ("", None):
            return None
        n = round(float(value), 2)
        if n < 0:
            raise ValueError("a payout cannot be negative")
        return n
    if field == "platform" and value not in PLATFORMS:
        raise ValueError(f"the platform must be one of: {', '.join(PLATFORMS)}")
    if field == "status" and value not in STATUSES:
        raise ValueError(f"the status must be one of: {', '.join(STATUSES)}")
    if field == "url":
        value = str(value or "").strip()
        if value and not value.lower().startswith(("http://", "https://")):
            raise ValueError("the link must start with http:// or https://")
        return value
    return str(value or "").strip()


def duplicate(rows: list[dict], clip: str, campaign: str, skip_id: str = "") -> dict | None:
    """An earlier post of the same clip to the same campaign, if any (A and B versions are the same clip)."""
    if not clip or not campaign:
        return None
    return next((r for r in rows if r["id"] != skip_id and r.get("clip") == clip and (r.get("campaign") or "").lower() == campaign.lower()), None)


def add(data_dir: Path, entry: dict, allow_duplicate: bool = False) -> dict:
    rows = load(data_dir)
    row = {"id": uuid.uuid4().hex[:10], "clip": str(entry.get("clip") or ""), "channel": str(entry.get("channel") or ""), "version": "A",
           "campaign": "", "platform": "YouTube Shorts", "url": "", "posted_at": time.strftime("%Y-%m-%d %H:%M"), "status": "posted",
           "views": None, "payout": None, "reason": "", "notes": "", "title": ""}
    for k in EDITABLE:
        if k in entry:
            row[k] = _clean(k, entry[k])
    before = duplicate(rows, row["clip"], row["campaign"])
    if before and not allow_duplicate:
        raise ValueError(f"this clip is already logged for the campaign '{row['campaign']}' ({before['platform']}, {before['posted_at']}). "
                         "Campaigns reject the same clip twice, and versions A and B count as the same clip.")
    row["updated_at"] = time.strftime("%Y-%m-%d %H:%M")
    rows.append(row)
    write_json(_path(data_dir), rows)
    return row


def update(data_dir: Path, row_id: str, changes: dict) -> dict:
    rows = load(data_dir)
    row = next((r for r in rows if r["id"] == row_id), None)
    if row is None:
        raise KeyError(f"no such tracker entry: {row_id}")
    unknown = set(changes) - EDITABLE
    if unknown:
        raise ValueError(f"these cannot be changed: {', '.join(sorted(unknown))}")
    for k, v in changes.items():
        row[k] = _clean(k, v)
    row["updated_at"] = time.strftime("%Y-%m-%d %H:%M")
    write_json(_path(data_dir), rows)
    return row


def delete(data_dir: Path, row_id: str) -> int:
    rows = load(data_dir)
    kept = [r for r in rows if r["id"] != row_id]
    write_json(_path(data_dir), kept)
    return len(rows) - len(kept)


def totals(rows: list[dict]) -> dict:
    """Views and money overall and per campaign, and how many posts were rejected."""
    def block(items: list[dict]) -> dict:
        return {"posts": len(items), "views": sum(int(r.get("views") or 0) for r in items),
                "payout": round(sum(float(r.get("payout") or 0) for r in items), 2),
                "rejected": sum(1 for r in items if r.get("status") == "rejected"),
                "paid": sum(1 for r in items if r.get("status") == "paid")}

    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r.get("campaign") or "(no campaign)", []).append(r)
    out = block(rows)
    out["campaigns"] = {name: block(items) for name, items in sorted(by.items())}
    return out


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(FIELDS)
    for r in rows:
        w.writerow(["" if r.get(f) is None else r.get(f) for f in FIELDS])
    return buf.getvalue()


def refresh_views(data_dir: Path, cfg) -> dict:
    """Read the public view count of every logged link that yt-dlp understands. Returns how many were updated and why others were not."""
    from .fetch import _ytdlp
    from .tools import ToolError, run

    rows = load(data_dir)
    updated, problems = 0, []
    for r in rows:
        if not r.get("url"):
            continue
        try:
            proc = run(_ytdlp(cfg) + ["--skip-download", "--print", "%(view_count)s", r["url"]], timeout_s=90, retries=0, what="reading a view count")
            value = (proc.stdout.strip().splitlines() or [""])[-1]
            if not value.isdigit():
                raise ToolError(f"the page gave no view count ({value or 'empty'})")
            r["views"], r["updated_at"] = int(value), time.strftime("%Y-%m-%d %H:%M")
            updated += 1
        except ToolError as e:
            problems.append(f"{r.get('platform')} {r['url']}: {str(e)[:160]}")
            log.warning("tracker: views not read for %s (%s)", r["url"], e)
    write_json(_path(data_dir), rows)
    return {"updated": updated, "problems": problems}
