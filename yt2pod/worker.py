"""Background work: watch subscribed channels for new uploads and download audio, one at a time."""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import DB, now
from .filters import Rules, check
from .settings import Settings
from .youtube import YouTube, YouTubeError, probe_audio

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
MIME = {"m4a": "audio/mp4", "mp3": "audio/mpeg", "opus": "audio/ogg", "ogg": "audio/ogg",
        "webm": "audio/webm", "aac": "audio/aac", "flac": "audio/flac"}
# Errors that won't fix themselves by retrying.
PERMANENT = ("private video", "video unavailable", "members-only", "join this channel",
             "has been removed", "copyright", "not available in your country")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Worker:
    def __init__(self, settings: Settings, db: DB, yt: YouTube):
        self.settings = settings
        self.db = db
        self.yt = yt
        self._wake_downloads = threading.Event()
        self._wake_watch = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.watch_status: dict = {"running": False, "last_run": None, "next_run": None}

    # --- lifecycle --------------------------------------------------------
    def start(self) -> None:
        for target, name in ((self._download_loop, "downloads"), (self._watch_loop, "watcher")):
            t = threading.Thread(target=target, name=f"yt2pod-{name}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._wake_downloads.set()
        self._wake_watch.set()

    def kick_downloads(self) -> None:
        self._wake_downloads.set()

    def check_now(self) -> None:
        self._wake_watch.set()

    # --- watching -----------------------------------------------------------
    def _watch_loop(self) -> None:
        time.sleep(3)  # let the server come up first
        while not self._stop.is_set():
            self.watch_status["running"] = True
            try:
                self.check_all()
            except Exception:  # never let the loop die
                log.exception("watcher crashed")
            self.watch_status.update(running=False, last_run=now())
            # Jitter so requests don't look like clockwork.
            delay = self.settings.check_interval * random.uniform(0.85, 1.15)
            self.watch_status["next_run"] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(
                timespec="seconds")
            self._wake_watch.wait(delay)
            self._wake_watch.clear()

    def check_all(self) -> None:
        for ch in self.db.watched_channels():
            if self._stop.is_set():
                return
            try:
                self.check_channel(ch["id"])
            except YouTubeError as e:
                log.warning("checking %s failed: %s", ch["title"], e)
                self.db.mark_checked(ch["id"], str(e)[:300])
            time.sleep(random.uniform(1, 3))

    def check_channel(self, channel_id: str) -> list[str]:
        """Look at the channel's RSS feed and route each new upload to the users who follow it.
        Returns the ids of videos added to at least one library."""
        entries = self.yt.channel_feed(channel_id)
        subs = [s for s in self.db.subscribers_of(channel_id) if s["auto_download"]]
        added: list[str] = []
        for entry in entries:
            self.db.upsert_video(entry)
            published = _parse(entry.get("published"))
            # Only uploads after the user subscribed; older ones are picked by hand in the UI.
            pending = [s for s in subs if published and published >= _parse(s["created_at"]) - timedelta(minutes=5)
                       and not self.db.library_state(s["user_id"], entry["id"])]
            if not pending:
                continue
            video = self._complete(entry["id"])
            if video is None:
                continue
            if video.get("live_status") in ("is_live", "is_upcoming", "post_live"):
                continue  # premieres and streams: look again on the next check
            for s in pending:
                reason = check(video, Rules.from_dict(s["rules"]))
                if reason:
                    self.db.skip(s["user_id"], video["id"], reason)
                else:
                    self.db.add_to_library(s["user_id"], video["id"])
                    self.db.enqueue(video["id"])
                    added.append(video["id"])
        self.db.mark_checked(channel_id)
        if added:
            self.kick_downloads()
        return added

    def _complete(self, video_id: str) -> dict | None:
        """The video with full metadata (duration, live status), fetching it once."""
        video = self.db.video(video_id)
        if video and video["complete"]:
            return video
        try:
            info = self.yt.video_info(video_id)
        except YouTubeError as e:
            log.warning("could not read %s: %s", video_id, e)
            return None
        published = None
        if info.get("timestamp"):
            published = datetime.fromtimestamp(int(info["timestamp"]), timezone.utc).isoformat(timespec="seconds")
        self.db.upsert_video({
            "id": video_id, "channel_id": info.get("channel_id"), "title": info.get("title"),
            "description": info.get("description"), "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"), "published": published,
            "is_short": info.get("is_short"), "live_status": info.get("live_status"),
        }, complete=info.get("live_status") not in ("is_live", "is_upcoming", "post_live"))
        return self.db.video(video_id)

    # --- downloading ------------------------------------------------------------
    def _download_loop(self) -> None:
        self.db.x("UPDATE downloads SET status='queued' WHERE status='downloading'")  # interrupted by a restart
        while not self._stop.is_set():
            job = self.db.next_download()
            if job is None:
                self._wake_downloads.wait(60)
                self._wake_downloads.clear()
                continue
            self.download(job["video_id"])
            time.sleep(random.uniform(2, 5))

    def download(self, video_id: str) -> None:
        job = self.db.download(video_id) or {}
        video = self.db.video(video_id) or {"id": video_id, "channel_id": None}
        self.db.update_download(video_id, status="downloading", progress=0)
        out_dir = self.settings.audio_dir / (video.get("channel_id") or "_")

        def progress(p: dict) -> None:
            if p.get("status") == "downloading" and p.get("percent") is not None:
                self.db.update_download(video_id, progress=round(p["percent"], 1))
            elif p.get("status") == "processing":
                self.db.update_download(video_id, progress=100)

        try:
            path = self.yt.download_audio(video_id, out_dir, self.settings.audio_format,
                                          self.settings.sponsorblock or None, progress=progress)
        except YouTubeError as e:
            attempts = (job.get("attempts") or 0) + 1
            msg = str(e)
            permanent = any(p in msg.lower() for p in PERMANENT)
            gave_up = permanent or attempts >= MAX_ATTEMPTS
            retry = datetime.now(timezone.utc) + timedelta(minutes=10 * 3 ** (attempts - 1))
            log.error("download %s failed (attempt %d): %s", video_id, attempts, msg)
            self.db.update_download(video_id, status="gave_up" if gave_up else "failed", attempts=attempts,
                                    error=_friendly_error(msg), next_try_at=retry.isoformat(timespec="seconds"),
                                    progress=None)
            return
        if not video.get("complete") or not video.get("duration"):
            self._complete(video_id)
        self.db.update_download(
            video_id, status="done", progress=100, error=None, filename=str(path.relative_to(self.settings.audio_dir)),
            filesize=path.stat().st_size, mime=MIME.get(path.suffix.lstrip("."), "audio/mpeg"),
            audio=probe_audio(path),
        )
        log.info("downloaded %s", video_id)

    def prune(self) -> list[str]:
        """Delete audio files nobody has in their library any more."""
        removed = []
        for d in self.db.orphaned_downloads():
            if d["filename"]:
                (self.settings.audio_dir / d["filename"]).unlink(missing_ok=True)
            self.db.x("DELETE FROM downloads WHERE video_id=?", (d["video_id"],))
            removed.append(d["video_id"])
        return removed

    def audio_path(self, download: dict) -> Path | None:
        if not download or download.get("status") != "done" or not download.get("filename"):
            return None
        path = (self.settings.audio_dir / download["filename"]).resolve()
        if self.settings.audio_dir.resolve() not in path.parents or not path.exists():
            return None
        return path


def _friendly_error(msg: str) -> str:
    low = msg.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        return "YouTube pide verificar que no eres un bot. Configura cookies o ejecuta yt2pod desde casa."
    if "private video" in low:
        return "El vídeo es privado."
    if "members-only" in low or "join this channel" in low:
        return "Vídeo solo para miembros del canal."
    if "unavailable" in low or "removed" in low:
        return "El vídeo ya no está disponible."
    if "429" in low or "too many requests" in low:
        return "YouTube está limitando las peticiones. Se reintentará más tarde."
    return msg[:300]
