"""Private podcast RSS feeds, for people who'd rather use Overcast, Pocket Casts or Apple Podcasts."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape


def _rfc822(iso: str | None) -> str:
    try:
        dt = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt)


def _hms(seconds: int | None) -> str:
    s = int(seconds or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def build_rss(*, title: str, description: str, link: str, image: str | None, self_url: str,
              episodes: list[dict], audio_url) -> str:
    """episodes: rows from DB.episodes(); audio_url(ep) → absolute URL of the file."""
    items = []
    for ep in episodes:
        if ep.get("download_status") != "done":
            continue
        img = f'<itunes:image href="{escape(ep["thumbnail"])}"/>' if ep.get("thumbnail") else ""
        items.append(f"""    <item>
      <title>{escape(ep["title"])}</title>
      <description>{escape(ep.get("description") or "")}</description>
      <link>https://www.youtube.com/watch?v={ep["id"]}</link>
      <guid isPermaLink="false">yt:{ep["id"]}</guid>
      <pubDate>{_rfc822(ep.get("published") or ep.get("added_at"))}</pubDate>
      <enclosure url="{escape(audio_url(ep))}" length="{ep.get("filesize") or 0}" type="{ep.get("mime") or "audio/mp4"}"/>
      <itunes:duration>{_hms(ep.get("duration"))}</itunes:duration>
      <itunes:author>{escape(ep.get("channel_title") or "")}</itunes:author>
      {img}
    </item>""")
    image_tags = (f'<itunes:image href="{escape(image)}"/>\n    <image><url>{escape(image)}</url>'
                  f'<title>{escape(title)}</title><link>{escape(link)}</link></image>') if image else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(title)}</title>
    <link>{escape(link)}</link>
    <description>{escape(description or title)}</description>
    <language>es</language>
    <atom:link href="{escape(self_url)}" rel="self" type="application/rss+xml"/>
    <itunes:block>Yes</itunes:block>
    {image_tags}
{chr(10).join(items)}
  </channel>
</rss>
"""
