"""Технические примитивы дедупликации."""

from __future__ import annotations

import calendar
import hashlib
import re
from datetime import datetime

from app.ingestion.keywords import STOPWORDS, normalize_text

TOKEN_RE = re.compile(r"[\w-]{3,}", re.UNICODE)

# Простое отсечение падежных окончаний. Полноценный stemmer здесь не нужен:
# задача — склеить перепечатки одного сообщения, а не анализировать морфологию.
ENDINGS = tuple(
    sorted(
        (
            "иями", "ями", "ами", "ов", "ев", "ой", "ей", "ая", "ые", "ых",
            "ий", "ия", "ии", "ах", "ям", "ом", "ем", "ую", "юю", "ь",
            "е", "а", "ы", "и", "у", "ю", "о",
        ),
        key=len,
        reverse=True,
    )
)


def _stem(token: str) -> str:
    for ending in ENDINGS:
        if token.endswith(ending) and len(token) - len(ending) >= 4:
            return token[: -len(ending)]
    return token


def tokenize(text: str) -> set[str]:
    """Множество значимых токенов для Jaccard-сходства."""
    return {
        _stem(token)
        for token in TOKEN_RE.findall(normalize_text(text))
        if token not in STOPWORDS and not token.isdigit()
    }


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    return intersection / union if union else 0.0


def build_content_hash(norm_text: str, region_code: str, threat_type: str, status: str) -> str:
    payload = f"{region_code}|{threat_type}|{status}|{norm_text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def bucket_for(event_at: datetime, window_minutes: int) -> int:
    """Номер временного окна. Naive datetime трактуется как UTC."""
    window_seconds = max(1, window_minutes) * 60
    if event_at.tzinfo is None:
        seconds = calendar.timegm(event_at.timetuple())
    else:
        seconds = int(event_at.timestamp())
    return seconds // window_seconds
