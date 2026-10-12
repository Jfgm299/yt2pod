"""Per-subscription rules that decide which new uploads are downloaded automatically.

Deliberately plain: a duration range and some words, no regular expressions.
"""

from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass, field


def _fold(text: str) -> str:
    """Lowercase and strip accents so 'Entrevista' matches 'entrevísta'."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


@dataclass
class Rules:
    min_minutes: int = 0              # 0 = no minimum
    max_minutes: int = 0              # 0 = no maximum
    include_words: list[str] = field(default_factory=list)  # title must contain one of these
    exclude_words: list[str] = field(default_factory=list)  # title must contain none of these
    skip_shorts: bool = True

    @classmethod
    def from_dict(cls, d: dict | None) -> "Rules":
        d = d or {}

        def words(v) -> list[str]:
            return [w.strip() for w in (v or []) if isinstance(w, str) and w.strip()][:20]

        return cls(
            min_minutes=max(0, int(d.get("min_minutes") or 0)),
            max_minutes=max(0, int(d.get("max_minutes") or 0)),
            include_words=words(d.get("include_words")),
            exclude_words=words(d.get("exclude_words")),
            skip_shorts=bool(d.get("skip_shorts", True)),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def needs_duration(self) -> bool:
        return bool(self.min_minutes or self.max_minutes or self.skip_shorts)


def check(video: dict, rules: Rules) -> str | None:
    """None if the video passes, otherwise a short human reason (Spanish, shown in the UI)."""
    if rules.skip_shorts and video.get("is_short"):
        return "Es un Short"
    duration = video.get("duration")
    if duration:
        if rules.skip_shorts and duration <= 60:
            return "Es un Short"
        if rules.min_minutes and duration < rules.min_minutes * 60:
            return f"Dura menos de {rules.min_minutes} min"
        if rules.max_minutes and duration > rules.max_minutes * 60:
            return f"Dura más de {rules.max_minutes} min"
    title = _fold(video.get("title") or "")
    if rules.include_words and not any(_fold(w) in title for w in rules.include_words):
        return "El título no contiene ninguna de tus palabras"
    for w in rules.exclude_words:
        if _fold(w) in title:
            return f"El título contiene «{w}»"
    return None


def fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "?"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
