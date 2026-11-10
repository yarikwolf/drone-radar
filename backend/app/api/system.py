"""Служебные эндпоинты: health, статистика, webhook-приём, WebSocket."""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.ingestion.pipeline import IngestPayload, ingest_message
from app.models import Alert
from app.schemas import HealthOut, StatsOut, WebhookIn, WebhookOut
from app.services.alerts import latest_region_alerts
from app.services.events import broadcaster
from app.utils.time import to_utc, utcnow

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])

API_VERSION = "1.0.0"


@router.get("/health", response_model=HealthOut, summary="Проверка живости")
async def health(session: AsyncSession = Depends(get_session)) -> HealthOut:
    try:
        await session.execute(select(1))
        database = "ok"
        status = "ok"
    except Exception:  # noqa: BLE001
        logger.exception("Healthcheck: БД недоступна")
        database = "error"
        status = "degraded"
    return HealthOut(status=status, database=database, version=API_VERSION)


@router.get("/stats/summary", response_model=StatsOut, summary="Счётчики для дашборда")
async def stats_summary(session: AsyncSession = Depends(get_session)) -> StatsOut:
    now = utcnow()
    day_cutoff = now - timedelta(hours=24)

    # Считаем текущий статус каждого региона, а не все исторические строки:
    # у региона может быть несколько тревог и отбоев за сутки.
    latest = await latest_region_alerts(session, since=day_cutoff)

    active = sum(1 for alert in latest if alert.status == "active")
    warning = sum(1 for alert in latest if alert.status == "warning")
    all_clear = sum(1 for alert in latest if alert.status == "all_clear")
    regions_with_alert = active + warning

    alerts_24h = await session.scalar(
        select(func.count(Alert.id)).where(Alert.event_at >= day_cutoff)
    )
    last_event_at = await session.scalar(select(func.max(Alert.event_at)))

    return StatsOut(
        active=active,
        warning=warning,
        all_clear=all_clear,
        regions_with_alert=regions_with_alert,
        alerts_24h=int(alerts_24h or 0),
        last_event_at=to_utc(last_event_at),
    )


@router.post(
    "/ingest/webhook",
    response_model=WebhookOut,
    status_code=202,
    summary="Приём сообщения из официального API",
)
async def ingest_webhook(
    body: WebhookIn,
    x_ingest_token: str | None = Header(default=None, alias="X-Ingest-Token"),
    session: AsyncSession = Depends(get_session),
) -> WebhookOut:
    if not settings.ingest_token:
        raise HTTPException(status_code=503, detail="Webhook-приём отключён: не задан INGEST_TOKEN")
    if not x_ingest_token or not secrets.compare_digest(x_ingest_token, settings.ingest_token):
        raise HTTPException(status_code=401, detail="Неверный X-Ingest-Token")

    result = await ingest_message(
        session,
        IngestPayload(
            text=body.text,
            source_kind="official_api",
            source_external_id=body.source,
            source_name=body.source_name or body.source,
            source_url=body.url,
            message_external_id=uuid4().hex,
            message_url=body.url,
            posted_at=body.posted_at,
        ),
    )
    return WebhookOut(
        created=result.created,
        merged=result.merged,
        ignored=result.ignored,
        reason=result.reason,
    )


@router.websocket("/ws/alerts")
async def alerts_websocket(websocket: WebSocket) -> None:
    """Push-канал: клиент перезапрашивает /api/alerts при каждом событии."""
    await broadcaster.connect(websocket)
    try:
        await websocket.send_json(
            {"type": "hello", "app": settings.app_name, "version": API_VERSION}
        )
        while True:
            # Клиент может присылать ping; ждём и игнорируем.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        logger.debug("WebSocket закрыт с ошибкой", exc_info=True)
    finally:
        await broadcaster.disconnect(websocket)
