from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from yt2pod import auth
from yt2pod.api import create_app
from yt2pod.db import DB
from yt2pod.filters import Rules, check
from yt2pod.settings import Settings
from yt2pod.youtube import entry_to_video, looks_like_channel_ref, parse_channel_feed

from .fakes import FakeYouTube, _cid

LA_PIZARRA = _cid(1)


@pytest.fixture
def env(tmp_path):
    yt = FakeYouTube()
    app = create_app(Settings(data_dir=tmp_path), yt=yt, start_worker=False)
    client = TestClient(app)
    r = client.post("/api/setup", json={"username": "jf", "password": "contraseña1"})
    assert r.status_code == 200, r.text
    return client, app, yt


def drain(app):
    w = app.state.worker
    while (job := app.state.db.next_download()) is not None:
        w.download(job["video_id"])


# --- accounts -------------------------------------------------------------------
def test_setup_only_once_and_login_from_second_device(env):
    client, app, _ = env
    assert client.get("/api/session").json()["user"]["username"] == "jf"
    assert client.post("/api/setup", json={"username": "otro", "password": "12345678"}).status_code == 403
    phone = TestClient(app)
    assert phone.get("/api/me").status_code == 401
    assert phone.post("/api/login", json={"username": "jf", "password": "mal"}).status_code == 401
    assert phone.post("/api/login", json={"username": "JF", "password": "contraseña1"}).status_code == 200
    assert phone.get("/api/me").json()["username"] == "jf"


def test_password_hashing():
    h = auth.hash_password("secreto123")
    assert auth.verify_password("secreto123", h)
    assert not auth.verify_password("otro", h)
    assert not auth.verify_password("x", "garbage")


# --- discovery & subscriptions -----------------------------------------------------
def test_search_marks_subscribed_channels(env):
    client, _, _ = env
    res = client.get("/api/search", params={"q": "pizarra"}).json()
    assert [c["title"] for c in res] == ["La Pizarra", "Pizarra Clips"]
    assert not res[0]["subscribed"]
    assert client.put(f"/api/subscriptions/{LA_PIZARRA}", json={}).status_code == 200
    client.app  # same client
    res = client.get("/api/search", params={"q": "pizarra"}).json()
    assert res[0]["subscribed"]


def test_search_with_handle_opens_channel(env):
    client, _, _ = env
    res = client.get("/api/search", params={"q": "@lapizarra"}).json()
    assert res[0]["id"] == LA_PIZARRA


def test_channel_page_lists_recent_videos(env):
    client, _, _ = env
    client.get("/api/search", params={"q": "pizarra"})
    data = client.get(f"/api/channels/{LA_PIZARRA}").json()
    assert data["channel"]["title"] == "La Pizarra"
    assert data["subscription"] is None
    assert [v["id"] for v in data["videos"]][:2] == ["ep3aaaaaaaa", "clip3aaaaaa"]


def test_pick_old_episodes_by_hand_and_listen(env):
    client, app, yt = env
    client.get(f"/api/channels/{LA_PIZARRA}")
    r = client.post("/api/episodes", json={"video_ids": ["ep1aaaaaaaa", "ep2aaaaaaaa"]})
    assert r.json()["added"] == ["ep1aaaaaaaa", "ep2aaaaaaaa"]
    eps = client.get("/api/episodes").json()
    assert {e["download_status"] for e in eps} == {"queued"}
    drain(app)
    assert sorted(yt.downloads) == ["ep1aaaaaaaa", "ep2aaaaaaaa"]
    ep = client.get("/api/episodes/ep1aaaaaaaa").json()
    assert ep["download_status"] == "done" and ep["mime"] == "audio/mp4"
    audio = client.get("/api/audio/ep1aaaaaaaa", headers={"Range": "bytes=0-9"})
    assert audio.status_code == 206 and len(audio.content) == 10


# --- watching for new uploads ---------------------------------------------------------
def subscribe(client, rules=None, auto=True):
    client.get(f"/api/channels/{LA_PIZARRA}")
    r = client.put(f"/api/subscriptions/{LA_PIZARRA}", json={"auto_download": auto, "rules": rules or {}})
    assert r.status_code == 200
    # Pretend the subscription is a bit older than the new uploads published in each test.
    client.app.state.db.x("UPDATE subscriptions SET created_at=?",
                          ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))


