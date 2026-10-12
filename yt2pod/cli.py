"""Command line interface: yt2pod sync | browse | add | serve | run."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from . import __version__
from .config import Config, ConfigError, load_config
from .filters import fmt_duration
from .store import Store
from .sync import add_video, browse, rebuild_feed, sync_feed
from .youtube import YouTube, YouTubeError

log = logging.getLogger("yt2pod")


def _feeds(cfg: Config, ids: list[str] | None):
    return [cfg.feed(i) for i in ids] if ids else cfg.feeds


def cmd_sync(cfg: Config, args) -> int:
    store, yt = Store(cfg.db_path), YouTube(cfg.cookies_file)
    errors = 0
    for feed in _feeds(cfg, args.feed):
        try:
            r = sync_feed(cfg, feed, store, yt, dry_run=args.dry_run)
        except YouTubeError as e:
            log.error("[%s] %s", feed.id, e)
            errors += 1
            continue
        verb = "would download" if args.dry_run else "downloaded"
        print(f"[{feed.id}] {verb} {len(r.downloaded)}, skipped back-catalogue {len(r.ignored)}, "
              f"failed {len(r.failed)}, pruned {len(r.removed)}")
    return 1 if errors else 0


def cmd_browse(cfg: Config, args) -> int:
    store, yt = Store(cfg.db_path), YouTube(cfg.cookies_file)
    feed = cfg.feed(args.feed)
    rows = browse(cfg, feed, store, yt, args.limit, full=args.full)
    for r in rows:
        v = r.video
        mark = {"downloaded": "✔", "ignored": "·", "failed": "✗", "removed": "-"}.get(r.status or "", " ")
        verdict = "OK " if r.reason is None else "-- "
        date = v.published_date.isoformat() if v.published_date else "          "
        line = f"{mark} {verdict} {v.id}  {fmt_duration(v.duration):>8}  {date}  {v.title}"
        if r.reason and args.reasons:
            line += f"   [{r.reason}]"
        print(line)
    print("\n✔ downloaded  · skipped (back-catalogue)  ✗ failed   OK passes filters  -- filtered out")
    print(f"Add one with: yt2pod add {feed.id} <video-id>")
    return 0


def cmd_add(cfg: Config, args) -> int:
    store, yt = Store(cfg.db_path), YouTube(cfg.cookies_file)
    feed = cfg.feed(args.feed)
    for vid in args.video:
        ep = add_video(cfg, feed, store, yt, vid)
        print(f"[{feed.id}] added {ep.video_id}: {ep.title}")
    return 0


def cmd_feed(cfg: Config, args) -> int:
    store = Store(cfg.db_path)
    for feed in _feeds(cfg, args.feed):
        rebuild_feed(cfg, feed, store)
        print(f"{cfg.base_url}/feeds/{feed.id}.xml")
    return 0


def cmd_serve(cfg: Config, args) -> int:
    from .server import serve

    httpd = serve(cfg)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_run(cfg: Config, args) -> int:
    """Serve feeds and re-check every source on an interval (what you run on a home server)."""
    from .server import serve

    httpd = serve(cfg)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    store, yt = Store(cfg.db_path), YouTube(cfg.cookies_file)
    for feed in cfg.feeds:  # make feeds reachable before the first sync finishes
        rebuild_feed(cfg, feed, store)
    try:
        while True:
            for feed in cfg.feeds:
                try:
                    r = sync_feed(cfg, feed, store, yt)
                    if r.downloaded or r.failed:
                        log.info("[%s] downloaded %d, failed %d", feed.id, len(r.downloaded), len(r.failed))
                except YouTubeError as e:
                    log.error("[%s] %s", feed.id, e)
                except Exception:
                    log.exception("[%s] unexpected error", feed.id)
            log.info("Next check in %s", fmt_duration(cfg.check_interval))
            time.sleep(cfg.check_interval)
    except KeyboardInterrupt:
        httpd.shutdown()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yt2pod", description="Turn YouTube channels and playlists into podcast feeds.")
    p.add_argument("-c", "--config", default="config.yaml", help="path to config file (default: config.yaml)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"yt2pod {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("sync", help="check sources once and download new matching videos")
    s.add_argument("feed", nargs="*", help="feed ids (default: all)")
    s.add_argument("-n", "--dry-run", action="store_true", help="show what would be downloaded")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("browse", help="list a source's videos and whether they pass the filters")
    s.add_argument("feed")
    s.add_argument("-l", "--limit", type=int, default=30)
    s.add_argument("--full", action="store_true", help="fetch full metadata (dates, descriptions); slower")
    s.add_argument("-r", "--reasons", action="store_true", help="show why videos were filtered out")
    s.set_defaults(func=cmd_browse)

    s = sub.add_parser("add", help="download specific videos into a feed, ignoring filters")
    s.add_argument("feed")
    s.add_argument("video", nargs="+", help="video id or URL")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("feed", help="regenerate RSS files and print their URLs")
    s.add_argument("feed", nargs="*")
    s.set_defaults(func=cmd_feed)

    s = sub.add_parser("ui", help="open the local web interface (works without a config file)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    s.set_defaults(func=None)

    sub.add_parser("serve", help="only serve feeds and audio over HTTP").set_defaults(func=cmd_serve)
    sub.add_parser("run", help="serve + sync every check_interval").set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        if args.command == "ui":
            from pathlib import Path

            from .ui import run_ui

            run_ui(Path(args.config), args.host, args.port, open_browser=not args.no_browser)
            return 0
        cfg = load_config(args.config)
        return args.func(cfg, args)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2
    except YouTubeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
