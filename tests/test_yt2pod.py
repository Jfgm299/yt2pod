import threading
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from yt2pod.config import ConfigError, Filters, config_from_dict, parse_duration
from yt2pod.filters import check, video_from_info
from yt2pod.store import Store
from yt2pod.sync import add_video, browse, sync_feed
from yt2pod.youtube import normalize_source


class FakeYouTube:
    """Stands in for yt-dlp: a channel whose entries are newest-first."""

    def __init__(self, entries):
        self.entries = entries
        self.downloads = []
        self.info_calls = 0

    def list_videos(self, source, limit):
        return self.entries[:limit], {"title": "Fake Channel", "url": "https://youtube.com/@fake", "image": None}

    def video_info(self, vid):
        self.info_calls += 1
        e = next(x for x in self.entries if x["id"] == vid)
        return {**e, "description": f"desc {vid}"}

    def download_audio(self, vid, out_dir, audio_format="m4a", sponsorblock_remove=None, progress=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = "m4a" if audio_format == "best" else audio_format
        path = out_dir / f"{vid}.{ext}"
        if progress:
            progress({"status": "downloading", "percent": 50.0})
            progress({"status": "finished", "file": path.name, "acodec": "opus", "abr": 160})
        path.write_bytes(b"x" * 1000)
        self.downloads.append(vid)
        return path


def entry(vid, duration, title=None, url=None):
    # Ids ending in a digit get that day of the month, so higher numbers are newer.
    day = int(vid[-1]) if vid[-1].isdigit() else 1
    return {"id": vid, "title": title or f"Video {vid}", "duration": duration,
            "url": url or f"https://www.youtube.com/watch?v={vid}", "upload_date": f"202601{day:02d}"}


def make_cfg(tmp_path, **feed):
    return config_from_dict({
        "data_dir": str(tmp_path / "data"),
        "server": {"base_url": "http://pod.local:8080", "port": 0, "host": "127.0.0.1"},
        "feeds": [{"id": "test", "source": "@fake", **feed}],
    })


# --- config ---------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("20m", 1200), ("1h30m", 5400), ("90s", 90), ("45", 45), (300, 300), ("1:02:03", 3723), (None, None),
])
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


def test_config_rejects_bad_values(tmp_path):
    with pytest.raises(ConfigError):
        config_from_dict({"feeds": []})
    with pytest.raises(ConfigError):
        config_from_dict({"feeds": [{"id": "Bad Id", "source": "@x"}]})
    with pytest.raises(ConfigError):
        config_from_dict({"feeds": [{"id": "a", "source": "@x", "filters": {"min_lenght": "5m"}}]})
    with pytest.raises(ConfigError):
        config_from_dict({"feeds": [{"id": "a", "source": "@x", "filters": {"title_exclude": "("}}]})


def test_defaults_are_merged_and_overridden():
    cfg = config_from_dict({
        "defaults": {"backfill": 5, "filters": {"min_duration": "10m", "title_exclude": ["clip"]}},
        "feeds": [{"id": "a", "source": "@x", "filters": {"min_duration": "30m"}}],
    })
    f = cfg.feeds[0]
    assert f.backfill == 5
    assert f.filters.min_duration == 1800
    assert f.filters.title_exclude == ["clip"]


def test_normalize_source():
    assert normalize_source("@lex") == "https://www.youtube.com/@lex/videos"
    assert normalize_source("https://www.youtube.com/@lex") == "https://www.youtube.com/@lex/videos"
    assert normalize_source("https://www.youtube.com/@lex/streams") == "https://www.youtube.com/@lex/streams"
    assert normalize_source("PLabc").endswith("playlist?list=PLabc")
    url = "https://www.youtube.com/playlist?list=PLabc"
    assert normalize_source(url) == url


# --- filters --------------------------------------------------------------