def test_watcher_downloads_only_new_uploads_that_pass_rules(env):
    client, app, yt = env
    subscribe(client, {"min_minutes": 20})
    worker = app.state.worker
    assert worker.check_channel(LA_PIZARRA) == []  # old uploads are not downloaded automatically

    yt.publish(LA_PIZARRA, "ep4aaaaaaaa", "#4 Nuevo episodio", 4000)
    yt.publish(LA_PIZARRA, "clip4aaaaaa", "Clip del #4", 300)
    yt.publish(LA_PIZARRA, "shorta4aaaa", "Short", 40, is_short=True)
    yt.publish(LA_PIZARRA, "liveaaaaaaa", "Directo", 0, live_status="is_upcoming")
    assert worker.check_channel(LA_PIZARRA) == ["ep4aaaaaaaa"]
    drain(app)
    assert yt.downloads == ["ep4aaaaaaaa"]

    videos = {v["id"]: v for v in client.get(f"/api/channels/{LA_PIZARRA}").json()["videos"]}
    assert videos["clip4aaaaaa"]["state"] == "skipped"
    assert videos["clip4aaaaaa"]["reason"] == "Dura menos de 20 min"
    assert "shorta4aaaa" not in videos  # Shorts aren't listed at all
    assert videos["liveaaaaaaa"]["state"] is None  # premiere: looked at again next time

    # Checking again doesn't duplicate anything.
    assert worker.check_channel(LA_PIZARRA) == []


def test_watcher_ignores_channels_with_auto_download_off(env):
    client, app, yt = env
    subscribe(client, auto=False)
    yt.publish(LA_PIZARRA, "ep4aaaaaaaa", "#4", 4000)
    assert app.state.worker.check_channel(LA_PIZARRA) == []


def test_removed_episode_is_not_downloaded_again_and_file_is_pruned(env, tmp_path):
    client, app, yt = env
    subscribe(client)
    yt.publish(LA_PIZARRA, "ep4aaaaaaaa", "#4", 4000)
    app.state.worker.check_channel(LA_PIZARRA)
    drain(app)
    files = list((tmp_path / "audio").rglob("*.m4a"))
    assert len(files) == 1
    client.delete("/api/episodes/ep4aaaaaaaa")
    assert not files[0].exists()
    assert app.state.worker.check_channel(LA_PIZARRA) == []


def test_storage_and_removing_played_episodes(env, tmp_path):
    client, app, yt = env
    subscribe(client)
    yt.publish(LA_PIZARRA, "ep4aaaaaaaa", "#4", 4000)
    yt.publish(LA_PIZARRA, "ep5aaaaaaaa", "#5", 4000)
    app.state.worker.check_channel(LA_PIZARRA)
    drain(app)
    st = client.get("/api/storage").json()
    assert st["count"] == 2 and st["bytes"] > 0 and st["played_count"] == 0
    assert st["server_bytes"] == st["bytes"] and st["disk_free"] > 0
    assert all(e["filesize"] for e in client.get("/api/episodes").json())
    client.post("/api/episodes/ep4aaaaaaaa/played", json={"played": True})
    st = client.get("/api/storage").json()
    assert st["played_count"] == 1 and 0 < st["played_bytes"] < st["bytes"]
    assert client.post("/api/episodes/remove-played").json()["removed"] == ["ep4aaaaaaaa"]
    assert [e["id"] for e in client.get("/api/episodes").json()] == ["ep5aaaaaaaa"]
    assert len(list((tmp_path / "audio").rglob("*.m4a"))) == 1


