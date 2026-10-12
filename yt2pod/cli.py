"""Command line: `yt2pod serve` runs everything; the rest are admin helpers."""

from __future__ import annotations

import argparse
import getpass
import logging
import shutil
import sys

from . import __version__, auth
from .db import DB
from .settings import Settings


def _password(prompt: str = "Contraseña: ") -> str:
    first = getpass.getpass(prompt)
    if getpass.getpass("Repite la contraseña: ") != first:
        raise SystemExit("Las contraseñas no coinciden")
    return first


def cmd_serve(settings: Settings, args) -> int:
    import uvicorn

    from .api import create_app

    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    print(f"yt2pod {__version__} en http://{settings.host}:{settings.port}  (datos en {settings.data_dir.resolve()})")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning",
                proxy_headers=True, forwarded_allow_ips="*")
    return 0


def cmd_user(settings: Settings, args) -> int:
    db = DB(settings.db_path)
    if args.action == "list":
        for u in db.q("SELECT username, is_admin, created_at FROM users ORDER BY id"):
            print(f"{u['username']}{'  (admin)' if u['is_admin'] else ''}  desde {u['created_at'][:10]}")
        return 0
    if not args.username:
        raise SystemExit("Indica el nombre de usuario")
    if args.action == "add":
        auth.create_user(db, args.username, _password(), is_admin=args.admin or db.user_count() == 0)
        print(f"Cuenta {args.username} creada")
    elif args.action == "passwd":
        user = db.user_by_name(args.username)
        if not user:
            raise SystemExit("No existe ese usuario")
        auth.set_password(db, user["id"], _password("Nueva contraseña: "))
        print("Contraseña cambiada; se han cerrado sus sesiones abiertas")
    return 0


def cmd_doctor(settings: Settings, args) -> int:
    """Check the things that usually break: ffmpeg, yt-dlp, and whether YouTube lets us in."""
    from .youtube import YouTube, YouTubeError

    ok = True

    def line(good: bool, text: str) -> None:
        nonlocal ok
        ok &= good
        print(("✔ " if good else "✘ ") + text)

    line(bool(shutil.which("ffmpeg")), "ffmpeg instalado" if shutil.which("ffmpeg") else "falta ffmpeg")
    import yt_dlp

    line(True, f"yt-dlp {yt_dlp.version.__version__} (si falla algo: pip install -U yt-dlp)")
    yt = YouTube(settings.cookies_file)
    channel = args.channel or "UCBR8-60-B28hp2BmDPdntcQ"  # YouTube's own channel
    try:
        entries = yt.channel_feed(channel)
        line(True, f"RSS del canal accesible ({len(entries)} vídeos)")
    except YouTubeError as e:
        line(False, f"RSS del canal: {e}")
        entries = []
    if entries:
        try:
            info = yt.video_info(entries[0]["id"])
            line(True, f"yt-dlp puede leer vídeos («{info['title'][:50]}»)")
        except YouTubeError as e:
            line(False, f"yt-dlp no puede leer vídeos: {e}")
            if "bot" in str(e).lower():
                print("  YouTube bloquea esta IP. Usa cookies (YT2POD_COOKIES_FILE) o ejecuta yt2pod en casa.")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="yt2pod", description="Sigue canales de YouTube y escúchalos como podcasts.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="Servidor web + vigilancia de canales + descargas (por defecto)")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    u = sub.add_parser("user", help="Gestionar cuentas")
    u.add_argument("action", choices=["add", "passwd", "list"])
    u.add_argument("username", nargs="?")
    u.add_argument("--admin", action="store_true")
    d = sub.add_parser("doctor", help="Comprobar ffmpeg, yt-dlp y el acceso a YouTube")
    d.add_argument("channel", nargs="?", help="ID de canal (UC…) para probar")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = Settings.from_env()
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    cmd = args.cmd or "serve"
    if cmd == "serve" and not hasattr(args, "host"):
        args.host, args.port = None, None
    try:
        return {"serve": cmd_serve, "user": cmd_user, "doctor": cmd_doctor}[cmd](settings, args)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
