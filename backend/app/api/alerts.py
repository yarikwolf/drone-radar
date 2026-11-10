"""API ленты алертов: фильтры, пагинация, активные события."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Alert, Region
from app.schemas import AlertOut, AlertPage, alert_to_out
from app.services.alerts import latest_region_alerts
from app.utils.time import to_naive_utc, utcnow

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@router.get("", response_model=AlertPage, summary="Лента алертов с фильтрами")
async def list_alerts(
    region: str | None = Query(default=None, description="Коды регионов через запятую"),
    threat: str | None = Query(default=None, description="drone,missile,mixed,unknown"),
    status: str | None = Query(default=None, description="active,warning,all_clear,cleared"),
    since: datetime | None = Query(default=None, description="ISO 8601, по умолчанию 24 часа"),
    until: datetime | None = Query(default=None, description="ISO 8601"),
    hours: int | None = Query(default=None, ge=1, le=24 * 30, description="Альтернатива since"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> AlertPage:
    now = utcnow()
    since_naive = to_naive_utc(since)
    until_naive = to_naive_utc(until)

    if since_naive is None:
        hours_value = hours if hours is not None else 24
        since_naive = now - timedelta(hours=hours_value)

    conditions = [Alert.event_at >= since_naive]
    if until_naive is not None:
        conditions.append(Alert.event_at <= until_naive)

    region_codes = _split(region)
    if region_codes:
        conditions.append(Alert.region.has(Region.code.in_(region_codes)))

    threat_values = _split(threat)
    if threat_values:
        conditions.append(Alert.threat_type.in_(threat_values))

    status_values = _split(status)
    if status_values:
        conditions.append(Alert.status.in_(status_values))

    total = await session.scalar(
        select(func.count(Alert.id)).where(*conditions)
    )
    items = (
        await session.scalars(
            select(Alert)
            .where(*conditions)
            .order_by(Alert.event_at.desc(), Alert.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()

    return AlertPage(
        items=[alert_to_out(alert) for alert in items],
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


@router.get("/active", response_model=list[AlertOut], summary="Последний алерт по каждому региону")
async def active_alerts(
    region: str | None = Query(default=None),
    threat: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[AlertOut]:
    now = utcnow()
    active_cutoff = now - timedelta(minutes=settings.active_window_minutes)
    clear_cutoff = now - timedelta(minutes=settings.all_clear_window_minutes)

    # Ранжируем по самому широкому окну, а затем отбрасываем статусы,
    # которые уже вышли из своего окна жизни. Так старый active не может
    # «перекрыть» более новый отбой того же региона.
    oldest = min(active_cutoff, clear_cutoff)
    latest = await latest_region_alerts(session, since=oldest)

    region_codes = _split(region)
    threat_values = _split(threat)

    result: list[Alert] = []
    for alert in latest:
        cutoff = active_cutoff if alert.status in ("active", "warning") else clear_cutoff
        if alert.event_at < cutoff:
            continue
        if region_codes and (alert.region is None or alert.region.code not in region_codes):
            continue
        if threat_values and alert.threat_type not in threat_values:
            continue
        result.append(alert)

    return [alert_to_out(alert) for alert in result]


@router.get("/{alert_id}", response_model=AlertOut, summary="Детали алерта")
async def get_alert(
    alert_id: int,
    session: AsyncSession = Depends(get_session),
) -> AlertOut:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Алерт не найден")
    return alert_to_out(alert)