# --- progress, synced between devices ------------------------------------------------
def test_progress_is_shared_between_devices_and_newest_wins(env):
    client, app, _ = env
    client.get(f"/api/channels/{LA_PIZARRA}")
    client.post("/api/episodes", json={"video_ids": ["ep1aaaaaaaa"]})
    phone = TestClient(app)
    phone.post("/api/login", json={"username": "jf", "password": "contraseña1"})

    t1 = datetime.now(timezone.utc)
    phone.post("/api/episodes/ep1aaaaaaaa/progress", json={"position": 600, "at": t1.isoformat()})
    assert client.get("/api/episodes/ep1aaaaaaaa").json()["position"] == 600
    # A late write recorded earlier on the laptop (e.g. it was offline) must not win.
    stale = (t1 - timedelta(minutes=5)).isoformat()
    client.post("/api/episodes/ep1aaaaaaaa/progress", json={"position": 30, "at": stale})
    assert phone.get("/api/episodes/ep1aaaaaaaa").json()["position"] == 600
    # Reaching the end marks it as played.
    client.post("/api/episodes/ep1aaaaaaaa/progress", json={"position": 3590})
    assert client.get("/api/episodes/ep1aaaaaaaa").json()["played"] == 1


def test_users_cannot_see_each_others_episodes(env):
    client, app, _ = env
    client.post("/api/users", json={"username": "ana", "password": "contraseña2"})
    client.get(f"/api/channels/{LA_PIZARRA}")
    client.post("/api/episodes", json={"video_ids": ["ep1aaaaaaaa"]})
    ana = TestClient(app)
    ana.post("/api/login", json={"username": "ana", "password": "contraseña2"})
    assert ana.get("/api/episodes").json() == []
    assert ana.get("/api/audio/ep1aaaaaaaa").status_code == 404
    assert ana.post("/api/users", json={"username": "x", "password": "12345678"}).status_code == 403


# --- downloads that fail ------------------------------------------------------------------
def test_failed_download_retries_then_gives_up_on_permanent_errors(env):
    client, app, yt = env
    client.get(f"/api/channels/{LA_PIZARRA}")
    yt.fail_downloads["ep1aaaaaaaa"] = "HTTP Error 429: Too Many Requests"
    client.post("/api/episodes", json={"video_ids": ["ep1aaaaaaaa"]})
    app.state.worker.download("ep1aaaaaaaa")
    d = app.state.db.download("ep1aaaaaaaa")
    assert d["status"] == "failed" and d["attempts"] == 1 and "limitando" in d["error"]
    assert app.state.db.next_download() is None  # waits for its backoff
    yt.fail_downloads["ep1aaaaaaaa"] = "ERROR: Private video"
    app.state.worker.download("ep1aaaaaaaa")
    assert app.state.db.download("ep1aaaaaaaa")["status"] == "gave_up"
    # Asking again (retry button) queues it once more.
    del yt.fail_downloads["ep1aaaaaaaa"]
    client.post("/api/episodes/ep1aaaaaaaa/retry")
    drain(app)
    assert app.state.db.download("ep1aaaaaaaa")["status"] == "done"


# --- RSS ------------------------------------------------------------------------------------
def test_private_rss_feed(env):
    client, app, _ = env
    client.get(f"/api/channels/{LA_PIZARRA}")
    client.post("/api/episodes", json={"video_ids": ["ep1aaaaaaaa"]})
    drain(app)
    rss_url = client.get("/api/me").json()["rss_url"]
    path = rss_url.split("testserver", 1)[1]
    anon = TestClient(app)
    xml = anon.get(path).text
    assert "<title>#1 Primer episodio</title>" in xml
    enclosure = xml.split('enclosure url="', 1)[1].split('"', 1)[0].split("testserver", 1)[1]
    assert anon.get(enclosure).status_code == 200
    assert anon.get("/rss/wrong.xml").status_code == 404


def test_pwa_shell_is_served(env):
    client, _, _ = env
    assert "<title>yt2pod</title>" in client.get("/").text
    assert "<title>yt2pod</title>" in client.get("/canal/UC123").text  # client-side routes
    assert client.get("/sw.js").headers["cache-control"] == "no-cache"
    assert client.get("/app.js").headers["cache-control"] == "no-cache"  # new deploys show up at once
    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/api/nope").status_code == 404


