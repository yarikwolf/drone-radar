"""Защита endpoint опроса, который дёргает внешний планировщик."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api import internal
from app.config import settings


async def test_poll_disabled_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "poll_token", "")
    with pytest.raises(HTTPException) as exc:
        await internal.trigger_poll("any")
    assert exc.value.status_code == 503


async def test_poll_rejects_wrong_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "poll_token", "secret")
    with pytest.raises(HTTPException) as exc:
        await internal.trigger_poll(None)
    assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as exc:
        await internal.trigger_poll("wrong")
    assert exc.value.status_code == 401


async def test_poll_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "poll_token", "secret")

    async def fake_run() -> dict[str, object]:
        return {"ok": True, "stored": 3, "skipped": False, "elapsed_seconds": 0.1}

    monkeypatch.setattr(internal, "run_poll", fake_run)

    result = await internal.trigger_poll("secret")
    assert result.ok is True
    assert result.stored == 3
    assert result.skipped is False


async def test_poll_handles_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "poll_token", "secret")

    async def broken_run() -> dict[str, object]:
        raise RuntimeError("Telegram недоступен")

    monkeypatch.setattr(internal, "run_poll", broken_run)

    with pytest.raises(HTTPException) as exc:
        await internal.trigger_poll("secret")
    assert exc.value.status_code == 502


async def test_setup_cron_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Установка задания pg_cron закрыта тем же токеном, что и опрос."""
    monkeypatch.setattr(settings, "poll_token", "")
    with pytest.raises(HTTPException) as exc:
        await internal.setup_cron(None, "any")  # type: ignore[arg-type]
    assert exc.value.status_code == 503

    monkeypatch.setattr(settings, "poll_token", "secret")
    with pytest.raises(HTTPException) as exc:
        await internal.setup_cron(None, None)  # type: ignore[arg-type]
    assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as exc:
        await internal.setup_cron(None, "wrong")  # type: ignore[arg-type]
    assert exc.value.status_code == 401
