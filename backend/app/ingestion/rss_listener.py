"""RSS/новостной поллер с изоляцией ошибок по каждому фиду.

Запуск:
    python -m app.ingestion.rss_listener
"""

from __future__ import annotations

import asyncio
import calendar
import hashlib
import html
import logging
import re
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from app.config import settings
from app.db import SessionLocal
from app.ingestion.pipeline import IngestPayload, ingest_message
from app.utils.time import utcnow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("rss_listener")

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
MAX_AGE = timedelta(hours=48)


def strip_html(value: str) -> str:
    value = TAG_RE.sub(" ", value or "")
    return SPACE_RE.sub(" ", html.unescape(value)).strip()


def entry_datetime(entry: dict) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc).replace(tzinfo=None)


def feed_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:32]


async def poll_once(client: httpx.AsyncClient, seen: set[str]) -> int:
    accepted = 0
    for feed_url in settings.rss_feed_list:
        try:
            response = await client.get(feed_url)
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            feed_title = strip_html(parsed.feed.get("title", "")) or feed_url
            source_id = feed_key(feed_url)
            logger.info("RSS %s: %s записей", feed_title, len(parsed.entries))

            for entry in parsed.entries:
                entry_id = str(entry.get("id") or entry.get("link") or "")
                if not entry_id:
                    raw = f"{entry.get('title', '')}|{entry.get('summary', '')}"
                    entry_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]
                if entry_id in seen:
                    continue
                seen.add(entry_id)

                event_at = entry_datetime(entry) or utcnow()
                if event_at < utcnow() - MAX_AGE:
                    continue

                text = strip_html(
                    f"{entry.get('title', '')}. {entry.get('summary', '')}"
                )[:4000]
                if len(text) < 3:
                    continue

                async with SessionLocal() as session:
                    result = await ingest_message(
                        session,
                        IngestPayload(
                            text=text,
                            source_kind="rss",
                            source_external_id=source_id,
                            source_name=feed_title,
                            source_url=feed_url,
                            message_external_id=entry_id[:128],
                            message_url=entry.get("link"),
                            posted_at=event_at,
                        ),
                    )
                if result.accepted:
                    accepted += 1
                    logger.info("RSS %s: +%s (%s)", feed_title, result.accepted, entry.get("link"))
        except (httpx.HTTPError, ValueError) as exc:
            # Один сломанный фид не мешает остальным; повторим на следующем цикле.
            logger.warning("RSS %s недоступен: %s", feed_url, exc)
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка обработки RSS %s", feed_url)

    if len(seen) > 5000:
        seen.clear()
    return accepted


async def main() -> None:
    if not settings.rss_feed_list:
        logger.error("RSS-воркер не запущен: RSS_FEEDS пуст")
        return

    seen: set[str] = set()
    timeout = httpx.Timeout(20.0, connect=10.0)
    headers = {"User-Agent": "DroneAlertRadar/1.0 (open-source monitoring)"}

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        while True:
            try:
                accepted = await poll_once(client, seen)
                logger.info("Цикл RSS завершён, новых событий: %s", accepted)
            except Exception:  # noqa: BLE001
                logger.exception("Критическая ошибка RSS-цикла")
            await asyncio.sleep(max(15, settings.rss_poll_seconds))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")