# --- pure helpers ------------------------------------------------------------------------------
def test_rules():
    long = {"title": "#5 Entrevísta larga", "duration": 3600}
    assert check(long, Rules()) is None
    assert check({"title": "x", "duration": 50}, Rules()) == "Es un Short"
    assert check({"title": "x", "duration": 50, "is_short": False}, Rules(skip_shorts=False)) is None
    assert check(long, Rules(min_minutes=90)) == "Dura menos de 90 min"
    assert check(long, Rules(max_minutes=30)) == "Dura más de 30 min"
    assert check(long, Rules(include_words=["entrevista"])) is None  # accents and case don't matter
    assert check(long, Rules(include_words=["directo"])) is not None
    assert check(long, Rules(exclude_words=["LARGA"])) == "El título contiene «LARGA»"
    assert Rules.from_dict({"min_minutes": "20", "include_words": [" a ", ""]}).include_words == ["a"]


def test_channel_refs():
    assert looks_like_channel_ref("@lapizarra")
    assert looks_like_channel_ref("https://www.youtube.com/@lapizarra/videos")
    assert looks_like_channel_ref("UC" + "x" * 22)
    assert not looks_like_channel_ref("la pizarra")


def test_parse_channel_rss():
    xml = b"""<?xml version="1.0"?><feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
      <entry><yt:videoId>abc</yt:videoId><title>Hola</title>
        <link rel="alternate" href="https://www.youtube.com/shorts/abc"/>
        <published>2026-10-01T10:00:00+00:00</published>
        <media:group><media:thumbnail url="https://i.ytimg.com/vi/abc/hqdefault.jpg"/>
        <media:description>desc</media:description></media:group></entry></feed>"""
    [v] = parse_channel_feed(xml, "UCx")
    assert v["id"] == "abc" and v["is_short"] and v["description"] == "desc"


def test_entry_to_video_defaults_thumbnail():
    v = entry_to_video({"id": "abc", "title": "t", "duration": 61.0, "upload_date": "20261001"}, "UCx")
    assert v["thumbnail"].endswith("/abc/hqdefault.jpg") and v["duration"] == 61 and v["published"]


def test_migrations_are_idempotent(tmp_path):
    DB(tmp_path / "x.db").close()
    db = DB(tmp_path / "x.db")
    assert db.one("PRAGMA user_version")["user_version"] == 1


def test_uploading_cookies_retries_blocked_downloads(env, tmp_path):
    client, app, yt = env
    client.get(f"/api/channels/{LA_PIZARRA}")
    yt.fail_downloads["ep1aaaaaaaa"] = "ERROR: Sign in to confirm you're not a bot"
    client.post("/api/episodes", json={"video_ids": ["ep1aaaaaaaa"]})
    app.state.worker.download("ep1aaaaaaaa")
    assert "Ajustes" in app.state.db.download("ep1aaaaaaaa")["error"]
    assert client.get("/api/cookies").json()["present"] is False

    assert client.put("/api/cookies", json={"content": "hola"}).status_code == 400
    cookies = ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tabc\n"
    r = client.put("/api/cookies", json={"content": cookies}).json()
    assert r["present"] and r["retried"] == 1
    saved = (tmp_path / "cookies.txt").read_text()
    assert saved.startswith("# Netscape") and "SID" in saved
    assert app.state.db.next_download()["video_id"] == "ep1aaaaaaaa"
    assert client.put("/api/cookies", json={"content": cookies}).json()["retried"] == 0

    ana = TestClient(app)
    client.post("/api/users", json={"username": "ana", "password": "contraseña2"})
    ana.post("/api/login", json={"username": "ana", "password": "contraseña2"})
    assert ana.put("/api/cookies", json={"content": cookies}).status_code == 403
    assert client.delete("/api/cookies").json()["present"] is False


def test_youtube_reads_cookie_file_lazily(tmp_path):
    from yt2pod.youtube import YouTube
    yt = YouTube(tmp_path / "cookies.txt")
    assert yt.cookies() is None
    (tmp_path / "cookies.txt").write_text("# Netscape HTTP Cookie File\n")
    assert yt.cookies() == str(tmp_path / "cookies.txt")
