"""Экспорт уже авторизованной файловой сессии Telethon в строку.

Нужно для serverless-хостинга (Vercel): там нельзя положить файл сессии,
зато можно передать TELEGRAM_SESSION_STRING через переменные окружения.

Запуск из backend/ (лаунчер можно не останавливать):

    python scripts/session_to_string.py

Скрипт использует тот же файл, что и локальный радар (TELEGRAM_SESSION,
по умолчанию data/radar), и печатает строку для .env / Vercel.
Строка даёт полный доступ к аккаунту — храните её как пароль.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from telethon import TelegramClient

from app.config import settings
from app.ingestion.telegram_listener import build_proxy


async def main() -> None:
    if not settings.telegram_configured:
        raise SystemExit("Задайте TELEGRAM_API_ID и TELEGRAM_API_HASH")

    path = Path(settings.telegram_session)
    if not path.exists():
        raise SystemExit(
            f"Файл сессии не найден: {path}. Сначала выполните scripts/telegram_login.py"
        )

    connection, proxy = build_proxy()
    async with TelegramClient(
        str(path),
        settings.telegram_api_id,
        settings.telegram_api_hash,
        connection=connection,
        proxy=proxy,
    ) as client:
        if not await client.is_user_authorized():
            raise SystemExit(
                "Сессия не авторизована. Выполните scripts/telegram_login.py"
            )

        me = await client.get_me()
        login = me.username or me.id
        print(f"\nАвторизован как: {me.first_name or ''} (@{login})")
        print("\nСкопируйте строку целиком в TELEGRAM_SESSION_STRING:\n")
        print(client.session.save())
        print()


if __name__ == "__main__":
    asyncio.run(main())