def test_filters():
    f = Filters.from_dict({"min_duration": "20m", "title_exclude": [r"\bclip\b"], "title_include": []})
    assert check(video_from_info(entry("a", 3600)), f) is None
    assert "too short" in check(video_from_info(entry("b", 300)), f)
    assert "exclude" in check(video_from_info(entry("c", 3600, "Best CLIP ever")), f)
    short = entry("d", 3600, url="https://www.youtube.com/shorts/d")
    assert check(video_from_info(short), f) == "short"
    f2 = Filters.from_dict({"title_include": [r"^#\d+"]})
    assert check(video_from_info(entry("e", 3600, "#123 Guest")), f2) is None
    assert check(video_from_info(entry("f", 3600, "Trailer")), f2) is not None


# --- sync -----------------------------------------------------------------

def test_first_sync_backfills_then_picks_up_new_videos(tmp_path):
    cfg = make_cfg(tmp_path, backfill=2, audio_format="m4a", filters={"min_duration": "20m"})
    feed = cfg.feeds[0]
    store = Store(cfg.db_path)
    yt = FakeYouTube([entry("v5", 3600), entry("clip4", 120), entry("v3", 4000),
                      entry("v2", 3000), entry("v1", 3500)])

    r = sync_feed(cfg, feed, store, yt)
    assert sorted(r.downloaded) == ["v3", "v5"]          # the 2 newest long ones
    assert sorted(r.ignored) == ["v1", "v2"]             # older back-catalogue skipped
    assert "clip4" not in yt.downloads

    # Second run: nothing new.
    assert sync_feed(cfg, feed, store, yt).downloaded == []

    # A new long video and a new clip are uploaded.
    yt.entries = [entry("v7", 5000), entry("clip6", 90)] + yt.entries
    r = sync_feed(cfg, feed, store, yt)
    assert r.downloaded == ["v7"]

    xml = (cfg.feeds_dir / "test.xml").read_text()
    root = ET.fromstring(xml)
    items = root.findall("./channel/item")
    assert [i.findtext("guid") for i in items][0] == "yt:v7"
    enclosure = items[0].find("enclosure")
    assert enclosure.get("url") == "http://pod.local:8080/audio/test/v7.m4a"
    assert enclosure.get("type") == "audio/mp4"
    assert root.findtext("./channel/title") == "Fake Channel"


def test_dry_run_downloads_nothing(tmp_path):
    cfg = make_cfg(tmp_path, backfill=10)
    store = Store(cfg.db_path)
    yt = FakeYouTube([entry("a", 3600), entry("b", 3600)])
    r = sync_feed(cfg, cfg.feeds[0], store, yt, dry_run=True)
    assert r.downloaded == ["a", "b"] and yt.downloads == []
    assert not store.is_initialized("test")


def test_retention_deletes_oldest(tmp_path):
    cfg = make_cfg(tmp_path, backfill=10, max_episodes=2)
    store = Store(cfg.db_path)
    yt = FakeYouTube([entry("c", 3600, ), entry("b", 3600), entry("a", 3600)])
    for e, day in zip(yt.entries, ("20260103", "20260102", "20260101")):
        e["upload_date"] = day
    r = sync_feed(cfg, cfg.feeds[0], store, yt)
    assert r.removed == ["a"]
    assert not (cfg.audio_dir / "test" / "a.m4a").exists()
    assert [e.video_id for e in store.episodes("test")] == ["c", "b"]


def test_browse_and_manual_add(tmp_path):
    cfg = make_cfg(tmp_path, filters={"min_duration": "20m"})
    store = Store(cfg.db_path)
    yt = FakeYouTube([entry("long", 3600), entry("clip", 100)])
    rows = browse(cfg, cfg.feeds[0], store, yt, limit=10)
    assert [(r.video.id, r.reason is None) for r in rows] == [("long", True), ("clip", False)]

    ep = add_video(cfg, cfg.feeds[0], store, yt, "https://www.youtube.com/watch?v=clip&t=3")
    assert ep.video_id == "clip" and ep.status == "downloaded"
    assert "yt:clip" in (cfg.feeds_dir / "test.xml").read_text()
    assert not store.is_initialized("test")  # a later sync still applies backfill


