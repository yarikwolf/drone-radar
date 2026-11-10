"""Одноразовая авторизация Telethon и печать StringSession.

Запуск из backend/:
    python scripts/telegram_login.py

При первом запуске Telethon спросит телефон и код подтверждения, после чего
создаст файл сессии backend/data/radar.session и выведет строковую сессию.

Полученную строку можно положить в TELEGRAM_SESSION_STRING (.env) — она нужна
только для Docker. Строка даёт полный доступ к аккаунту — храните её как пароль.
"""

from __future__ import annotations

import asyncio

from telethon.sessions import StringSession

from app.config import settings
from app.ingestion.telegram_listener import build_proxy


async def main() -> None:
    if not settings.telegram_configured:
        raise SystemExit("Задайте TELEGRAM_API_ID и TELEGRAM_API_HASH")

    # Импорт внутри функции: TelegramClient создаётся с нужным классом соединения.
    from telethon import TelegramClient

    connection, proxy = build_proxy()
    async with TelegramClient(
        StringSession(),
        settings.telegram_api_id,
        settings.telegram_api_hash,
        connection=connection,
        proxy=proxy,
    ) as client:
        if settings.telegram_phone:
            await client.start(phone=settings.telegram_phone)
        else:
            await client.start()
        me = await client.get_me()
        print(f"\nАвторизован как: {me.first_name or ''} (@{me.username or me.id})")
        print("\nTELEGRAM_SESSION_STRING=")
        print(client.session.save())


if __name__ == "__main__":
    asyncio.run(main())
