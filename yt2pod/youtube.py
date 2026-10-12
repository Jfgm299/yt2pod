"""Thin wrapper around yt-dlp so the rest of the code (and the tests) don't touch it directly."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


AUDIO_EXTS = {"m4a", "mp3", "opus", "ogg", "webm", "aac", "flac"}


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

    def download_audio(self, video_id: str, out_dir: Path, audio_format: str = "best",
                       sponsorblock_remove: list[str] | None = None,
                       progress: Callable[[dict], None] | None = None) -> Path:
        """Download a video's audio track.

        audio_format:
          best  keep YouTube's best stream untouched (usually Opus ~160 kbps, otherwise AAC);
                no re-encoding, so no quality loss. Output .opus or .m4a.
          m4a   YouTube's AAC stream (128 kbps), copied without re-encoding. Plays everywhere (iOS).
          opus  Opus stream, copied without re-encoding.
          mp3   re-encoded to VBR V0 (~245 kbps). Maximum compatibility, but a lossy re-encode.
        progress: optional callback receiving dicts like
          {"status": "downloading", "percent": 42.0} / {"status": "processing"} /
          {"status": "finished", "acodec": "opus", "abr": 135.2, "format_id": "251"}.
        """
        import yt_dlp

        out_dir.mkdir(parents=True, exist_ok=True)
        fmt = {
            "best": "bestaudio/best",
            "m4a": "bestaudio[ext=m4a]/bestaudio/best",
            "opus": "bestaudio[acodec=opus]/bestaudio/best",
            "mp3": "bestaudio/best",
        }.get(audio_format, "bestaudio/best")
        postprocessors: list[dict[str, Any]] = []
        if sponsorblock_remove:
            postprocessors += [
                {"key": "SponsorBlock", "categories": sponsorblock_remove},
                {"key": "ModifyChapters", "remove_sponsor_segments": sponsorblock_remove},
            ]
        # FFmpegExtractAudio copies the stream when the codec already matches, so only
        # mp3 (or a codec mismatch) actually re-encodes.
        postprocessors.append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": "best" if audio_format == "best" else audio_format,
            "preferredquality": "0",  # best VBR quality when a re-encode is unavoidable
        })
        postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True})

        chosen: dict[str, Any] = {}

        def on_progress(d: dict) -> None:
            info = d.get("info_dict") or {}
            if info.get("format_id"):
                chosen.update(acodec=info.get("acodec"), abr=info.get("abr"), format_id=info.get("format_id"),
                              asr=info.get("asr"), title=info.get("title"))
            if progress is None:
                return
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                pct = (100.0 * d.get("downloaded_bytes", 0) / total) if total else None
                progress({"status": "downloading", "percent": pct, "speed": d.get("speed")})
            elif d.get("status") == "finished":
                progress({"status": "processing"})

        opts = _base_opts(self.cookies_file) | {
            "format": fmt,
            "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
            "postprocessors": postprocessors,
            "progress_hooks": [on_progress],
            "overwrites": True,
        }
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as e:
            raise YouTubeError(f"Download failed for {url}: {e}") from e
        candidates = [p for p in sorted(out_dir.glob(f"{video_id}.*")) if p.suffix.lstrip(".") in AUDIO_EXTS]
        preferred = out_dir / f"{video_id}.{audio_format}"
        if preferred.exists():
            path = preferred
        elif candidates:
            path = max(candidates, key=lambda p: p.stat().st_mtime)
        else:
            raise YouTubeError(f"Download finished but no audio file found for {video_id}")
        if progress:
            progress({"status": "finished", "file": path.name, "size": path.stat().st_size,
                      "source": chosen, "result": probe_audio(path)})
        return path


def probe_audio(path: Path) -> dict:
    """Codec, bitrate and sample rate of the file actually written (via ffprobe), best effort."""
    import json
    import shutil
    import subprocess

    if not shutil.which("ffprobe"):
        return {}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=codec_name,sample_rate,channels,bit_rate:format=bit_rate,duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(out.stdout)
    except Exception:
        return {}
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    bitrate = stream.get("bit_rate") or fmt.get("bit_rate")
    return {
        "codec": stream.get("codec_name"),
        "kbps": round(int(bitrate) / 1000) if bitrate and str(bitrate).isdigit() else None,
        "sample_rate": int(stream["sample_rate"]) if stream.get("sample_rate") else None,
        "channels": stream.get("channels"),
    }


def _best_thumbnail(info: dict) -> str | None:
    thumbs = info.get("thumbnails") or []
    # Prefer square-ish avatars for podcast artwork.
    squares = [t for t in thumbs if t.get("width") and t.get("width") == t.get("height")]
    pick = max(squares or thumbs, key=lambda t: (t.get("width") or 0), default=None)
    return pick.get("url") if pick else info.get("thumbnail")
