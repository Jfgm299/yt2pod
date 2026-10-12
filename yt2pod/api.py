"""HTTP API + the PWA's static files, in one process with the background worker."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__, auth
from .db import DB
from .feed import build_rss
from .filters import Rules
from .settings import Settings
from .worker import Worker
from .youtube import CHANNEL_ID_RE, YouTube, YouTubeError, looks_like_channel_ref

log = logging.getLogger(__name__)
WEB = Path(__file__).parent / "web"
CHANNEL_REFRESH = timedelta(hours=1)


# --- request bodies -------------------------------------------------------------
class Credentials(BaseModel):
    username: str = Field(max_length=40)
    password: str = Field(max_length=200)


class NewPassword(BaseModel):
    current: str
    password: str = Field(max_length=200)


class SubscriptionIn(BaseModel):
    auto_download: bool = True
    rules: dict = Field(default_factory=dict)


class EpisodesIn(BaseModel):
    video_ids: list[str] = Field(max_length=100)


class ProgressIn(BaseModel):
    position: float = Field(ge=0)
    played: bool | None = None
    at: str | None = None  # client clock (ISO 8601) of when this position was recorded


class CookiesIn(BaseModel):
    content: str = Field(max_length=2_000_000)


class PlayedIn(BaseModel):
    played: bool


# --- app ----------------------------------------------------------------------------
def create_app(settings: Settings | None = None, yt: YouTube | None = None, start_worker: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()
    db = DB(settings.db_path)
    yt = yt or YouTube(settings.cookies_path)
    worker = Worker(settings, db, yt)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="yt2pod", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.db, app.state.worker, app.state.settings = db, worker, settings
    search_cache: dict[str, tuple[float, list[dict]]] = {}
    failed_logins: dict[str, list[float]] = {}
    refresh_locks: dict[str, threading.Lock] = {}

    # --- auth helpers ---------------------------------------------------------------
    def current_user(request: Request) -> dict:
        user = auth.session_user(db, request.cookies.get(auth.SESSION_COOKIE))
        if not user:
            raise HTTPException(401, "Inicia sesión")
        return user

    def admin_user(user: dict = Depends(current_user)) -> dict:
        if not user["is_admin"]:
            raise HTTPException(403, "Solo para administradores")
        return user

    def set_session(request: Request, response: Response, token: str) -> None:
        response.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                            samesite="lax", secure=request.url.scheme == "https", path="/")

    def public_user(u: dict) -> dict:
        return {"id": u["id"], "username": u["username"], "is_admin": bool(u["is_admin"])}

    def base_url(request: Request) -> str:
        return settings.base_url or str(request.base_url).rstrip("/")

    def throttle(request: Request) -> None:
        ip = request.client.host if request.client else "?"
        recent = [t for t in failed_logins.get(ip, []) if t > time.time() - 600]
        failed_logins[ip] = recent
        if len(recent) >= 10:
            raise HTTPException(429, "Demasiados intentos. Espera unos minutos.")

    # --- session --------------------------------------------------------------------
    @app.get("/api/session")
    def session(request: Request):
        user = auth.session_user(db, request.cookies.get(auth.SESSION_COOKIE))
        return {"user": public_user(user) if user else None, "needs_setup": db.user_count() == 0,
                "version": __version__}

    @app.post("/api/setup")
    def setup(body: Credentials, request: Request, response: Response):
        if db.user_count() > 0 and not settings.open_signup:
            raise HTTPException(403, "Ya hay una cuenta creada")
        try:
            auth.create_user(db, body.username, body.password, is_admin=db.user_count() == 0)
        except ValueError as e:
            raise HTTPException(400, str(e))
        token = auth.login(db, body.username, body.password, request.headers.get("user-agent", ""))
        set_session(request, response, token)
        return {"user": public_user(db.user_by_name(body.username))}

    @app.post("/api/login")
    def do_login(body: Credentials, request: Request, response: Response):
        throttle(request)
        token = auth.login(db, body.username, body.password, request.headers.get("user-agent", ""))
        if not token:
            failed_logins.setdefault(request.client.host if request.client else "?", []).append(time.time())
            raise HTTPException(401, "Usuario o contraseña incorrectos")
        set_session(request, response, token)
        return {"user": public_user(db.user_by_name(body.username))}

    @app.post("/api/logout")
    def do_logout(request: Request, response: Response):
        auth.logout(db, request.cookies.get(auth.SESSION_COOKIE))
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request, user: dict = Depends(current_user)):
        return {**public_user(user), "rss_url": f"{base_url(request)}/rss/{user['rss_token']}.xml",
                "cookies": settings.cookies_path.is_file(),
                "audio_format": settings.audio_format, "check_interval": settings.check_interval,
                "watcher": worker.watch_status}

    @app.post("/api/me/password")
    def change_password(body: NewPassword, request: Request, response: Response,
                        user: dict = Depends(current_user)):
        if not auth.verify_password(body.current, user["password_hash"]):
            raise HTTPException(400, "La contraseña actual no es correcta")
        try:
            auth.set_password(db, user["id"], body.password)
        except ValueError as e:
            raise HTTPException(400, str(e))
        set_session(request, response, auth.login(db, user["username"], body.password,
                                                  request.headers.get("user-agent", "")))
        return {"ok": True}

    @app.get("/api/users")
    def users(admin: dict = Depends(admin_user)):
        return [public_user(u) for u in db.q("SELECT * FROM users ORDER BY id")]

    @app.post("/api/users")
    def add_user(body: Credentials, admin: dict = Depends(admin_user)):
        try:
            uid = auth.create_user(db, body.username, body.password)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return public_user(db.one("SELECT * FROM users WHERE id=?", (uid,)))

    # --- YouTube cookies (for servers YouTube treats as bots) ---------------------
    def cookies_status() -> dict:
        path = settings.cookies_path
        if not path.is_file():
            return {"present": False, "from_env": bool(settings.cookies_file)}
        mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        return {"present": True, "updated_at": mtime, "from_env": bool(settings.cookies_file)}

    @app.get("/api/cookies")
    def get_cookies(admin: dict = Depends(admin_user)):
        return cookies_status()

    @app.put("/api/cookies")
    def put_cookies(body: CookiesIn, admin: dict = Depends(admin_user)):
        text = body.content.replace("\r\n", "\n")
        rows = [ln.split("\t") for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
        if not any(len(r) >= 7 and "youtube.com" in r[0] for r in rows):
            raise HTTPException(400, "No parece un cookies.txt de YouTube. Expórtalo en formato Netscape "
                                     "estando en youtube.com con la sesión iniciada.")
        if not text.startswith("# Netscape") and not text.startswith("# HTTP Cookie File"):
            text = "# Netscape HTTP Cookie File\n" + text  # yt-dlp requires the header
        path = settings.cookies_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o600)
        retried = db.retry_blocked_downloads()
        worker.kick_downloads()
        return {**cookies_status(), "retried": retried}

    @app.delete("/api/cookies")
    def delete_cookies(admin: dict = Depends(admin_user)):
        settings.cookies_path.unlink(missing_ok=True)
        return cookies_status()

    # --- discovery ----------------------------------------------------------------
    def with_subscribed(user: dict, channels: list[dict]) -> list[dict]:
        subs = {s["channel_id"] for s in db.q("SELECT channel_id FROM subscriptions WHERE user_id=?",
                                              (user["id"],))}
        return [{**c, "subscribed": c["id"] in subs} for c in channels]

    @app.get("/api/search")
    def search(q: str, user: dict = Depends(current_user)):
        q = q.strip()
        if len(q) < 2:
            return []
        key = q.casefold()
        hit = search_cache.get(key)
        if hit and hit[0] > time.time() - 600:
            return with_subscribed(user, hit[1])
        try:
            if looks_like_channel_ref(q):
                ch, videos = yt.channel(q, 30)
                db.upsert_channel(ch)
                for v in videos:
                    db.upsert_video(v)
                db.mark_listed(ch["id"])
                results = [ch]
            else:
                results = yt.search_channels(q)
        except YouTubeError as e:
            raise HTTPException(502, f"YouTube no respondió: {e}")
        for ch in results:
            db.upsert_channel(ch)
        search_cache[key] = (time.time(), results)
        return with_subscribed(user, results)

    def refresh_channel(channel_id: str, force: bool = False) -> None:
        ch = db.channel(channel_id)
        listed = ch and ch["listed_at"]
        fresh = listed and datetime.fromisoformat(listed) > datetime.now(timezone.utc) - CHANNEL_REFRESH
        if fresh and not force:
            return
        lock = refresh_locks.setdefault(channel_id, threading.Lock())
        with lock:
            info, videos = yt.channel(channel_id, 40)
            db.upsert_channel(info)
            for v in videos:
                db.upsert_video(v)
            db.mark_listed(channel_id)

    @app.get("/api/channels/{channel_id}")
    def channel(channel_id: str, refresh: bool = False, user: dict = Depends(current_user)):
        if not CHANNEL_ID_RE.match(channel_id):
            raise HTTPException(404, "Canal no válido")
        try:
            refresh_channel(channel_id, force=refresh)
        except YouTubeError as e:
            if not db.channel(channel_id):
                raise HTTPException(502, f"YouTube no respondió: {e}")
        ch = db.channel(channel_id)
        sub = db.subscription(user["id"], channel_id)
        return {"channel": ch, "subscription": sub,
                "videos": [v for v in db.channel_videos(user["id"], channel_id) if not v["is_short"]]}

    # --- subscriptions ------------------------------------------------------------
    @app.get("/api/subscriptions")
    def subscriptions(user: dict = Depends(current_user)):
        return db.subscriptions(user["id"])

    @app.put("/api/subscriptions/{channel_id}")
    def subscribe(channel_id: str, body: SubscriptionIn, user: dict = Depends(current_user)):
        if not db.channel(channel_id):
            raise HTTPException(404, "Canal desconocido; búscalo primero")
        db.subscribe(user["id"], channel_id, body.auto_download, Rules.from_dict(body.rules).to_dict())
        return db.subscription(user["id"], channel_id)

    @app.delete("/api/subscriptions/{channel_id}")
    def unsubscribe(channel_id: str, user: dict = Depends(current_user)):
        db.unsubscribe(user["id"], channel_id)
        return {"ok": True}

    # --- episodes -----------------------------------------------------------------
    @app.get("/api/episodes")
    def episodes(channel: str | None = None, user: dict = Depends(current_user)):
        return db.episodes(user["id"], channel_id=channel)

    @app.post("/api/episodes")
    def add_episodes(body: EpisodesIn, user: dict = Depends(current_user)):
        added = []
        for vid in body.video_ids:
            if db.video(vid):
                db.add_to_library(user["id"], vid)
                db.enqueue(vid)
                added.append(vid)
        worker.kick_downloads()
        return {"added": added}

    def owned(user: dict, video_id: str) -> dict:
        ep = db.episode(user["id"], video_id)
        if not ep:
            raise HTTPException(404, "No tienes este episodio")
        return ep

    @app.get("/api/episodes/{video_id}")
    def episode(video_id: str, user: dict = Depends(current_user)):
        return owned(user, video_id)

    @app.get("/api/storage")
    def storage(user: dict = Depends(current_user)):
        out = db.storage(user["id"])
        if user["is_admin"]:
            settings.audio_dir.mkdir(parents=True, exist_ok=True)
            out["server_bytes"] = db.server_storage()
            out["disk_free"] = shutil.disk_usage(settings.audio_dir).free
        return out

    @app.post("/api/episodes/remove-played")
    def remove_played(user: dict = Depends(current_user)):
        ids = db.played_episode_ids(user["id"])
        for vid in ids:
            db.remove_from_library(user["id"], vid)
        worker.prune()
        return {"removed": ids}

    @app.delete("/api/episodes/{video_id}")
    def remove_episode(video_id: str, user: dict = Depends(current_user)):
        owned(user, video_id)
        db.remove_from_library(user["id"], video_id)
        worker.prune()
        return {"ok": True}

    @app.post("/api/episodes/{video_id}/progress")
    def progress(video_id: str, body: ProgressIn, user: dict = Depends(current_user)):
        ep = owned(user, video_id)
        played = body.played
        if played is None and ep["duration"] and body.position >= ep["duration"] - 30:
            played = True
        db.set_progress(user["id"], video_id, body.position, played, body.at)
        return {"ok": True}

    @app.post("/api/episodes/{video_id}/played")
    def mark_played(video_id: str, body: PlayedIn, user: dict = Depends(current_user)):
        owned(user, video_id)
        db.set_progress(user["id"], video_id, 0, body.played)
        return {"ok": True}

    @app.post("/api/episodes/{video_id}/retry")
    def retry(video_id: str, user: dict = Depends(current_user)):
        owned(user, video_id)
        db.enqueue(video_id)
        worker.kick_downloads()
        return {"ok": True}

    @app.get("/api/activity")
    def activity(user: dict = Depends(current_user)):
        """Cheap endpoint the app polls: download state of the user's pending episodes."""
        rows = db.q("""SELECT d.video_id, d.status, d.progress, d.error FROM downloads d
                       JOIN library l ON l.video_id=d.video_id AND l.user_id=? AND l.state='active'
                       WHERE d.status!='done' OR d.updated_at>?""",
                    (user["id"], (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(timespec="seconds")))
        return {"downloads": rows, "watcher": worker.watch_status}

    @app.post("/api/check")
    def check_now(user: dict = Depends(current_user)):
        worker.check_now()
        return {"ok": True}

    @app.get("/api/audio/{video_id}")
    def audio(video_id: str, user: dict = Depends(current_user)):
        owned(user, video_id)
        d = db.download(video_id)
        path = worker.audio_path(d)
        if not path:
            raise HTTPException(404, "El audio aún no está descargado")
        return FileResponse(path, media_type=d["mime"], headers={"Cache-Control": "private, max-age=604800"})

    # --- RSS for podcast apps -----------------------------------------------------
    def rss_user(token: str) -> dict:
        user = db.user_by_rss_token(token)
        if not user:
            raise HTTPException(404)
        return user

    def rss_response(request: Request, user: dict, channel_id: str | None) -> Response:
        base = base_url(request)
        eps = db.episodes(user["id"], channel_id=channel_id)
        ch = db.channel(channel_id) if channel_id else None
        if channel_id and not ch:
            raise HTTPException(404)
        xml = build_rss(
            title=ch["title"] if ch else f"yt2pod · {user['username']}",
            description=(ch or {}).get("description") or "Episodios descargados por yt2pod",
            link=(ch or {}).get("url") or base, image=(ch or {}).get("avatar"),
            self_url=str(request.url), episodes=eps,
            audio_url=lambda ep: f"{base}/rss/{user['rss_token']}/audio/{ep['id']}{Path(ep['filename']).suffix}",
        )
        return Response(xml, media_type="application/rss+xml; charset=utf-8")

    @app.get("/rss/{token}.xml")
    def rss_all(token: str, request: Request):
        return rss_response(request, rss_user(token), None)

    @app.get("/rss/{token}/{channel_id}.xml")
    def rss_channel(token: str, channel_id: str, request: Request):
        return rss_response(request, rss_user(token), channel_id)

    @app.get("/rss/{token}/audio/{name}")
    def rss_audio(token: str, name: str):
        user = rss_user(token)
        video_id = name.rsplit(".", 1)[0]
        if not db.episode(user["id"], video_id):
            raise HTTPException(404)
        d = db.download(video_id)
        path = worker.audio_path(d)
        if not path:
            raise HTTPException(404)
        return FileResponse(path, media_type=d["mime"])

    # --- the PWA --------------------------------------------------------------------
    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__}

    # Every load asks for the app code under a URL that changes with its content, so no browser or
    # service worker can keep running an old copy after a deploy.
    stamp = hashlib.sha256(b"".join(f.read_bytes() for f in sorted(WEB.rglob("*")) if f.is_file())).hexdigest()[:10]
    stamped = re.compile(r'"(/(?:app|lib|store|i18n)\.js|/styles\.css|/vendor/htm-preact\.js)"')

    def stamp_urls(file: Path) -> Response:
        body = stamped.sub(lambda m: f'"{m.group(1)}?v={stamp}"', file.read_text(encoding="utf-8"))
        media = "text/html" if file.suffix == ".html" else "text/javascript"
        return Response(body, media_type=f"{media}; charset=utf-8", headers={"Cache-Control": "no-cache"})

    @app.get("/{path:path}")
    def static(path: str):
        if path.startswith(("api/", "rss/")):
            raise HTTPException(404)
        file = (WEB / path).resolve()
        if path and WEB.resolve() in file.parents and file.is_file():
            # App code is revalidated on every load (cheap 304s via ETag) so a new deploy shows up at
            # once; only icons and the vendored library, which never change in place, are cached.
            immutable = path.startswith(("icons/", "vendor/"))
            headers = {"Cache-Control": "public, max-age=86400" if immutable else "no-cache"}
            if file.name == "sw.js":
                headers["Service-Worker-Allowed"] = "/"
            if path in ("app.js", "lib.js", "store.js", "index.html"):
                return stamp_urls(file)
            return FileResponse(file, headers=headers)
        return stamp_urls(WEB / "index.html")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app
