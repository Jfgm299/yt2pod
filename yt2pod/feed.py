"""Podcast RSS 2.0 generation (with iTunes tags so Apple Podcasts, Pocket Casts, AntennaPod... accept it)."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from .config import Config, FeedConfig
from .filters import fmt_duration
from .store import Episode

MIME = {"m4a": "audio/mp4", "mp3": "audio/mpeg", "opus": "audio/ogg", "webm": "audio/webm"}


def _rfc2822(iso: str | None) -> str:
    if not iso:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt)


def _cdata(text: str) -> str:
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def render_feed(cfg: Config, feed: FeedConfig, episodes: list[Episode], channel: dict) -> str:
    title = feed.title or channel.get("title") or feed.id
    description = feed.description or channel.get("description") or f"Audio from {feed.source}"
    author = feed.author or channel.get("title") or "yt2pod"
    image = feed.image or channel.get("image")
    link = channel.get("url") or feed.source
    self_url = f"{cfg.base_url}/feeds/{feed.id}.xml"

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        'xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/">',
        "<channel>",
        f"<title>{escape(title)}</title>",
        f"<link>{escape(link)}</link>",
        f'<atom:link href="{escape(self_url)}" rel="self" type="application/rss+xml"/>',
        f"<description>{_cdata(description)}</description>",
        "<language>und</language>",
        "<generator>yt2pod</generator>",
        f"<lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>",
        f"<itunes:author>{escape(author)}</itunes:author>",
        f"<itunes:summary>{_cdata(description)}</itunes:summary>",
        "<itunes:explicit>false</itunes:explicit>",
        "<itunes:type>episodic</itunes:type>",
    ]
    if image:
        out += [
            f'<itunes:image href="{escape(image)}"/>',
            f"<image><url>{escape(image)}</url><title>{escape(title)}</title><link>{escape(link)}</link></image>",
        ]
    for ep in episodes:
        if not ep.filename:
            continue
        ext = Path(ep.filename).suffix.lstrip(".")
        enclosure = f"{cfg.base_url}/audio/{quote(feed.id)}/{quote(ep.filename)}"
        desc = (ep.description or "") + f"\n\nOriginal: {ep.url}"
        out += [
            "<item>",
            f"<title>{escape(ep.title or ep.video_id)}</title>",
            f'<guid isPermaLink="false">yt:{escape(ep.video_id)}</guid>',
            f"<link>{escape(ep.url or '')}</link>",
            f"<pubDate>{_rfc2822(ep.published or ep.updated_at)}</pubDate>",
            f"<description>{_cdata(desc)}</description>",
            f'<enclosure url="{escape(enclosure)}" length="{ep.filesize or 0}" type="{MIME.get(ext, "audio/mpeg")}"/>',
        ]
        if ep.duration:
            out.append(f"<itunes:duration>{fmt_duration(ep.duration)}</itunes:duration>")
        if ep.thumbnail:
            out.append(f'<itunes:image href="{escape(ep.thumbnail)}"/>')
        out.append("</item>")
    out += ["</channel>", "</rss>", ""]
    return "\n".join(out)


def write_feed(cfg: Config, feed: FeedConfig, episodes: list[Episode], channel: dict) -> Path:
    cfg.feeds_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.feeds_dir / f"{feed.id}.xml"
    tmp = path.with_suffix(".xml.tmp")
    tmp.write_text(render_feed(cfg, feed, episodes, channel), encoding="utf-8")
    tmp.replace(path)
    return path


def render_opml(cfg: Config) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0"><head><title>yt2pod feeds</title></head><body>',
    ]
    for f in cfg.feeds:
        url = f"{cfg.base_url}/feeds/{f.id}.xml"
        name = escape(f.title or f.id, {'"': "&quot;"})
        lines.append(f'<outline type="rss" text="{name}" title="{name}" xmlUrl="{escape(url)}"/>')
    lines += ["</body></opml>", ""]
    return "\n".join(lines)
