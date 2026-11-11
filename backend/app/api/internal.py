"""Запуск опроса источников внешним планировщиком.

На Vercel (Hobby) собственный cron умеет ходить только раз в сутки, поэтому
опрос каждые 5 минут дёргает внешний планировщик: GitHub Actions или
cron-job.org. Endpoint защищён токеном POLL_TOKEN.
"""

from __future__ import annotations

import asyncio
import logging
import secrets

from fastapi import APIRouter, Header, HTTPException

from app.config import settings
from app.schemas import PollOut
from app.services.poller import run_poll

logger = logging.getLogger(__name__)
router = APIRouter(tags=["internal"])


@router.post(
    "/internal/poll",
    response_model=PollOut,
    summary="Один цикл опроса источников (для внешнего cron)",
)
async def trigger_poll(
    x_poll_token: str | None = Header(default=None, alias="X-Poll-Token"),
) -> PollOut:
    if not settings.poll_token:
        raise HTTPException(
            status_code=503,
            detail="Опрос по расписанию отключён: не задан POLL_TOKEN",
        )
    if not x_poll_token or not secrets.compare_digest(x_poll_token, settings.poll_token):
        raise HTTPException(status_code=401, detail="Неверный X-Poll-Token")

    try:
        result = await asyncio.wait_for(
            run_poll(), timeout=settings.poll_max_seconds
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Опрос не завершился за {settings.poll_max_seconds} c",
        ) from None
    except Exception:  # noqa: BLE001 — планировщик должен увидеть ошибку
        logger.exception("Опрос источников завершился ошибкой")
        raise HTTPException(
            status_code=502, detail="Опрос источников завершился ошибкой"
        ) from None

    return PollOut(**result)
