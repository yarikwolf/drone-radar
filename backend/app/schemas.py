"""Pydantic-схемы API и функции сериализации ORM → JSON."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models import Alert
from app.utils.time import to_utc


class RegionOut(BaseModel):
    code: str
    name: str
    region_type: str
    country: str
    lat: float
    lon: float


class AlertSourceOut(BaseModel):
    source_name: str
    source_kind: str
    trust: float
    url: str | None = None
    external_message_id: str
    posted_at: datetime


class PlaceOut(BaseModel):
    """Точка назначения: конкретный населённый пункт или район."""

    name: str
    lat: float
    lon: float
    kind: str = "settlement"


class AlertOut(BaseModel):
    id: int
    region: RegionOut | None = None
    threat_type: str
    status: str
    title: str
    message: str
    priority: int
    confidence: float
    lat: float | None = None
    lon: float | None = None
    places: list[PlaceOut] = Field(default_factory=list)
    event_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime
    cleared_at: datetime | None = None
    merged_count: int
    is_demo: bool
    sources: list[AlertSourceOut] = Field(default_factory=list)


class AlertPage(BaseModel):
    items: list[AlertOut]
    total: int
    limit: int
    offset: int


class RegionListOut(BaseModel):
    items: list[RegionOut]
    total: int


class StatsOut(BaseModel):
    active: int
    warning: int
    all_clear: int
    regions_with_alert: int
    alerts_24h: int
    last_event_at: datetime | None = None


class HealthOut(BaseModel):
    status: str
    database: str
    version: str


class WebhookIn(BaseModel):
    text: str = Field(min_length=3, max_length=4000)
    source: str = Field(default="webhook", max_length=120)
    source_name: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None, max_length=1024)
    posted_at: datetime | None = None


class WebhookOut(BaseModel):
    created: int
    merged: int
    ignored: int
    reason: str | None = None


def alert_to_out(alert: Alert) -> AlertOut:
    """Явная сериализация: не тянем lazy-связи вне сессии и фиксируем UTC."""
    region = None
    if alert.region is not None:
        region = RegionOut(
            code=alert.region.code,
            name=alert.region.name,
            region_type=alert.region.region_type,
            country=alert.region.country,
            lat=alert.region.lat,
            lon=alert.region.lon,
        )

    sources = sorted(alert.sources, key=lambda link: link.posted_at)
    source_items = [
        AlertSourceOut(
            source_name=link.source.name if link.source else "unknown",
            source_kind=link.source.kind if link.source else "unknown",
            trust=link.source.trust if link.source else 0.0,
            url=link.message_url or (link.source.url if link.source else None),
            external_message_id=link.external_message_id,
            posted_at=to_utc(link.posted_at) or to_utc(alert.event_at),
        )
        for link in sources
    ]

    places = [
        PlaceOut(
            name=item.get("name", "—"),
            lat=float(item.get("lat", 0.0)),
            lon=float(item.get("lon", 0.0)),
            kind=item.get("kind", "settlement"),
        )
        for item in (alert.places or [])
        if item.get("lat") is not None and item.get("lon") is not None
    ]

    return AlertOut(
        id=alert.id,
        region=region,
        threat_type=alert.threat_type,
        status=alert.status,
        title=alert.title,
        message=alert.message,
        priority=alert.priority,
        confidence=alert.confidence,
        lat=alert.lat if alert.lat is not None else (region.lat if region else None),
        lon=alert.lon if alert.lon is not None else (region.lon if region else None),
        places=places,
        event_at=to_utc(alert.event_at),
        first_seen_at=to_utc(alert.first_seen_at),
        last_seen_at=to_utc(alert.last_seen_at),
        cleared_at=to_utc(alert.cleared_at),
        merged_count=alert.merged_count,
        is_demo=alert.is_demo,
        sources=source_items,
    )
