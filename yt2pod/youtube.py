"""Thin wrapper around yt-dlp so the rest of the code (and the tests) don't touch it directly."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class YouTubeError(RuntimeError):
    pass


def _base_opts(cookies_file: str | None) -> dict[str, Any]:
    opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "noprogress": True}
    if cookies_file:
        opts["cookiefile"] = cookies_file
    return opts


def normalize_source(source: str) -> str:
    """Accept '@handle', a channel URL or a playlist URL/ID and return a URL yt-dlp understands.

    A bare channel (no tab) is pointed at its /videos tab so Shorts and live streams
    aren't mixed in.
    """
    s = source.strip()
    if s.startswith("@"):
        return f"https://www.youtube.com/{s}/videos"
    if s.startswith(("PL", "UU", "OL", "FL")) and "/" not in s:
        return f"https://www.youtube.com/playlist?list={s}"
    if s.startswith("UC") and "/" not in s:
        return f"https://www.youtube.com/channel/{s}/videos"
    if "youtube.com/" in s and "list=" not in s and "/watch" not in s:
        tail = s.rstrip("/").rsplit("/", 1)[-1]
        if tail not in ("videos", "streams", "shorts", "podcasts", "playlists", "featured") and (
            "/@" in s or "/channel/" in s or "/c/" in s or "/user/" in s
        ):
            return s.rstrip("/") + "/videos"
    return s


class YouTube:
    def __init__(self, cookies_file: str | None = None):
        self.cookies_file = cookies_file

    def list_videos(self, source: str, limit: int) -> tuple[list[dict], dict]:
        """Return (flat entries newest-first, playlist/channel info)."""
        import yt_dlp

        opts = _base_opts(self.cookies_file) | {
            "extract_flat": "in_playlist",
            "playlistend": limit,
            "lazy_playlist": True,
        }
        url = normalize_source(source)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:  # yt-dlp raises many types
            raise YouTubeError(f"Could not list {url}: {e}") from e
        if not info:
            raise YouTubeError(f"Nothing found at {url}")
        entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
        meta = {
            "title": info.get("channel") or info.get("uploader") or info.get("title"),
            "playlist_title": info.get("title"),
            "description": info.get("description"),
            "url": info.get("webpage_url") or url,
            "image": _best_thumbnail(info),
        }
        return entries[:limit], meta

    def video_info(self, video_id_or_url: str) -> dict:
        import yt_dlp

        url = video_id_or_url
        if "/" not in url:
            url = f"https://www.youtube.com/watch?v={url}"
        try:
            with yt_dlp.YoutubeDL(_base_opts(self.cookies_file)) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            raise YouTubeError(f"Could not get info for {url}: {e}") from e
        keep = ("id", "title", "description", "duration", "timestamp", "release_timestamp",
                "upload_date", "thumbnail", "webpage_url", "live_status", "channel", "uploader")
        return {k: info.get(k) for k in keep}

    def download_audio(self, video_id: str, out_dir: Path, audio_format: str = "m4a",
                       sponsorblock_remove: list[str] | None = None) -> Path:
        import yt_dlp

        out_dir.mkdir(parents=True, exist_ok=True)
        postprocessors: list[dict[str, Any]] = []
        if audio_format == "m4a":
            fmt = "bestaudio[ext=m4a]/bestaudio/best"
        else:
            fmt = "bestaudio/best"
        if sponsorblock_remove:
            postprocessors += [
                {"key": "SponsorBlock", "categories": sponsorblock_remove},
                {"key": "ModifyChapters", "remove_sponsor_segments": sponsorblock_remove},
            ]
        # Always pass through FFmpegExtractAudio so the final container matches audio_format.
        postprocessors.append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_format,
            "preferredquality": "128" if audio_format == "mp3" else "0",
        })
        opts = _base_opts(self.cookies_file) | {
            "format": fmt,
            "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
            "postprocessors": postprocessors,
        }
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as e:
            raise YouTubeError(f"Download failed for {url}: {e}") from e
        path = out_dir / f"{video_id}.{audio_format}"
        if not path.exists():
            candidates = sorted(out_dir.glob(f"{video_id}.*"))
            if not candidates:
                raise YouTubeError(f"Download finished but no file found for {video_id}")
            path = candidates[0]
        return path


def _best_thumbnail(info: dict) -> str | None:
    thumbs = info.get("thumbnails") or []
    # Prefer square-ish avatars for podcast artwork.
    squares = [t for t in thumbs if t.get("width") and t.get("width") == t.get("height")]
    pick = max(squares or thumbs, key=lambda t: (t.get("width") or 0), default=None)
    return pick.get("url") if pick else info.get("thumbnail")
