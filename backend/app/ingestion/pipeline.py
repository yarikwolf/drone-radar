"""Единая точка приёма сообщения: NLP → гео → дедупликация → БД → WebSocket."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.classifier import classify_hits
from app.ingestion.geo import (
    GeoMatch,
    REGION_BY_CODE,
    match_places,
    match_places_anywhere,
    match_regions,
)
from app.ingestion.keywords import detect_keywords, normalize_text
from app.models import Alert, Region
from app.services.alerts import get_or_create_source, upsert_alert
from app.services.events import broadcaster
from app.utils.time import to_naive_utc, to_utc, utcnow

logger = logging.getLogger(__name__)

SOURCE_TRUST = {
    "official_api": 0.95,
    "telegram": 0.75,
    "rss": 0.65,
    "demo": 0.45,
    "webhook": 0.8,
}


@dataclass(slots=True)
class IngestPayload:
    text: str
    source_kind: str
    source_external_id: str
    source_name: str
    source_url: str | None = None
    message_external_id: str | None = None
    message_url: str | None = None
    posted_at: datetime | None = None
    is_demo: bool = False


@dataclass(slots=True)
class IngestResult:
    created: int = 0
    merged: int = 0
    ignored: int = 0
    alerts: list[Alert] = field(default_factory=list)
    reason: str | None = None

    @property
    def accepted(self) -> int:
        return self.created + self.merged


async def _resolve_region(session: AsyncSession, code: str) -> Region | None:
    """Берёт регион из БД, при необходимости создаёт из статического каталога."""
    region = await session.scalar(select(Region).where(Region.code == code))
    if region is not None:
        return region

    definition = REGION_BY_CODE.get(code)
    if definition is None:
        return None

    region = Region(
        code=definition.code,
        name=definition.name,
        region_type=definition.region_type,
        country=definition.country,
        lat=definition.lat,
        lon=definition.lon,
        aliases=list(definition.aliases),
    )
    session.add(region)
    await session.flush()
    return region


def _message_id(payload: IngestPayload, event_at: datetime, norm_text: str) -> str:
    if payload.message_external_id:
        return str(payload.message_external_id)
    raw = f"{payload.source_kind}|{payload.source_external_id}|{event_at.isoformat()}|{norm_text[:120]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


async def ingest_message(session: AsyncSession, payload: IngestPayload) -> IngestResult:
    """Обрабатывает одно сообщение. Коммитит транзакцию при успехе."""
    raw_text = (payload.text or "").strip()[:4000]
    if len(raw_text) < 3:
        return IngestResult(ignored=1, reason="empty_text")

    norm_text = normalize_text(raw_text)
    hits = detect_keywords(norm_text)
    if not hits.relevant:
        return IngestResult(ignored=1, reason="no_keywords")

    region_matches = match_regions(norm_text)
    if not region_matches:
        # В сообщении может быть только населённый пункт («Шебекино»).
        # Если все найденные точки указывают на один регион — доверяем им.
        anywhere = match_places_anywhere(norm_text)
        codes = list(dict.fromkeys(item.place.region_code for item in anywhere))
        if len(codes) == 1:
            definition = REGION_BY_CODE.get(codes[0])
            if definition is not None:
                region_matches = [
                    GeoMatch(region=definition, alias=anywhere[0].alias)
                ]
    if not region_matches:
        return IngestResult(ignored=1, reason="no_region")

    place_matches = match_places(
        norm_text, [match.region.code for match in region_matches]
    )
    places_by_region: dict[str, list[dict]] = {}
    for place_match in place_matches:
        places_by_region.setdefault(place_match.place.region_code, []).append(
            {
                "name": place_match.place.name,
                "lat": place_match.place.lat,
                "lon": place_match.place.lon,
                "kind": place_match.place.kind,
            }
        )

    classification = classify_hits(hits)
    event_at = to_naive_utc(payload.posted_at) or utcnow()
    now = utcnow()

    source = await get_or_create_source(
        session,
        kind=payload.source_kind,
        external_id=payload.source_external_id,
        name=payload.source_name,
        url=payload.source_url,
        trust=SOURCE_TRUST.get(payload.source_kind, 0.7),
    )

    created = 0
    merged = 0
    alerts: list[Alert] = []
    publish_pairs: list[tuple[Alert, GeoMatch, bool]] = []
    seen_regions: set[str] = set()

    try:
        for match in region_matches:
            if match.region.code in seen_regions:
                continue
            seen_regions.add(match.region.code)

            region = await _resolve_region(session, match.region.code)
            if region is None:
                continue

            alert, is_created = await upsert_alert(
                session,
                region=region,
                classification=classification,
                norm_text=norm_text,
                raw_text=raw_text,
                source=source,
                message_external_id=_message_id(payload, event_at, norm_text),
                message_url=payload.message_url,
                event_at=event_at,
                is_demo=payload.is_demo,
                places=places_by_region.get(match.region.code),
                now=now,
            )
            alerts.append(alert)
            publish_pairs.append((alert, match, is_created))
            if is_created:
                created += 1
            else:
                merged += 1

        if not alerts:
            await session.rollback()
            return IngestResult(ignored=1, reason="region_not_found")

        await session.commit()
    except Exception:
        await session.rollback()
        raise

    # Публикуем только после успешного commit, чтобы UI не увидел «фантом».
    for alert, match, is_created in publish_pairs:
        try:
            await broadcaster.publish(
                {
                    "type": "alert",
                    "alert_id": alert.id,
                    "region_code": match.region.code,
                    "region_name": match.region.name,
                    "threat_type": alert.threat_type,
                    "status": alert.status,
                    "priority": alert.priority,
                    "places": [item.get("name") for item in (alert.places or [])],
                    "event_at": to_utc(alert.event_at).isoformat() if alert.event_at else None,
                    "created": is_created,
                    "is_demo": alert.is_demo,
                }
            )
        except Exception:  # noqa: BLE001 — сбой push не должен откатывать данные
            logger.exception("Не удалось опубликовать WebSocket-событие")

    return IngestResult(created=created, merged=merged, alerts=alerts)
