"""Telethon-воркер: слушает публичные Telegram-каналы.

Запуск:
    python -m app.ingestion.telegram_listener

Требуется TELEGRAM_API_ID, TELEGRAM_API_HASH и список TELEGRAM_CHANNELS.
Слушатель не работает с закрытыми чатами и invite-ссылками.

Если Telegram недоступен напрямую, задайте TELEGRAM_PROXY:
socks5://host:port, http://host:port или mtproxy://secret@host:port.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError
from telethon.network.connection import (
    ConnectionTcpMTProxyRandomizedIntermediate,
    ConnectionTcpFull,
)
from telethon.sessions import StringSession

from app.config import settings
from app.db import SessionLocal, init_db
from app.ingestion.pipeline import IngestPayload, IngestResult, ingest_message
from app.utils.time import to_naive_utc, utcnow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("telegram_listener")


def clean_channel(value: str) -> str:
    """Приводит @name / https://t.me/name к чистому username."""
    channel = (value or "").strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if channel.startswith(prefix):
            channel = channel[len(prefix) :]
    return channel.split("?")[0].strip("/")


def build_proxy() -> tuple[object, dict | tuple | None]:
    """Готовит параметры прокси для Telethon.

    Возвращает пару (класс соединения, аргумент proxy). Без прокси — (None, None).
    """
    config = settings.telegram_proxy_config
    if not config:
        return ConnectionTcpFull, None

    kind = config["kind"]
    host = config["host"]
    port = int(config["port"])

    if kind == "mtproxy":
        secret = str(config["secret"])
        logger.info("Telegram через MTProto-прокси %s:%s", host, port)
        return ConnectionTcpMTProxyRandomizedIntermediate, (host, port, secret)

    try:
        import socks  # PySocks
    except ImportError as exc:  # pragma: no cover — зависимость ставится через requirements
        raise RuntimeError(
            "Для SOCKS/HTTP-прокси нужен PySocks: pip install PySocks"
        ) from exc

    proxy_type = socks.SOCKS5 if kind == "socks5" else socks.HTTP
    user = config.get("user")
    password = config.get("password")
    logger.info("Telegram через %s-прокси %s:%s", kind.upper(), host, port)
    return ConnectionTcpFull, (proxy_type, host, port, True, user, password)


def build_client() -> TelegramClient:
    if not settings.telegram_configured:
        raise RuntimeError("Не заданы TELEGRAM_API_ID / TELEGRAM_API_HASH")

    if settings.telegram_session_string:
        session = StringSession(settings.telegram_session_string)
    else:
        path = Path(settings.telegram_session)
        path.parent.mkdir(parents=True, exist_ok=True)
        session = str(path)

    connection, proxy = build_proxy()

    return TelegramClient(
        session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
        connection=connection,
        proxy=proxy,
        connection_retries=5,
        retry_delay=3,
        auto_reconnect=True,
        request_retries=3,
        flood_sleep_threshold=60,
    )


async def _store_message(message, chat) -> IngestResult | None:
    text = (message.raw_text or "").strip()
    if not text:
        return None

    username = getattr(chat, "username", None)
    source_id = username or str(getattr(chat, "id", "unknown"))
    title = getattr(chat, "title", None) or source_id
    message_url = f"https://t.me/{username}/{message.id}" if username else None

    payload = IngestPayload(
        text=text,
        source_kind="telegram",
        source_external_id=source_id,
        source_name=title,
        source_url=f"https://t.me/{username}" if username else None,
        message_external_id=str(message.id),
        message_url=message_url,
        posted_at=getattr(message, "date", None),
    )

    try:
        async with SessionLocal() as session:
            result = await ingest_message(session, payload)
        if result.created:
            logger.info(
                "TG %s/%s: создан алерт (created=%s)",
                source_id,
                message.id,
                result.created,
            )
        elif result.merged:
            logger.debug("TG %s/%s: подтверждение дубля", source_id, message.id)
        elif result.reason not in ("no_keywords", "no_region"):
            logger.debug("TG %s/%s пропущено: %s", source_id, message.id, result.reason)
        return result
    except Exception:  # noqa: BLE001 — одно сообщение не должно ронять воркер
        logger.exception("Ошибка обработки сообщения %s/%s", source_id, message.id)
        return None


