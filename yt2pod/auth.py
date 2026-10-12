"""Passwords and login sessions (standard library only)."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from .db import DB, now

SESSION_COOKIE = "yt2pod_session"
SESSION_DAYS = 400  # browsers cap cookie lifetime around here; we refresh it on use


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(db: DB, username: str, password: str, is_admin: bool = False) -> int:
    username = username.strip()
    if not (2 <= len(username) <= 40):
        raise ValueError("El nombre de usuario debe tener entre 2 y 40 caracteres")
    if len(password) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres")
    if db.user_by_name(username):
        raise ValueError("Ese nombre de usuario ya existe")
    return db.create_user(username, hash_password(password), secrets.token_urlsafe(18), is_admin)


def set_password(db: DB, user_id: int, password: str) -> None:
    if len(password) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres")
    db.x("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), user_id))
    db.x("DELETE FROM sessions WHERE user_id=?", (user_id,))


def login(db: DB, username: str, password: str, device: str = "") -> str | None:
    user = db.user_by_name(username.strip())
    if not user or not verify_password(password, user["password_hash"]):
        return None
    token = secrets.token_urlsafe(32)
    db.x("INSERT INTO sessions (token_hash, user_id, device, created_at, last_seen) VALUES (?, ?, ?, ?, ?)",
         (_hash_token(token), user["id"], device[:200], now(), now()))
    return token


def session_user(db: DB, token: str | None) -> dict | None:
    if not token:
        return None
    row = db.one("""SELECT u.*, s.token_hash, s.last_seen FROM sessions s JOIN users u ON u.id=s.user_id
                    WHERE s.token_hash=?""", (_hash_token(token),))
    if row and (row["last_seen"] or "")[:10] != now()[:10]:
        db.x("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now(), row["token_hash"]))
    return row


def logout(db: DB, token: str | None) -> None:
    if token:
        db.x("DELETE FROM sessions WHERE token_hash=?", (_hash_token(token),))
