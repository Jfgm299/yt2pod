"""Local web UI: pick a channel, tune filters, preview matches, download audio, save the feed.

Run with `yt2pod ui` and open http://localhost:8765.
"""

from __future__ import annotations

import itertools
import json
import logging
import queue
import shutil
import threading
from dataclasses import asdict
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .config import Config, ConfigError, FeedConfig, Filters, _AUDIO_FORMATS, _FEED_ID_RE, load_config
from .filters import fmt_duration
from .server import make_handler
from .store import Store
from .sync import BrowseRow, download, evaluate, full_video, rebuild_feed
from .youtube import YouTube, YouTubeError, normalize_source

log = logging.getLogger(__name__)


def _filters_to_dict(f: Filters) -> dict[str, Any]:
    d = asdict(f)
    d["published_after"] = f.published_after.isoformat() if f.published_after else None
    return d


def _feed_to_dict(feed: FeedConfig) -> dict[str, Any]:
    return {
        "id": feed.id, "title": feed.title, "source": feed.source, "audio_format": feed.audio_format,
        "sponsorblock_remove": feed.sponsorblock_remove, "scan_limit": feed.scan_limit,
        "backfill": feed.backfill, "max_episodes": feed.max_episodes, "filters": _filters_to_dict(feed.filters),
    }