async def _backfill(client: TelegramClient, entities: list) -> int:
    """Читает свежие сообщения каналов. Возвращает число обработанных."""
    limit = settings.telegram_backfill_limit
    if limit <= 0:
        return 0
    # Сообщения старше окна дедупликации не создают актуальных алертов:
    # не засоряем базу историей канала.
    max_age = timedelta(minutes=settings.dedup_window_minutes)
    cutoff = utcnow() - max_age
    stored = 0
    for entity in entities:
        try:
            async for message in client.iter_messages(entity, limit=limit):
                posted_at = to_naive_utc(getattr(message, "date", None))
                if posted_at is not None and posted_at < cutoff:
                    # История идёт от новых к старым — дальше только старее.
                    break
                if message.raw_text:
                    await _store_message(message, entity)
                    stored += 1
        except FloodWaitError as exc:
            logger.warning("Telegram FloodWait при backfill: %s c", exc.seconds)
            await asyncio.sleep(exc.seconds + 5)
        except (RPCError, OSError) as exc:
            logger.warning("Не удалось прочитать историю канала: %s", exc)
    return stored


async def _resolve_entities(client: TelegramClient) -> list:
    """Открывает публичные каналы из TELEGRAM_CHANNELS."""
    channels = [clean_channel(item) for item in settings.telegram_channel_list]
    entities = []
    for channel in channels:
        try:
            entities.append(await client.get_entity(channel))
        except (ValueError, RPCError) as exc:
            logger.error("Канал %s недоступен: %s", channel, exc)
    return entities


async def poll_once() -> int:
    """Одна проверка источников: читает свежие сообщения и отключается.

    Рассчитана на запуск по расписанию (например, раз в 5 минут), без
    постоянного соединения. Возвращает число обработанных сообщений.
    """
    if not settings.telegram_configured:
        logger.error("Проверка Telegram не выполнена: нет TELEGRAM_API_ID/HASH")
        return 0
    if not settings.telegram_channel_list:
        logger.error("Проверка Telegram не выполнена: TELEGRAM_CHANNELS пуст")
        return 0

    # Скрипт может запускаться раньше API: гарантируем схему БД.
    await init_db()

    client = build_client()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.error(
                "Сессия Telegram не авторизована. Выполните: python scripts/telegram_login.py"
            )
            return 0

        entities = await _resolve_entities(client)
        if not entities:
            logger.error("Ни один канал не удалось открыть")
            return 0

        stored = await _backfill(client, entities)
        logger.info("Проверено каналов: %s, обработано сообщений: %s", len(entities), stored)
        return stored
    finally:
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:  # noqa: BLE001
            logger.debug("Ошибка при отключении клиента", exc_info=True)


async def main() -> None:
    if not settings.telegram_configured:
        logger.error("Telegram-воркер не запущен: нет TELEGRAM_API_ID/HASH")
        return
    if not settings.telegram_channel_list:
        logger.error("Telegram-воркер не запущен: TELEGRAM_CHANNELS пуст")
        return

    backoff = 5
    while True:
        client = build_client()
        try:
            if settings.telegram_phone:
                await client.start(phone=settings.telegram_phone)
            else:
                # Без TELEGRAM_PHONE Telethon спросит номер и код в терминале.
                await client.start()

            entities = await _resolve_entities(client)

            if not entities:
                raise RuntimeError("Ни один канал не удалось открыть")

            async def handler(event: events.NewMessage.Event) -> None:
                try:
                    chat = await event.get_chat()
                    await _store_message(event.message, chat)
                except FloodWaitError as exc:
                    logger.warning("FloodWait: спим %s c", exc.seconds)
                    await asyncio.sleep(exc.seconds + 5)
                except Exception:  # noqa: BLE001
                    logger.exception("Ошибка в обработчике Telegram")

            client.add_event_handler(handler, events.NewMessage(chats=entities))
            await _backfill(client, entities)

            logger.info("Слушаем %s публичных каналов", len(entities))
            backoff = 5
            await client.run_until_disconnected()
            logger.warning("Соединение с Telegram потеряно, переподключаемся")
        except FloodWaitError as exc:
            wait = exc.seconds + 5
            logger.warning("Telegram FloodWait: %s c", wait)
            await asyncio.sleep(wait)
        except (ConnectionError, OSError, RPCError) as exc:
            logger.error("Сетевая ошибка Telegram: %s. Retry через %s c", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)
        except Exception:  # noqa: BLE001
            logger.exception("Критическая ошибка Telegram-воркера. Retry через %s c", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)
        finally:
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception:  # noqa: BLE001
                logger.debug("Ошибка при отключении клиента", exc_info=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")
