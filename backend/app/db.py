"""Асинхронное подключение к БД и базовый класс моделей."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Общий declarative base для всех ORM-моделей."""


def _engine_kwargs(url: str) -> dict:
    """Параметры пула: SQLite не поддерживает pool_size/max_overflow.

    В serverless каждый вызов функции живёт отдельно, поэтому держать пул
    соединений нельзя: он утекает вместе с остановленным инстансом.
    NullPool открывает соединение на запрос, а statement_cache_size=0 нужен
    для PgBouncer в transaction-режиме (Neon pooler).
    """
    if url.startswith("sqlite"):
        # timeout — ждать освобождения блокировки, пока воркер пишет данные.
        return {"connect_args": {"check_same_thread": False, "timeout": 30}}

    if settings.serverless or "-pooler" in url:
        return {
            "pool_pre_ping": True,
            "poolclass": NullPool,
            "connect_args": {"statement_cache_size": 0, "timeout": 15},
        }

    return {"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20}


engine = create_async_engine(settings.database_url, future=True, **_engine_kwargs(settings.database_url))

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-зависимость: сессия на один HTTP-запрос."""
    async with SessionLocal() as session:
        yield session


def _migrate(connection) -> None:
    """Простые аддитивные миграции для уже существующих баз.

    Полноценный Alembic здесь избыточен: приложение локальное, а база
    может быть создана предыдущей версией. Добавляем только новые колонки.
    """
    inspector = inspect(connection)
    if "alerts" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("alerts")}
    if "places" not in columns:
        connection.execute(text("ALTER TABLE alerts ADD COLUMN places JSON"))
        logger.info("Миграция: в alerts добавлена колонка places")


async def init_db() -> None:
    """Создаёт таблицы. Для production используйте Alembic."""
    from app import models  # noqa: F401  (нужно для регистрации metadata)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate)

