"""Работа со временем.

Внутри БД храним naive UTC (переносимо между PostgreSQL и SQLite),
наружу в API отдаём timezone-aware ISO 8601.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Текущее UTC-время без tzinfo для хранения в БД."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: datetime | None) -> datetime | None:
    """Приводит aware datetime к naive UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def to_utc(value: datetime | None) -> datetime | None:
    """Помечает naive UTC как timezone-aware для ответа API."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