def _clean_filters(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop empty values coming from the form so they don't end up in config.yaml."""
    out = {}
    for k, v in (raw or {}).items():
        if isinstance(v, str):
            v = v.strip()
        if isinstance(v, list):
            v = [x.strip() for x in v if str(x).strip()]
        if v in (None, "", []):
            continue
        out[k] = v
    return out


class App:
    def __init__(self, cfg: Config, config_path: Path):
        self.cfg = cfg
        self.config_path = config_path
        self.store = Store(cfg.db_path)
        self.yt = YouTube(cfg.cookies_file)
        self.jobs: dict[int, dict[str, Any]] = {}
        self.jobs_lock = threading.Lock()
        self._ids = itertools.count(1)
        self.queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    # --- helpers ---------------------------------------------------------
    def feed_from_request(self, body: dict[str, Any]) -> FeedConfig:
        feed_id = str(body.get("feed_id") or "").strip().lower()
        if not _FEED_ID_RE.match(feed_id):
            raise ConfigError("El nombre del feed solo puede tener minúsculas, números, '-' y '_'")
        source = str(body.get("source") or "").strip()
        if not source:
            raise ConfigError("Indica un canal o una lista de reproducción")
        audio_format = str(body.get("audio_format") or "best")
        if audio_format not in _AUDIO_FORMATS:
            raise ConfigError(f"Formato de audio no válido: {audio_format}")
        return FeedConfig(
            id=feed_id,
            source=source,
            title=(body.get("title") or "").strip() or None,
            filters=Filters.from_dict(_clean_filters(body.get("filters") or {})),
            audio_format=audio_format,
            sponsorblock_remove=list(body.get("sponsorblock_remove") or []),
        )

    def state(self) -> dict[str, Any]:
        import yt_dlp.version

        return {
            "feeds": [_feed_to_dict(f) for f in self.cfg.feeds],
            "config_path": str(self.config_path),
            "data_dir": str(self.cfg.data_dir),
            "base_url": self.cfg.base_url,
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "yt_dlp": yt_dlp.version.__version__,
            "cookies": bool(self.cfg.cookies_file),
        }

    # --- search ----------------------------------------------------------
    def search(self, body: dict[str, Any]) -> dict[str, Any]:
        feed = self.feed_from_request(body)
        limit = max(1, min(int(body.get("limit") or 30), 500))
        full = bool(body.get("full"))
        entries, channel = self.yt.list_videos(feed.source, limit)
        rows: list[BrowseRow] = []
        for entry in entries:
            existing = self.store.get(feed.id, entry["id"])
            try:
                video, reason = evaluate(self.store, self.yt, feed, entry, fetch=full)
            except YouTubeError as e:
                video, reason = evaluate(self.store, self.yt, feed, entry, fetch=False)
                reason = reason or f"no se pudo leer: {e}"
            rows.append(BrowseRow(video, existing.status if existing else None, reason))
        if not self.store.channel_info(feed.id):
            self.store.save_channel_info(feed.id, channel)
        return {
            "source_url": normalize_source(feed.source),
            "channel": channel,
            "videos": [{
                "id": r.video.id,
                "title": r.video.title,
                "url": r.video.url,
                "duration": r.video.duration,
                "duration_str": fmt_duration(r.video.duration),
                "published": r.video.published_date.isoformat() if r.video.published_date else None,
                "thumbnail": r.video.thumbnail,
                "reason": r.reason,
                "status": r.status,
            } for r in rows],
        }

    # --- downloads -------------------------------------------------------
    def enqueue(self, body: dict[str, Any]) -> list[int]:
        feed = self.feed_from_request(body)
        ids = []
        for v in body.get("videos") or []:
            job_id = next(self._ids)
            job = {"id": job_id, "feed_id": feed.id, "video_id": v["id"], "title": v.get("title") or v["id"],
                   "status": "queued", "percent": None, "file": None, "audio": None, "error": None,
                   "format": feed.audio_format}
            with self.jobs_lock:
                self.jobs[job_id] = job
            self.queue.put((job_id, feed))
            ids.append(job_id)
        return ids

    def _update(self, job_id: int, **fields: Any) -> None:
        with self.jobs_lock:
            self.jobs[job_id].update(fields)

    def _worker(self) -> None:
        while True:
            job_id, feed = self.queue.get()
            job = self.jobs[job_id]
            try:
                self._update(job_id, status="fetching")
                video = full_video(self.store, self.yt, {"id": job["video_id"]})

                def progress(d: dict, job_id=job_id) -> None:
                    if d["status"] == "downloading":
                        self._update(job_id, status="downloading", percent=d.get("percent"))
                    elif d["status"] == "processing":
                        self._update(job_id, status="processing", percent=100.0)
                    elif d["status"] == "finished":
                        self._update(job_id, audio={"source": d.get("source"), "result": d.get("result"),
                                                    "size": d.get("size")})

                ep = download(self.cfg, feed, self.store, self.yt, video, progress=progress)
                rebuild_feed(self.cfg, feed, self.store)
                self._update(job_id, status="done", file=f"/audio/{feed.id}/{ep.filename}",
                             feed_url=f"{self.cfg.base_url}/feeds/{feed.id}.xml")
            except Exception as e:  # keep the worker alive whatever happens
                log.exception("download failed")
                self._update(job_id, status="error", error=str(e)[:400])

    def job_list(self) -> list[dict[str, Any]]:
        with self.jobs_lock:
            return [dict(j) for j in sorted(self.jobs.values(), key=lambda j: j["id"])]

    # --- saving the feed to config.yaml ----------------------------------
    def save_feed(self, body: dict[str, Any]) -> dict[str, Any]:
        feed = self.feed_from_request(body)  # validates
        entry: dict[str, Any] = {"id": feed.id, "source": feed.source}
        if feed.title:
            entry["title"] = feed.title
        entry["audio_format"] = feed.audio_format
        if feed.sponsorblock_remove:
            entry["sponsorblock_remove"] = feed.sponsorblock_remove
        for key in ("backfill", "max_episodes", "scan_limit"):
            if body.get(key) not in (None, ""):
                entry[key] = int(body[key])
        filters = _clean_filters(body.get("filters") or {})
        if filters:
            entry["filters"] = filters

        path = self.config_path
        raw: dict[str, Any] = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            shutil.copyfile(path, path.with_suffix(path.suffix + ".bak"))
        else:
            raw = {"data_dir": "./data", "check_interval": "1h",
                   "server": {"host": "0.0.0.0", "port": 8080, "base_url": "http://localhost:8080"}}
        feeds = [f for f in (raw.get("feeds") or []) if f.get("id") != feed.id]
        replaced = len(feeds) != len(raw.get("feeds") or [])
        feeds.append(entry)
        raw["feeds"] = feeds
        path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self.cfg.feeds = [f for f in self.cfg.feeds if f.id != feed.id] + [feed]
        return {"path": str(path), "replaced": replaced}


def make_ui_handler(app: App):
    Base = make_handler(app.cfg)
    page = resources.files("yt2pod").joinpath("ui.html").read_text(encoding="utf-8")

    class UIHandler(Base):
        def _json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/ui"):
                return self._send_bytes(page.encode(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self._json(app.state())
            if path == "/api/jobs":
                return self._json(app.job_list())
            return super().do_GET()

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            routes = {"/api/search": app.search, "/api/download": lambda b: {"jobs": app.enqueue(b)},
                      "/api/save-feed": app.save_feed}
            if path not in routes:
                return self.send_error(HTTPStatus.NOT_FOUND)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                return self._json(routes[path](body))
            except (ConfigError, ValueError) as e:
                return self._json({"error": str(e)}, 400)
            except YouTubeError as e:
                return self._json({"error": str(e)}, 502)
            except Exception as e:
                log.exception("UI request failed")
                return self._json({"error": f"Error inesperado: {e}"}, 500)

    return UIHandler


def default_config(config_path: Path, port: int) -> Config:
    """Config used when there is no config.yaml yet: everything under ./data next to it."""
    return Config(data_dir=(config_path.parent / "data").resolve(), base_url=f"http://localhost:{port}",
                  host="127.0.0.1", port=port)


def run_ui(config_path: Path, host: str, port: int, open_browser: bool = True) -> None:
    if config_path.exists():
        try:
            cfg = load_config(config_path)
        except ConfigError as e:
            if "No feeds defined" not in str(e):
                raise
            cfg = default_config(config_path, port)
        cfg.base_url = f"http://localhost:{port}"
    else:
        cfg = default_config(config_path, port)
    app = App(cfg, config_path)
    httpd = ThreadingHTTPServer((host, port), make_ui_handler(app))
    url = f"http://localhost:{httpd.server_address[1]}"
    print(f"yt2pod UI en {url}  (Ctrl+C para salir)")
    if open_browser:
        import webbrowser

        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
