"""A YouTube stand-in so tests (and UI demos) never touch the network."""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from yt2pod.youtube import YouTubeError


def _cid(n: int) -> str:
    return f"UC{str(n) * 22}"[:24]


class FakeYouTube:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.channels = {
            _cid(1): {"id": _cid(1), "title": "La Pizarra", "handle": "@lapizarra", "avatar": None,
                      "banner": None, "subscribers": 1_240_000, "description": "Charlas largas.",
                      "url": f"https://www.youtube.com/channel/{_cid(1)}", "verified": True},
            _cid(2): {"id": _cid(2), "title": "Pizarra Clips", "handle": "@pizarraclips", "avatar": None,
                      "banner": None, "subscribers": 85_000, "description": "Cortes.",
                      "url": f"https://www.youtube.com/channel/{_cid(2)}", "verified": False},
        }
        self.uploads: dict[str, list[dict]] = {
            _cid(1): [
                self._video("ep3aaaaaaaa", _cid(1), "#3 Entrevista completa con Ana", 5400, now - timedelta(days=1)),
                self._video("clip3aaaaaa", _cid(1), "El mejor momento del #3", 420, now - timedelta(days=2)),
                self._video("ep2aaaaaaaa", _cid(1), "#2 Charla sobre ciencia", 4100, now - timedelta(days=8)),
                self._video("ep1aaaaaaaa", _cid(1), "#1 Primer episodio", 3600, now - timedelta(days=15)),
            ],
            _cid(2): [self._video("clip2aaaaaa", _cid(2), "Resumen", 300, now - timedelta(days=3))],
        }
        self.downloads: list[str] = []
        self.fail_downloads: dict[str, str] = {}

    @staticmethod
    def _video(vid, cid, title, duration, published):
        return {"id": vid, "channel_id": cid, "title": title, "description": f"Descripción de {title}",
                "thumbnail": None, "duration": duration, "published": published.isoformat(timespec="seconds"),
                "is_short": False, "live_status": None}

    def publish(self, cid: str, vid: str, title: str, duration: int, is_short=False, live_status=None) -> None:
        v = self._video(vid, cid, title, duration, datetime.now(timezone.utc))
        v.update(is_short=is_short, live_status=live_status)
        self.uploads[cid].insert(0, v)

    # --- YouTube interface ---------------------------------------------------
    def search_channels(self, query, limit=12):
        q = query.casefold()
        return [c for c in self.channels.values() if q in c["title"].casefold()][:limit]

    def channel(self, source, limit=30):
        cid = source if source in self.channels else next(
            (c["id"] for c in self.channels.values() if c["handle"] == source.strip()), None)
        if not cid:
            raise YouTubeError(f"no channel {source}")
        return dict(self.channels[cid]), [dict(v, duration=v["duration"]) for v in self.uploads[cid][:limit]]

    def channel_videos(self, channel_id, start, count):
        return [dict(v) for v in self.uploads[channel_id][start - 1:start - 1 + count]]

    def search_channel(self, channel_id, query, limit=30):
        q = query.casefold()
        return [dict(v) for v in self.uploads[channel_id] if q in v["title"].casefold()][:limit]

    def channel_feed(self, channel_id):
        # Like the real RSS feed: no durations.
        return [dict(v, duration=None) for v in self.uploads[channel_id][:15]]

    def video_info(self, video_id):
        for vids in self.uploads.values():
            for v in vids:
                if v["id"] == video_id:
                    ts = int(datetime.fromisoformat(v["published"]).timestamp())
                    return {**v, "timestamp": ts}
        raise YouTubeError(f"Video unavailable: {video_id}")

    def download_audio(self, video_id, out_dir: Path, audio_format="m4a", sponsorblock_remove=None, progress=None):
        if video_id in self.fail_downloads:
            raise YouTubeError(self.fail_downloads[video_id])
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{video_id}.m4a"
        if progress:
            progress({"status": "downloading", "percent": 50.0})
        if shutil.which("ffmpeg"):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=3", "-c:a", "aac", "-b:a", "64k", str(path)], check=True)
        else:
            path.write_bytes(b"\0" * 1024)
        self.downloads.append(video_id)
        return path
