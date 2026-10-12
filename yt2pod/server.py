"""Minimal HTTP server for feeds and audio files (supports Range requests, which podcast apps need for seeking)."""

from __future__ import annotations

import html
import logging
import os
import re
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import Config
from .feed import render_opml

log = logging.getLogger(__name__)
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


def make_handler(cfg: Config):
    root = cfg.data_dir

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        extensions_map = {
            **SimpleHTTPRequestHandler.extensions_map,
            ".xml": "application/rss+xml; charset=utf-8",
            ".m4a": "audio/mp4",
            ".mp3": "audio/mpeg",
            ".opus": "audio/ogg",
            ".opml": "text/x-opml; charset=utf-8",
        }

        def log_message(self, fmt, *args):
            log.info("%s - %s", self.address_string(), fmt % args)

        def _allowed(self) -> bool:
            path = self.path.split("?", 1)[0]
            return path in ("/", "/feeds.opml") or path.startswith(("/feeds/", "/audio/"))

        def list_directory(self, path):  # no directory listings
            self.send_error(HTTPStatus.NOT_FOUND)
            return None

        def do_HEAD(self):
            if not self._allowed():
                return self.send_error(HTTPStatus.NOT_FOUND)
            return super().do_HEAD()

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                return self._send_bytes(self._index().encode(), "text/html; charset=utf-8")
            if path == "/feeds.opml":
                return self._send_bytes(render_opml(cfg).encode(), "text/x-opml; charset=utf-8")
            if not self._allowed():
                return self.send_error(HTTPStatus.NOT_FOUND)
            range_header = self.headers.get("Range")
            if range_header and path.startswith("/audio/"):
                return self._send_range(range_header)
            return super().do_GET()

        def _send_bytes(self, body: bytes, ctype: str):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_range(self, range_header: str):
            fs_path = Path(self.translate_path(self.path))
            if not fs_path.is_file():
                return self.send_error(HTTPStatus.NOT_FOUND)
            size = fs_path.stat().st_size
            m = _RANGE_RE.match(range_header.strip())
            if not m or (not m.group(1) and not m.group(2)):
                return super().do_GET()
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:  # suffix range: last N bytes
                start = max(0, size - int(m.group(2)))
                end = size - 1
            end = min(end, size - 1)
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            length = end - start + 1
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Type", self.guess_type(str(fs_path)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with open(fs_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    remaining -= len(chunk)

        def end_headers(self):
            if self.path.startswith("/audio/"):
                self.send_header("Accept-Ranges", "bytes")
            super().end_headers()

        def _index(self) -> str:
            items = []
            for f in cfg.feeds:
                url = f"{cfg.base_url}/feeds/{f.id}.xml"
                exists = (cfg.feeds_dir / f"{f.id}.xml").exists()
                note = "" if exists else " <em>(not synced yet)</em>"
                items.append(
                    f'<li><strong>{html.escape(f.title or f.id)}</strong>{note}<br>'
                    f'<a href="{html.escape(url)}">{html.escape(url)}</a></li>'
                )
            return (
                "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                "<title>yt2pod</title><body style='font-family:system-ui;max-width:40rem;margin:2rem auto;padding:0 1rem'>"
                "<h1>yt2pod</h1><p>Copy a feed URL into your podcast app "
                f"(or import <a href='{html.escape(cfg.base_url)}/feeds.opml'>all feeds as OPML</a>).</p>"
                f"<ul>{''.join(items)}</ul></body>"
            )

    return Handler


def serve(cfg: Config) -> ThreadingHTTPServer:
    os.makedirs(cfg.data_dir, exist_ok=True)
    httpd = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(cfg))
    log.info("Serving feeds on http://%s:%s (public URL %s)", cfg.host, cfg.port, cfg.base_url)
    return httpd
