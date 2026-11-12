"""Запуск опроса источников внешним планировщиком.

На Vercel (Hobby) собственный cron умеет ходить только раз в сутки, поэтому
опрос каждые 5 минут дёргает внешний планировщик: Supabase Cron (pg_cron +
pg_net), GitHub Actions или cron-job.org. Endpoint защищён токеном POLL_TOKEN.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.schemas import PollOut
from app.services.poller import run_poll

logger = logging.getLogger(__name__)
router = APIRouter(tags=["internal"])

CRON_JOB_NAME = "drone-radar-poll"
CRON_SCHEDULE = "*/5 * * * *"
# Vercel Hobby: функция живёт максимум 300 c, опрос — до POLL_MAX_SECONDS.
CRON_HTTP_TIMEOUT_MS = 280_000


def _check_poll_token(x_poll_token: str | None) -> None:
    """Общая проверка токена для служебных endpoint-ов."""
    if not settings.poll_token:
        raise HTTPException(
            status_code=503,
            detail="Опрос по расписанию отключён: не задан POLL_TOKEN",
        )
    if not x_poll_token or not secrets.compare_digest(x_poll_token, settings.poll_token):
        raise HTTPException(status_code=401, detail="Неверный X-Poll-Token")


@router.post(
    "/internal/poll",
    response_model=PollOut,
    summary="Один цикл опроса источников (для внешнего cron)",
)
async def trigger_poll(
    x_poll_token: str | None = Header(default=None, alias="X-Poll-Token"),
) -> PollOut:
    _check_poll_token(x_poll_token)

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


@router.post(
    "/internal/setup-cron",
    summary="Создать задание Supabase Cron (pg_cron + pg_net)",
)
async def setup_cron(
    request: Request,
    x_poll_token: str | None = Header(default=None, alias="X-Poll-Token"),
) -> dict[str, Any]:
    """Ставит в базу задание `pg_cron`, которое каждые 5 минут зовёт /poll.

    Нужно потому, что cron Vercel на Hobby срабатывает раз в сутки, а
    расписание GitHub Actions сильно задерживается. Повторный вызов
    пересоздаёт задание (идемпотентно).
    """
    _check_poll_token(x_poll_token)

    base_url = str(request.base_url).rstrip("/")
    poll_url = f"{base_url}/api/internal/poll"
    headers = json.dumps(
        {"X-Poll-Token": settings.poll_token, "Content-Type": "application/json"}
    )
    call = (
        "select net.http_post("
        f"url := '{poll_url}', "
        f"headers := '{headers}'::jsonb, "
        "body := '{}'::jsonb, "
        f"timeout_milliseconds := {CRON_HTTP_TIMEOUT_MS})"
    )
    statements = [
        "create extension if not exists pg_cron",
        "create extension if not exists pg_net",
        # Пересоздаём задание, чтобы обновить URL и токен.
        f"select cron.unschedule(jobid) from cron.job where jobname = '{CRON_JOB_NAME}'",
        "select cron.schedule("
        f"'{CRON_JOB_NAME}', '{CRON_SCHEDULE}', $job$ {call}; $job$)",
    ]

    try:
        async with engine.begin() as conn:
            for statement in statements:
                await conn.execute(text(statement))
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "select jobname, schedule, active from cron.job "
                        "where jobname = :name"
                    ),
                    {"name": CRON_JOB_NAME},
                )
            ).all()
    except Exception as exc:  # noqa: BLE001 — вернуть причину для диагностики
        logger.exception("Не удалось создать задание Supabase Cron")
        raise HTTPException(
            status_code=502, detail=f"pg_cron: {str(exc)[:300]}"
        ) from None

    return {
        "ok": True,
        "poll_url": poll_url,
        "schedule": CRON_SCHEDULE,
        "jobs": [list(row) for row in rows],
    }


@router.get(
    "/internal/cron-status",
    summary="Состояние задания Supabase Cron и последние вызовы",
)
async def cron_status(
    x_poll_token: str | None = Header(default=None, alias="X-Poll-Token"),
) -> dict[str, Any]:
    """Диагностика планировщика: активность задания и ответы pg_net."""
    _check_poll_token(x_poll_token)

    try:
        async with engine.connect() as conn:
            jobs = (
                await conn.execute(
                    text("select jobname, schedule, active from cron.job")
                )
            ).all()
    except Exception as exc:  # noqa: BLE001 — нет pg_cron (локальный запуск)
        raise HTTPException(
            status_code=502, detail=f"pg_cron недоступен: {str(exc)[:300]}"
        ) from None

    responses: list[list[Any]] = []
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "select id, status_code, left(coalesce(content, ''), 200), "
                        "created_at from net._http_response order by id desc limit 5"
                    )
                )
            ).all()
        responses = [list(row) for row in rows]
    except Exception:  # noqa: BLE001 — у pg_net может не быть ни одного вызова
        logger.info("pg_net: таблица ответов пока пуста")

    runs: list[list[Any]] = []
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "select jobid, status, left(coalesce(return_message, ''), 200), "
                        "start_time from cron.job_run_details order by runid desc limit 5"
                    )
                )
            ).all()
        runs = [list(row) for row in rows]
    except Exception:  # noqa: BLE001 — нет прав или таблицы
        logger.info("pg_cron: журнал запусков недоступен")

    return {
        "jobs": [list(row) for row in jobs],
        "last_runs": runs,
        "last_responses": responses,
    }
