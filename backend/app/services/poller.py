"""Один цикл опроса источников.

Используется CLI-скриптом (`scripts/poll_sources.py`), контейнером `poller`
и внешним планировщиком на serverless-хостинге (Vercel): endpoint
`POST /api/internal/poll`.

Опрос не должен выполняться параллельно: два одновременных запуска дают
лишние подключения к Telegram и дубли в базе. В PostgreSQL берём advisory
lock (работает между инстансами), в остальных режимах — обычный asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import text

from app.config import settings
from app.db import engine

logger = logging.getLogger(__name__)

# Произвольное постоянное число: одинаковое у всех инстансов приложения.
LOCK_KEY = 918_273_645
_local_lock = asyncio.Lock()


async def _run_with_db_lock() -> int | None:
    """Запускает опрос под advisory lock. None — опрос уже идёт."""
    from app.ingestion.telegram_listener import poll_once

    async with engine.connect() as conn:
        locked = await conn.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}
        )
        if not locked:
            return None
        try:
            return await poll_once()
        finally:
            await conn.scalar(
                text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY}
            )


async def run_poll() -> dict[str, object]:
    """Выполняет один опрос и возвращает сводку для планировщика."""
    started = time.monotonic()
    skipped = False

    if settings.database_url.startswith("postgresql"):
        stored = await _run_with_db_lock()
        if stored is None:
            skipped = True
            stored = 0
    else:
        async with _local_lock:
            from app.ingestion.telegram_listener import poll_once

            stored = await poll_once()

    elapsed = round(time.monotonic() - started, 2)
    if skipped:
        logger.info("Опрос пропущен: предыдущий ещё выполняется (%.2f c)", elapsed)
    else:
        logger.info("Опрос завершён: сообщений %s за %.2f c", stored, elapsed)

    return {
        "ok": not skipped,
        "stored": stored,
        "skipped": skipped,
        "elapsed_seconds": elapsed,
    }