def test_failed_download_is_retried(tmp_path):
    from yt2pod.youtube import YouTubeError

    cfg = make_cfg(tmp_path, backfill=5)
    store = Store(cfg.db_path)
    yt = FakeYouTube([entry("a", 3600)])
    real = yt.download_audio

    def boom(*a, **k):
        raise YouTubeError("network")
    yt.download_audio = boom
    assert sync_feed(cfg, cfg.feeds[0], store, yt).failed == ["a"]
    yt.download_audio = real
    assert sync_feed(cfg, cfg.feeds[0], store, yt).downloaded == ["a"]


# --- server ---------------------------------------------------------------

def test_server_serves_feed_and_ranges(tmp_path):
    from yt2pod.server import serve

    cfg = make_cfg(tmp_path, backfill=1)
    store = Store(cfg.db_path)
    sync_feed(cfg, cfg.feeds[0], store, FakeYouTube([entry("a", 3600)]))
    httpd = serve(cfg)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(f"{base}/feeds/test.xml") as r:
            assert b"<rss" in r.read()
        req = urllib.request.Request(f"{base}/audio/test/a.m4a", headers={"Range": "bytes=10-19"})
        with urllib.request.urlopen(req) as r:
            assert r.status == 206
            assert r.headers["Content-Range"] == "bytes 10-19/1000"
            assert len(r.read()) == 10
        with urllib.request.urlopen(f"{base}/") as r:
            assert b"/feeds/test.xml" in r.read()
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{base}/yt2pod.sqlite3")
    finally:
        httpd.shutdown()


# --- web UI ---------------------------------------------------------------

def test_ui_search_download_and_save(tmp_path):
    import json
    import time
    from http.server import ThreadingHTTPServer

    import yaml

    from yt2pod import ui

    config_path = tmp_path / "config.yaml"
    app = ui.App(ui.default_config(config_path, 0), config_path)
    app.yt = FakeYouTube([entry("long1", 3600), entry("clip2", 200)])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ui.make_ui_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    req = {"source": "@fake", "feed_id": "fake", "audio_format": "best",
           "filters": {"min_duration": "20m", "max_duration": "", "title_exclude": ["", "clip"]}}
    try:
        with urllib.request.urlopen(base + "/") as r:
            assert b"yt2pod" in r.read()
        status, data = post("/api/search", req)
        assert status == 200
        assert [(v["id"], v["reason"] is None) for v in data["videos"]] == [("long1", True), ("clip2", False)]

        assert post("/api/search", {**req, "feed_id": "Bad Name"})[0] == 400
        assert post("/api/search", {**req, "filters": {"min_duration": "veinte"}})[0] == 400

        status, data = post("/api/download", {**req, "videos": [{"id": "long1", "title": "Long"}]})
        assert status == 200 and len(data["jobs"]) == 1
        for _ in range(50):
            with urllib.request.urlopen(base + "/api/jobs") as r:
                jobs = json.load(r)
            if jobs[0]["status"] in ("done", "error"):
                break
            time.sleep(0.05)
        assert jobs[0]["status"] == "done", jobs
        assert jobs[0]["file"] == "/audio/fake/long1.m4a"
        with urllib.request.urlopen(base + "/feeds/fake.xml") as r:
            assert b"yt:long1" in r.read()

        status, data = post("/api/save-feed", req)
        assert status == 200
        saved = yaml.safe_load(config_path.read_text())
        assert saved["feeds"] == [{"id": "fake", "source": "@fake", "audio_format": "best",
                                   "filters": {"min_duration": "20m", "title_exclude": ["clip"]}}]
        ui_cfg = __import__("yt2pod.config", fromlist=["load_config"]).load_config(config_path)
        assert ui_cfg.feeds[0].filters.min_duration == 1200
    finally:
        httpd.shutdown()
