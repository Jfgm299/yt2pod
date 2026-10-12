"""SQLite state: which videos were downloaded, ignored or failed per feed."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    feed_id     TEXT NOT NULL,
    video_id    TEXT NOT NULL,
    status      TEXT NOT NULL,           -- downloaded | ignored | failed | removed
    title       TEXT,
    description TEXT,
    url         TEXT,
    thumbnail   TEXT,
    duration    INTEGER,
    published   TEXT,                    -- ISO 8601
    filename    TEXT,
    filesize    INTEGER,
    error       TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (feed_id, video_id)
);
CREATE TABLE IF NOT EXISTS video_cache (
    video_id TEXT PRIMARY KEY,
    info     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_state (
    feed_id      TEXT PRIMARY KEY,
    initialized  INTEGER NOT NULL DEFAULT 0,
    channel_info TEXT,
    last_sync    TEXT
);
"""


@dataclass
class Episode:
    feed_id: str
    video_id: str
    status: str
    title: str | None = None
    description: str | None = None
    url: str | None = None
    thumbnail: str | None = None
    duration: int | None = None
    published: str | None = None
    filename: str | None = None
    filesize: int | None = None
    error: str | None = None
    updated_at: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Shared between the web UI's request threads and its download worker.
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def _q(self, sql: str, args: tuple | list = (), fetch: str | None = None):
        with self.lock:
            cur = self.db.execute(sql, args)
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
            return None

    def close(self) -> None:
        self.db.close()

    # --- episodes -------------------------------------------------------
    def get(self, feed_id: str, video_id: str) -> Episode | None:
        row = self._q("SELECT * FROM episodes WHERE feed_id=? AND video_id=?", (feed_id, video_id), fetch="one")
        return Episode(**dict(row)) if row else None

    def upsert(self, ep: Episode) -> None:
        ep.updated_at = _now()
        data = ep.__dict__
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        self._q(f"INSERT OR REPLACE INTO episodes ({cols}) VALUES ({marks})", tuple(data.values()))

    def episodes(self, feed_id: str, status: str | None = "downloaded") -> list[Episode]:
        q = "SELECT * FROM episodes WHERE feed_id=?"
        args: list = [feed_id]
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY COALESCE(published, updated_at) DESC"
        return [Episode(**dict(r)) for r in self._q(q, args, fetch="all")]

    # --- metadata cache -------------------------------------------------
    def cached_info(self, video_id: str) -> dict | None:
        row = self._q("SELECT info FROM video_cache WHERE video_id=?", (video_id,), fetch="one")
        return json.loads(row["info"]) if row else None

    def cache_info(self, video_id: str, info: dict) -> None:
        self._q(
            "INSERT OR REPLACE INTO video_cache (video_id, info) VALUES (?, ?)",
            (video_id, json.dumps(info)),
        )

    # --- feed state -----------------------------------------------------
    def is_initialized(self, feed_id: str) -> bool:
        row = self._q("SELECT initialized FROM feed_state WHERE feed_id=?", (feed_id,), fetch="one")
        return bool(row and row["initialized"])

    def mark_synced(self, feed_id: str, channel_info: dict | None = None) -> None:
        self._q(
            """INSERT INTO feed_state (feed_id, initialized, channel_info, last_sync) VALUES (?, 1, ?, ?)
               ON CONFLICT(feed_id) DO UPDATE SET initialized=1, last_sync=excluded.last_sync,
               channel_info=COALESCE(excluded.channel_info, feed_state.channel_info)""",
            (feed_id, json.dumps(channel_info) if channel_info else None, _now()),
        )

    def save_channel_info(self, feed_id: str, channel_info: dict) -> None:
        self._q(
            """INSERT INTO feed_state (feed_id, initialized, channel_info) VALUES (?, 0, ?)
               ON CONFLICT(feed_id) DO UPDATE SET channel_info=excluded.channel_info""",
            (feed_id, json.dumps(channel_info)),
        )

    def channel_info(self, feed_id: str) -> dict:
        row = self._q("SELECT channel_info FROM feed_state WHERE feed_id=?", (feed_id,), fetch="one")
        return json.loads(row["channel_info"]) if row and row["channel_info"] else {}
