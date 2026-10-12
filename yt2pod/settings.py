"""Runtime settings, read from environment variables (the natural fit for Docker)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

AUDIO_FORMATS = ("m4a", "best", "opus", "mp3")


def _duration(text: str, default: int) -> int:
    """'30m', '1h', '90s' or plain seconds."""
    text = (text or "").strip().lower()
    if not text:
        return default
    units = {"s": 1, "m": 60, "h": 3600}
    if text[-1] in units:
        return int(float(text[:-1]) * units[text[-1]])
    return int(text)


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path("data"))
    host: str = "0.0.0.0"
    port: int = 8765
    # Public URL used in RSS feeds (e.g. https://yt2pod.tu-tailnet.ts.net). Optional for the app itself.
    base_url: str = ""
    check_interval: int = 30 * 60
    audio_format: str = "m4a"
    cookies_file: str | None = None
    sponsorblock: list[str] = field(default_factory=list)
    # Don't let anyone create an account after the first one unless an admin does it.
    open_signup: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "yt2pod.sqlite3"

    @property
    def cookies_path(self) -> Path:
        """YouTube cookies: the file named in YT2POD_COOKIES_FILE, else the one uploaded from the app."""
        return Path(self.cookies_file) if self.cookies_file else self.data_dir / "cookies.txt"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        fmt = e.get("YT2POD_AUDIO_FORMAT", "m4a").lower()
        if fmt not in AUDIO_FORMATS:
            raise ValueError(f"YT2POD_AUDIO_FORMAT must be one of {', '.join(AUDIO_FORMATS)}")
        return cls(
            data_dir=Path(e.get("YT2POD_DATA_DIR", "data")),
            host=e.get("YT2POD_HOST", "0.0.0.0"),
            port=int(e.get("YT2POD_PORT", "8765")),
            base_url=e.get("YT2POD_BASE_URL", "").rstrip("/"),
            check_interval=max(300, _duration(e.get("YT2POD_CHECK_INTERVAL", ""), 30 * 60)),
            audio_format=fmt,
            cookies_file=e.get("YT2POD_COOKIES_FILE") or None,
            sponsorblock=[c for c in e.get("YT2POD_SPONSORBLOCK", "").replace(" ", "").split(",") if c],
            open_signup=e.get("YT2POD_OPEN_SIGNUP", "").lower() in ("1", "true", "yes"),
        )
