"""Core logic: scan a source, apply filters, download audio, rebuild the feed."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import Config, FeedConfig
from .feed import write_feed
from .filters import Video, check, needs_full_info, video_from_info
from .store import Episode, Store
from .youtube import YouTube, YouTubeError

log = logging.getLogger(__name__)

DONE_STATUSES = {"downloaded", "ignored", "removed"}


@dataclass
class SyncResult:
    feed_id: str
    downloaded: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


@dataclass
class BrowseRow:
    video: Video
    status: str | None
    reason: str | None


def full_video(store: Store, yt: YouTube, entry: dict) -> Video:
    """Return the video with full metadata, using the local cache when possible."""
    vid = entry["id"]
    info = store.cached_info(vid)
    if info is None:
        info = yt.video_info(vid)
        store.cache_info(vid, info)
    merged = {**entry, **{k: v for k, v in info.items() if v is not None}}
    return video_from_info(merged)


def evaluate(store: Store, yt: YouTube, feed: FeedConfig, entry: dict, fetch: bool = True) -> tuple[Video, str | None]:
    video = video_from_info(entry)
    reason = check(video, feed.filters)
    if reason is None and fetch and needs_full_info(video, feed.filters):
        video = full_video(store, yt, entry)
        reason = check(video, feed.filters)
    return video, reason


def download(cfg: Config, feed: FeedConfig, store: Store, yt: YouTube, video: Video,
             progress=None) -> Episode:
    log.info("[%s] downloading %s - %s", feed.id, video.id, video.title)
    kwargs = {"progress": progress} if progress else {}
    path = yt.download_audio(video.id, cfg.audio_dir / feed.id, feed.audio_format, feed.sponsorblock_remove,
                             **kwargs)
    ep = Episode(
        feed_id=feed.id,
        video_id=video.id,
        status="downloaded",
        title=video.title,
        description=video.description,
        url=video.url,
        thumbnail=video.thumbnail,
        duration=video.duration,
        published=video.published.isoformat() if video.published else None,
        filename=path.name,
        filesize=path.stat().st_size,
    )
    store.upsert(ep)
    return ep


def apply_retention(cfg: Config, feed: FeedConfig, store: Store) -> list[str]:
    if not feed.max_episodes:
        return []
    removed = []
    for ep in store.episodes(feed.id)[feed.max_episodes:]:
        if ep.filename:
            (cfg.audio_dir / feed.id / ep.filename).unlink(missing_ok=True)
        ep.status, ep.filename, ep.filesize = "removed", None, None
        store.upsert(ep)
        removed.append(ep.video_id)
    return removed


def rebuild_feed(cfg: Config, feed: FeedConfig, store: Store) -> None:
    write_feed(cfg, feed, store.episodes(feed.id), store.channel_info(feed.id))


def sync_feed(cfg: Config, feed: FeedConfig, store: Store, yt: YouTube, dry_run: bool = False) -> SyncResult:
    result = SyncResult(feed.id)
    entries, channel = yt.list_videos(feed.source, feed.scan_limit)
    first_sync = not store.is_initialized(feed.id)

    candidates: list[Video] = []
    for entry in entries:  # newest first
        existing = store.get(feed.id, entry["id"])
        if existing and existing.status in DONE_STATUSES:
            continue
        try:
            video, reason = evaluate(store, yt, feed, entry)
        except YouTubeError as e:
            log.warning("[%s] skipping %s: %s", feed.id, entry["id"], e)
            continue
        if reason:
            log.debug("[%s] filtered %s (%s): %s", feed.id, video.id, video.title, reason)
            continue
        candidates.append(video)

    if first_sync:
        # Don't download a channel's whole back catalogue on the first run.
        to_download, to_ignore = candidates[: feed.backfill], candidates[feed.backfill:]
    else:
        to_download, to_ignore = candidates, []

    if dry_run:
        result.downloaded = [v.id for v in to_download]
        result.ignored = [v.id for v in to_ignore]
        return result

    for video in to_ignore:
        store.upsert(Episode(feed_id=feed.id, video_id=video.id, status="ignored",
                             title=video.title, url=video.url, duration=video.duration))
        result.ignored.append(video.id)

    for video in reversed(to_download):  # oldest first so retention keeps the newest
        try:
            video = full_video(store, yt, {"id": video.id, **_video_as_entry(video)})
            download(cfg, feed, store, yt, video)
            result.downloaded.append(video.id)
        except YouTubeError as e:
            log.error("[%s] %s", feed.id, e)
            store.upsert(Episode(feed_id=feed.id, video_id=video.id, status="failed",
                                 title=video.title, url=video.url, error=str(e)[:500]))
            result.failed.append(video.id)

    result.removed = apply_retention(cfg, feed, store)
    store.mark_synced(feed.id, channel)
    rebuild_feed(cfg, feed, store)
    return result


def _video_as_entry(video: Video) -> dict:
    entry = {"title": video.title, "url": video.url, "duration": video.duration}
    return {k: v for k, v in entry.items() if v is not None}


def browse(cfg: Config, feed: FeedConfig, store: Store, yt: YouTube, limit: int, full: bool = False) -> list[BrowseRow]:
    entries, channel = yt.list_videos(feed.source, limit)
    rows = []
    for entry in entries:
        existing = store.get(feed.id, entry["id"])
        video, reason = evaluate(store, yt, feed, entry, fetch=full)
        rows.append(BrowseRow(video, existing.status if existing else None, reason))
    return rows


def add_video(cfg: Config, feed: FeedConfig, store: Store, yt: YouTube, video_id: str) -> Episode:
    """Download a specific video into a feed, ignoring filters (e.g. picked via `browse`)."""
    if "watch?v=" in video_id:
        video_id = video_id.split("watch?v=", 1)[1].split("&", 1)[0]
    elif "youtu.be/" in video_id:
        video_id = video_id.split("youtu.be/", 1)[1].split("?", 1)[0]
    video = full_video(store, yt, {"id": video_id})
    ep = download(cfg, feed, store, yt, video)
    if not store.channel_info(feed.id):
        try:
            _, channel = yt.list_videos(feed.source, 1)
            store.save_channel_info(feed.id, channel)
        except YouTubeError:
            pass
    rebuild_feed(cfg, feed, store)
    return ep
