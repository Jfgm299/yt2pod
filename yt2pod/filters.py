"""Decide whether a video belongs in a feed."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .config import Filters


@dataclass
class Video:
    id: str
    title: str
    url: str
    duration: int | None = None
    description: str = ""
    published: datetime | None = None
    thumbnail: str | None = None
    is_short: bool = False
    is_live: bool = False
    channel: str | None = None

    @property
    def published_date(self) -> date | None:
        return self.published.date() if self.published else None


def video_from_info(info: dict) -> Video:
    """Build a Video from a yt-dlp info dict (flat or full)."""
    vid = info.get("id") or ""
    url = info.get("webpage_url") or info.get("url") or f"https://www.youtube.com/watch?v={vid}"
    published = None
    ts = info.get("timestamp") or info.get("release_timestamp")
    if ts:
        published = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    elif info.get("upload_date"):
        published = datetime.strptime(info["upload_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
    thumb = info.get("thumbnail")
    if not thumb and info.get("thumbnails"):
        thumb = info["thumbnails"][-1].get("url")
    live_status = info.get("live_status")
    return Video(
        id=vid,
        title=info.get("title") or vid,
        url=url,
        duration=int(info["duration"]) if info.get("duration") else None,
        description=info.get("description") or "",
        published=published,
        thumbnail=thumb,
        is_short="/shorts/" in str(info.get("url") or "") or "/shorts/" in str(info.get("webpage_url") or ""),
        is_live=live_status in ("is_live", "is_upcoming", "post_live") or bool(info.get("is_live")),
        channel=info.get("channel") or info.get("uploader"),
    )


def _matches_any(patterns: list[str], text: str) -> str | None:
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
            return p
    return None


def check(video: Video, f: Filters) -> str | None:
    """Return None if the video passes the filters, otherwise the reason it was rejected.

    Filters whose data is missing (e.g. no publish date in a flat listing) are not applied;
    call again with full metadata to apply them.
    """
    if f.exclude_shorts and video.is_short:
        return "short"
    if f.exclude_live and video.is_live:
        return "live/upcoming"
    if video.duration is not None:
        if f.min_duration is not None and video.duration < f.min_duration:
            return f"too short ({fmt_duration(video.duration)} < {fmt_duration(f.min_duration)})"
        if f.max_duration is not None and video.duration > f.max_duration:
            return f"too long ({fmt_duration(video.duration)} > {fmt_duration(f.max_duration)})"
        if f.exclude_shorts and video.duration <= 60 and f.min_duration is None:
            # Shorts listed outside the /shorts tab still tend to be <= 60s.
            return "short (<= 60s)"
    if f.title_include and not _matches_any(f.title_include, video.title):
        return "title does not match title_include"
    hit = _matches_any(f.title_exclude, video.title)
    if hit:
        return f"title matches exclude pattern {hit!r}"
    if video.description:
        hit = _matches_any(f.description_exclude, video.description)
        if hit:
            return f"description matches exclude pattern {hit!r}"
    if f.published_after and video.published_date and video.published_date < f.published_after:
        return f"published before {f.published_after.isoformat()}"
    return None


def needs_full_info(video: Video, f: Filters) -> bool:
    """True if some filter could not be evaluated with the data we have."""
    if video.duration is None and (f.min_duration or f.max_duration or f.exclude_shorts):
        return True
    if f.published_after and video.published is None:
        return True
    if f.description_exclude and not video.description:
        return True
    return False


def fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "?"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
