"""Генератор демо-событий для проверки карты без Telegram/RSS.

    python -m app.ingestion.demo --once
    python -m app.ingestion.demo --interval 20
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from uuid import uuid4

from app.db import SessionLocal, init_db
from app.ingestion.pipeline import IngestPayload, ingest_message
from app.seed import seed_regions
from app.utils.time import utcnow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("demo")

# Тексты намеренно похожи на реальные публикации мониторинговых каналов.
DEMO_EVENTS = [
    "Угроза БПЛА в Белгородской области. Беспилотники курсом на запад.",
    "Ракетная опасность в Курской области. Укрывайтесь.",
    "Воздушная тревога в Брянской области.",
    "Угроза БПЛА в Ростовской области. Работает ПВО.",
    "Смешанная атака: ракеты и БПЛА в Воронежской области.",
    "Отбой тревоги в Белгородской области.",
    "Угроза БПЛА в Тульской области.",
    "Ракетная опасность в Смоленской области.",
    "Отбой тревоги в Брянской области.",
    "Воздушная тревога в Московской области.",
    "Угроза БПЛА в Калужской области.",
    "Отбой тревоги в Курской области.",
]


async def ensure_db() -> None:
    await init_db()
    async with SessionLocal() as session:
        await seed_regions(session)


async def emit(text: str) -> None:
    async with SessionLocal() as session:
        result = await ingest_message(
            session,
            IngestPayload(
                text=text,
                source_kind="demo",
                source_external_id="demo-feed",
                source_name="Демо-лента",
                message_external_id=uuid4().hex,
                posted_at=utcnow(),
                is_demo=True,
            ),
        )
    logger.info("DEMO: создано=%s склеено=%s пропущено=%s %s", result.created, result.merged, result.ignored, text[:70])


async def run(once: bool, interval: int) -> None:
    await ensure_db()
    if once:
        for text in DEMO_EVENTS:
            await emit(text)
            await asyncio.sleep(0.2)
        return

    index = 0
    while True:
        await emit(DEMO_EVENTS[index % len(DEMO_EVENTS)])
        index += 1
        await asyncio.sleep(max(5, interval))


def main() -> None:
    parser = argparse.ArgumentParser(description="Демо-генератор событий")
    parser.add_argument("--once", action="store_true", help="Отправить набор и выйти")
    parser.add_argument("--interval", type=int, default=20, help="Интервал цикла, сек")
    args = parser.parse_args()
    try:
        asyncio.run(run(once=args.once, interval=args.interval))
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")


if __name__ == "__main__":
    main()
