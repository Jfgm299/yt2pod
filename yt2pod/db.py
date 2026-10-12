"""SQLite storage: users, channels, subscriptions, videos, downloads and listening progress.

Audio files are shared: if two users follow the same channel, each video is downloaded once.
Each user has their own library (which episodes they have) and progress in it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE users (
        id            INTEGER PRIMARY KEY,
        username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        is_admin      INTEGER NOT NULL DEFAULT 0,
        rss_token     TEXT NOT NULL UNIQUE,
        created_at    TEXT NOT NULL
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        device     TEXT,
        created_at TEXT NOT NULL,
        last_seen  TEXT NOT NULL
    );
    CREATE TABLE channels (
        id          TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        handle      TEXT,
        avatar      TEXT,
        banner      TEXT,
        subscribers INTEGER,
        description TEXT,
        url         TEXT,
        checked_at  TEXT,                -- last RSS check by the watcher
        listed_at   TEXT,                -- last full listing of recent uploads (channel page)
        last_error  TEXT,
        updated_at  TEXT NOT NULL
    );
    CREATE TABLE subscriptions (
        user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        channel_id    TEXT NOT NULL REFERENCES channels(id),
        auto_download INTEGER NOT NULL DEFAULT 1,
        rules         TEXT NOT NULL DEFAULT '{}',
        created_at    TEXT NOT NULL,
        PRIMARY KEY (user_id, channel_id)
    );
    CREATE TABLE videos (
        id          TEXT PRIMARY KEY,
        channel_id  TEXT,
        title       TEXT NOT NULL,
        description TEXT,
        thumbnail   TEXT,
        duration    INTEGER,
        published   TEXT,
        is_short    INTEGER NOT NULL DEFAULT 0,
        live_status TEXT,
        complete    INTEGER NOT NULL DEFAULT 0,   -- full metadata fetched (duration, live status)
        first_seen  TEXT NOT NULL
    );
    CREATE INDEX videos_channel ON videos(channel_id, published);
    CREATE TABLE downloads (
        video_id      TEXT PRIMARY KEY REFERENCES videos(id),
        status        TEXT NOT NULL,     -- queued | downloading | done | failed | gave_up
        progress      REAL,
        attempts      INTEGER NOT NULL DEFAULT 0,
        next_try_at   TEXT,
        error         TEXT,
        filename      TEXT,
        filesize      INTEGER,
        mime          TEXT,
        audio         TEXT,              -- JSON: codec, kbps…
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    );
    CREATE TABLE library (
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        video_id   TEXT NOT NULL REFERENCES videos(id),
        state      TEXT NOT NULL DEFAULT 'active',   -- active | removed | skipped
        reason     TEXT,                             -- why the watcher skipped it
        added_at   TEXT NOT NULL,
        position   REAL NOT NULL DEFAULT 0,
        played     INTEGER NOT NULL DEFAULT 0,
        progress_at TEXT,                            -- last time position changed
        PRIMARY KEY (user_id, video_id)
    );
    CREATE INDEX library_user ON library(user_id, state, added_at);
    """,
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _progress_ts(at: str | None) -> str:
    """Normalise a client timestamp to UTC with milliseconds so they compare correctly as text.
    Clocks ahead of the server are clamped, or one device could freeze everyone else's progress."""
    server = datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat((at or "").replace("Z", "+00:00"))
        dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        dt = min(dt.astimezone(timezone.utc), server)
    except ValueError:
        dt = server
    return dt.isoformat(timespec="milliseconds")


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r else None


