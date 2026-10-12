"""Configuration loading and validation (YAML)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

_DURATION_RE = re.compile(r"^(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s?)?$")
_FEED_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ConfigError(ValueError):
    pass


def parse_duration(value: Any) -> int | None:
    """Parse '1h30m', '20m', '90s', '45' or an int into seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower().replace(" ", "")
    if ":" in text:  # hh:mm:ss or mm:ss
        parts = [int(p) for p in text.split(":")]
        seconds = 0
        for p in parts:
            seconds = seconds * 60 + p
        return seconds
    m = _DURATION_RE.match(text)
    if not m or not any(m.groups()):
        raise ConfigError(f"Invalid duration: {value!r} (use e.g. '20m', '1h30m', '90s')")
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + s


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ConfigError(f"Invalid date: {value!r} (use YYYY-MM-DD)")


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


@dataclass
class Filters:
    min_duration: int | None = None
    max_duration: int | None = None
    title_include: list[str] = field(default_factory=list)
    title_exclude: list[str] = field(default_factory=list)
    description_exclude: list[str] = field(default_factory=list)
    exclude_shorts: bool = True
    exclude_live: bool = True
    published_after: date | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Filters":
        data = dict(data or {})
        known = {
            "min_duration", "max_duration", "title_include", "title_exclude",
            "description_exclude", "exclude_shorts", "exclude_live", "published_after",
        }
        unknown = set(data) - known
        if unknown:
            raise ConfigError(f"Unknown filter option(s): {', '.join(sorted(unknown))}")
        f = cls(
            min_duration=parse_duration(data.get("min_duration")),
            max_duration=parse_duration(data.get("max_duration")),
            title_include=_as_list(data.get("title_include")),
            title_exclude=_as_list(data.get("title_exclude")),
            description_exclude=_as_list(data.get("description_exclude")),
            exclude_shorts=bool(data.get("exclude_shorts", True)),
            exclude_live=bool(data.get("exclude_live", True)),
            published_after=parse_date(data.get("published_after")),
        )
        for pattern in f.title_include + f.title_exclude + f.description_exclude:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ConfigError(f"Invalid regex {pattern!r}: {e}") from e
        return f


def merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged.update({k: v for k, v in override.items()})
    return merged


@dataclass
class FeedConfig:
    id: str
    source: str
    title: str | None = None
    description: str | None = None
    author: str | None = None
    image: str | None = None
    filters: Filters = field(default_factory=Filters)
    scan_limit: int = 30
    backfill: int = 3
    max_episodes: int | None = None
    audio_format: str = "m4a"
    sponsorblock_remove: list[str] = field(default_factory=list)


@dataclass
class Config:
    data_dir: Path
    base_url: str
    host: str = "0.0.0.0"
    port: int = 8080
    check_interval: int = 3600
    cookies_file: str | None = None
    feeds: list[FeedConfig] = field(default_factory=list)

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def feeds_dir(self) -> Path:
        return self.data_dir / "feeds"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "yt2pod.sqlite3"

    def feed(self, feed_id: str) -> FeedConfig:
        for f in self.feeds:
            if f.id == feed_id:
                return f
        raise ConfigError(f"Feed {feed_id!r} not found. Available: {', '.join(x.id for x in self.feeds)}")


_AUDIO_FORMATS = {"m4a", "mp3", "opus"}


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return config_from_dict(raw, base_dir=path.parent)


def config_from_dict(raw: dict[str, Any], base_dir: Path = Path(".")) -> Config:
    server = raw.get("server") or {}
    defaults = raw.get("defaults") or {}
    data_dir = Path(os.environ.get("YT2POD_DATA_DIR") or raw.get("data_dir", "./data"))
    if not data_dir.is_absolute():
        data_dir = (base_dir / data_dir).resolve()
    port = int(server.get("port", 8080))
    base_url = str(server.get("base_url") or f"http://localhost:{port}").rstrip("/")

    feeds_raw = raw.get("feeds") or []
    if not feeds_raw:
        raise ConfigError("No feeds defined: add at least one entry under 'feeds:'")

    feeds: list[FeedConfig] = []
    seen: set[str] = set()
    for item in feeds_raw:
        if "id" not in item or "source" not in item:
            raise ConfigError(f"Each feed needs 'id' and 'source': {item!r}")
        fid = str(item["id"])
        if not _FEED_ID_RE.match(fid):
            raise ConfigError(f"Feed id {fid!r} must be lowercase letters, digits, '-' or '_'")
        if fid in seen:
            raise ConfigError(f"Duplicate feed id: {fid}")
        seen.add(fid)

        filters = Filters.from_dict(merge_dicts(defaults.get("filters") or {}, item.get("filters") or {}))
        audio_format = str(item.get("audio_format", defaults.get("audio_format", "m4a")))
        if audio_format not in _AUDIO_FORMATS:
            raise ConfigError(f"audio_format must be one of {sorted(_AUDIO_FORMATS)}")
        max_eps = item.get("max_episodes", defaults.get("max_episodes"))
        feeds.append(FeedConfig(
            id=fid,
            source=str(item["source"]),
            title=item.get("title"),
            description=item.get("description"),
            author=item.get("author"),
            image=item.get("image"),
            filters=filters,
            scan_limit=int(item.get("scan_limit", defaults.get("scan_limit", 30))),
            backfill=int(item.get("backfill", defaults.get("backfill", 3))),
            max_episodes=int(max_eps) if max_eps else None,
            audio_format=audio_format,
            sponsorblock_remove=_as_list(item.get("sponsorblock_remove", defaults.get("sponsorblock_remove"))),
        ))

    return Config(
        data_dir=data_dir,
        base_url=base_url,
        host=str(server.get("host", "0.0.0.0")),
        port=port,
        check_interval=parse_duration(raw.get("check_interval", "1h")) or 3600,
        cookies_file=raw.get("cookies_file"),
        feeds=feeds,
    )
