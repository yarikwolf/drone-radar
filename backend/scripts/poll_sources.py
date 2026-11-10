"""Однократная проверка Telegram-источников для запуска по расписанию.

Читает свежие сообщения публичных каналов из TELEGRAM_CHANNELS, обновляет
базу и завершается. Лаунчер вызывает скрипт каждые 5 минут.

Пример:
    python scripts/poll_sources.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Скрипт запускается как файл (python scripts/poll_sources.py),
# поэтому добавляем корень backend в путь импорта.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ingestion.telegram_listener import poll_once

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("poll_sources")


def main() -> int:
    try:
        stored = asyncio.run(poll_once())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")
        return 130
    except Exception:  # noqa: BLE001 — следующая попытка через 5 минут
        logger.exception("Проверка источников не удалась")
        return 1

    logger.info("Готово: обработано сообщений %s", stored)
    return 0


if __name__ == "__main__":
    sys.exit(main())