class DB:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self.lock:
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
                self.conn.executescript("BEGIN;" + script + f"; PRAGMA user_version={i}; COMMIT;")

    def q(self, sql: str, args: tuple | list = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple | list = ()) -> dict | None:
        with self.lock:
            return _row(self.conn.execute(sql, args).fetchone())

    def x(self, sql: str, args: tuple | list = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            # New row id for inserts, number of affected rows for everything else.
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount

    def close(self) -> None:
        self.conn.close()

    # --- channels ---------------------------------------------------------
    def upsert_channel(self, ch: dict) -> None:
        self.x(
            """INSERT INTO channels (id, title, handle, avatar, banner, subscribers, description, url, updated_at)
               VALUES (:id, :title, :handle, :avatar, :banner, :subscribers, :description, :url, :t)
               ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                 handle=COALESCE(excluded.handle, handle), avatar=COALESCE(excluded.avatar, avatar),
                 banner=COALESCE(excluded.banner, banner),
                 subscribers=COALESCE(excluded.subscribers, subscribers),
                 description=COALESCE(NULLIF(excluded.description, ''), description),
                 url=COALESCE(excluded.url, url), updated_at=excluded.updated_at""",
            {**{k: ch.get(k) for k in ("id", "title", "handle", "avatar", "banner", "subscribers",
                                       "description", "url")}, "t": now()},
        )

    def channel(self, channel_id: str) -> dict | None:
        return self.one("SELECT * FROM channels WHERE id=?", (channel_id,))

    def watched_channels(self) -> list[dict]:
        """Channels at least one user follows."""
        return self.q("""SELECT c.* FROM channels c WHERE EXISTS
                         (SELECT 1 FROM subscriptions s WHERE s.channel_id=c.id) ORDER BY c.checked_at""")

    def mark_listed(self, channel_id: str) -> None:
        self.x("UPDATE channels SET listed_at=? WHERE id=?", (now(), channel_id))

    def mark_checked(self, channel_id: str, error: str | None = None) -> None:
        self.x("UPDATE channels SET checked_at=?, last_error=? WHERE id=?", (now(), error, channel_id))

    # --- videos -------------------------------------------------------------
    def upsert_video(self, v: dict, complete: bool = False) -> None:
        """Insert a video or fill in fields we didn't know yet. Never forgets known values."""
        self.x(
            """INSERT INTO videos (id, channel_id, title, description, thumbnail, duration, published,
                                   is_short, live_status, complete, first_seen)
               VALUES (:id, :channel_id, :title, :description, :thumbnail, :duration, :published,
                       :is_short, :live_status, :complete, :t)
               ON CONFLICT(id) DO UPDATE SET
                 channel_id=COALESCE(excluded.channel_id, channel_id), title=excluded.title,
                 description=COALESCE(NULLIF(excluded.description, ''), description),
                 thumbnail=COALESCE(excluded.thumbnail, thumbnail),
                 duration=COALESCE(excluded.duration, duration),
                 published=COALESCE(excluded.published, published),
                 is_short=MAX(is_short, excluded.is_short),
                 live_status=COALESCE(excluded.live_status, live_status),
                 complete=MAX(complete, excluded.complete)""",
            {"id": v["id"], "channel_id": v.get("channel_id"), "title": v.get("title") or v["id"],
             "description": v.get("description") or "", "thumbnail": v.get("thumbnail"),
             "duration": v.get("duration"), "published": v.get("published"),
             "is_short": int(bool(v.get("is_short"))), "live_status": v.get("live_status"),
             "complete": int(complete), "t": now()},
        )

    def video(self, video_id: str) -> dict | None:
        return self.one("SELECT * FROM videos WHERE id=?", (video_id,))

    # --- users & sessions ---------------------------------------------------
    def user_count(self) -> int:
        return self.one("SELECT COUNT(*) n FROM users")["n"]

    def create_user(self, username: str, password_hash: str, rss_token: str, is_admin: bool) -> int:
        return self.x("INSERT INTO users (username, password_hash, is_admin, rss_token, created_at) "
                      "VALUES (?, ?, ?, ?, ?)", (username, password_hash, int(is_admin), rss_token, now()))

    def user_by_name(self, username: str) -> dict | None:
        return self.one("SELECT * FROM users WHERE username=?", (username,))

    def user_by_rss_token(self, token: str) -> dict | None:
        return self.one("SELECT * FROM users WHERE rss_token=?", (token,))

    # --- subscriptions --------------------------------------------------------
    def subscriptions(self, user_id: int) -> list[dict]:
        rows = self.q(
            """SELECT c.*, s.auto_download, s.rules, s.created_at AS subscribed_at,
                 (SELECT COUNT(*) FROM library l JOIN videos v ON v.id=l.video_id
                   WHERE l.user_id=s.user_id AND v.channel_id=c.id AND l.state='active' AND l.played=0) AS unplayed,
                 (SELECT MAX(v.published) FROM library l JOIN videos v ON v.id=l.video_id
                   WHERE l.user_id=s.user_id AND v.channel_id=c.id AND l.state='active') AS latest
               FROM subscriptions s JOIN channels c ON c.id=s.channel_id
               WHERE s.user_id=? ORDER BY COALESCE(latest, s.created_at) DESC""",
            (user_id,),
        )
        for r in rows:
            r["rules"] = json.loads(r["rules"] or "{}")
        return rows

    def subscription(self, user_id: int, channel_id: str) -> dict | None:
        r = self.one("SELECT * FROM subscriptions WHERE user_id=? AND channel_id=?", (user_id, channel_id))
        if r:
            r["rules"] = json.loads(r["rules"] or "{}")
        return r

    def subscribers_of(self, channel_id: str) -> list[dict]:
        rows = self.q("SELECT * FROM subscriptions WHERE channel_id=?", (channel_id,))
        for r in rows:
            r["rules"] = json.loads(r["rules"] or "{}")
        return rows

    def subscribe(self, user_id: int, channel_id: str, auto_download: bool, rules: dict) -> None:
        self.x("""INSERT INTO subscriptions (user_id, channel_id, auto_download, rules, created_at)
                  VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id, channel_id) DO UPDATE SET
                  auto_download=excluded.auto_download, rules=excluded.rules""",
               (user_id, channel_id, int(auto_download), json.dumps(rules), now()))

    def unsubscribe(self, user_id: int, channel_id: str) -> None:
        self.x("DELETE FROM subscriptions WHERE user_id=? AND channel_id=?", (user_id, channel_id))

    # --- downloads --------------------------------------------------------------
    def enqueue(self, video_id: str) -> None:
        """Queue a download unless the file already exists or is on its way.
        A download that gave up is retried when someone asks for it again."""
        self.x("""INSERT INTO downloads (video_id, status, created_at, updated_at) VALUES (?, 'queued', ?, ?)
                  ON CONFLICT(video_id) DO UPDATE SET status='queued', attempts=0, next_try_at=NULL,
                  error=NULL, updated_at=excluded.updated_at
                  WHERE downloads.status IN ('failed', 'gave_up')""", (video_id, now(), now()))

    def download(self, video_id: str) -> dict | None:
        return self.one("SELECT * FROM downloads WHERE video_id=?", (video_id,))

    def next_download(self) -> dict | None:
        return self.one("""SELECT * FROM downloads WHERE status='queued'
                           OR (status='failed' AND (next_try_at IS NULL OR next_try_at<=?))
                           ORDER BY created_at LIMIT 1""", (now(),))

    def update_download(self, video_id: str, **fields: Any) -> None:
        fields["updated_at"] = now()
        if "audio" in fields and not isinstance(fields["audio"], (str, type(None))):
            fields["audio"] = json.dumps(fields["audio"])
        sets = ", ".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE downloads SET {sets} WHERE video_id=?", (*fields.values(), video_id))

    def retry_blocked_downloads(self) -> int:
        """After new cookies arrive, retry at once everything YouTube refused as a bot."""
        return self.x("""UPDATE downloads SET status='queued', attempts=0, next_try_at=NULL, error=NULL, updated_at=?
                         WHERE status IN ('failed', 'gave_up') AND error LIKE '%bot%'""", (now(),))

    def orphaned_downloads(self) -> list[dict]:
        """Finished files nobody has in their library any more."""
        return self.q("""SELECT d.* FROM downloads d WHERE d.status='done' AND NOT EXISTS
                         (SELECT 1 FROM library l WHERE l.video_id=d.video_id AND l.state='active')""")

    # --- library ------------------------------------------------------------------
    def library_state(self, user_id: int, video_id: str) -> dict | None:
        return self.one("SELECT * FROM library WHERE user_id=? AND video_id=?", (user_id, video_id))

    def add_to_library(self, user_id: int, video_id: str) -> None:
        self.x("""INSERT INTO library (user_id, video_id, state, added_at) VALUES (?, ?, 'active', ?)
                  ON CONFLICT(user_id, video_id) DO UPDATE SET state='active', reason=NULL""",
               (user_id, video_id, now()))

    def skip(self, user_id: int, video_id: str, reason: str) -> None:
        """Remember the watcher looked at this video for this user and left it out."""
        self.x("""INSERT INTO library (user_id, video_id, state, reason, added_at) VALUES (?, ?, 'skipped', ?, ?)
                  ON CONFLICT(user_id, video_id) DO NOTHING""", (user_id, video_id, reason, now()))

    def remove_from_library(self, user_id: int, video_id: str) -> None:
        self.x("UPDATE library SET state='removed' WHERE user_id=? AND video_id=?", (user_id, video_id))

    def set_progress(self, user_id: int, video_id: str, position: float, played: bool | None = None,
                     at: str | None = None) -> bool:
        """Store the listening position. `at` is when the client recorded it; an older write
        from a device that was offline never overwrites a newer one from another device."""
        at = _progress_ts(at)
        args: list = [max(0.0, float(position)), at]
        sql = "UPDATE library SET position=?, progress_at=?"
        if played is not None:
            sql += ", played=?"
            args.append(int(played))
        sql += " WHERE user_id=? AND video_id=? AND (progress_at IS NULL OR progress_at<=?)"
        return bool(self.x(sql, (*args, user_id, video_id, at)))

    _EPISODE_SQL = """
        SELECT v.id, v.title, v.description, v.thumbnail, v.duration, v.published, v.channel_id,
               c.title AS channel_title, c.avatar AS channel_avatar,
               l.state, l.reason, l.added_at, l.position, l.played, l.progress_at,
               d.status AS download_status, d.progress AS download_progress, d.error AS download_error,
               d.filesize, d.mime, d.filename, d.attempts
        FROM library l JOIN videos v ON v.id=l.video_id
        LEFT JOIN channels c ON c.id=v.channel_id
        LEFT JOIN downloads d ON d.video_id=v.id
    """

    def episodes(self, user_id: int, *, channel_id: str | None = None, only_unplayed: bool = False,
                 in_progress: bool = False, limit: int = 200) -> list[dict]:
        sql = self._EPISODE_SQL + " WHERE l.user_id=? AND l.state='active'"
        args: list = [user_id]
        if channel_id:
            sql += " AND v.channel_id=?"
            args.append(channel_id)
        if only_unplayed:
            sql += " AND l.played=0"
        if in_progress:
            sql += " AND l.played=0 AND l.position>5 ORDER BY l.progress_at DESC"
        else:
            sql += " ORDER BY COALESCE(v.published, l.added_at) DESC"
        sql += " LIMIT ?"
        args.append(limit)
        return self.q(sql, args)

    def episode(self, user_id: int, video_id: str) -> dict | None:
        return self.one(self._EPISODE_SQL + " WHERE l.user_id=? AND v.id=?", (user_id, video_id))

    def channel_videos(self, user_id: int, channel_id: str, limit: int = 60) -> list[dict]:
        """Everything we know a channel uploaded, with this user's library state for each."""
        return self.q(
            """SELECT v.*, l.state, l.reason, l.position, l.played,
                      d.status AS download_status, d.progress AS download_progress
               FROM videos v LEFT JOIN library l ON l.video_id=v.id AND l.user_id=?
               LEFT JOIN downloads d ON d.video_id=v.id
               WHERE v.channel_id=? ORDER BY v.published DESC, v.first_seen DESC LIMIT ?""",
            (user_id, channel_id, limit),
        )
