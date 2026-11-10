"""Сервис создания алертов и дедупликации.

Ключевая идея: несколько сообщений из разных каналов об одном событии
дают один Alert и много AlertSource. Это сохраняет аудит и повышает доверие.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.ingestion.classifier import Classification, title_for
from app.models import Alert, AlertSource, Region, Source
from app.services.dedup import bucket_for, build_content_hash, jaccard, tokenize
from app.utils.time import to_naive_utc, utcnow


async def get_or_create_source(
    session: AsyncSession,
    *,
    kind: str,
    external_id: str,
    name: str,
    url: str | None = None,
    trust: float = 0.7,
) -> Source:
    """Идемпотентно получает/создаёт источник."""
    external_id = (external_id or "unknown")[:255]
    stmt = select(Source).where(Source.kind == kind, Source.external_id == external_id)
    source = await session.scalar(stmt)
    now = utcnow()

    if source is None:
        source = Source(
            kind=kind,
            external_id=external_id,
            name=(name or external_id)[:255],
            url=(url or None),
            trust=trust,
        )
        try:
            async with session.begin_nested():
                session.add(source)
                await session.flush()
        except IntegrityError:
            # Параллельный воркер уже создал источник.
            source = await session.scalar(stmt)
            if source is None:
                raise
    else:
        source.name = (name or source.name)[:255]
        if url:
            source.url = url

    source.last_seen_at = now
    source.last_error = None
    return source


async def _exact_duplicate(
    session: AsyncSession,
    content_hash: str,
    bucket: int,
) -> Alert | None:
    """Точный дубль: тот же hash в текущем или предыдущем окне."""
    stmt = (
        select(Alert)
        .where(
            Alert.content_hash == content_hash,
            Alert.dedup_bucket.in_((bucket, bucket - 1)),
        )
        .order_by(Alert.event_at.desc())
        .limit(1)
    )
    return await session.scalar(stmt)


async def _fuzzy_duplicate(
    session: AsyncSession,
    *,
    region_id: int,
    classification: Classification,
    norm_text: str,
    now: datetime,
) -> Alert | None:
    """Похожий текст в том же регионе и с тем же статусом в коротком окне."""
    tokens = tokenize(norm_text)
    if not tokens:
        return None

    cutoff = now - timedelta(minutes=settings.dedup_fuzzy_window_minutes)
    stmt = (
        select(Alert)
        .where(
            Alert.region_id == region_id,
            Alert.threat_type == classification.threat_type,
            Alert.status == classification.status,
            Alert.event_at >= cutoff,
        )
        .order_by(Alert.event_at.desc())
        .limit(20)
    )
    candidates = list((await session.scalars(stmt)).all())

    best: Alert | None = None
    best_score = 0.0
    for candidate in candidates:
        score = jaccard(tokens, tokenize(candidate.norm_text))
        if score > best_score:
            best, best_score = candidate, score

    if best is not None and best_score >= settings.dedup_similarity_threshold:
        return best
    return None


def _occurrence_hash(content_hash: str, event_at: datetime) -> str:
    """Ключ дедупликации для повторного события с тем же текстом.

    Уникальный индекс (content_hash, dedup_bucket) не позволяет создать
    вторую запись с тем же текстом в том же окне. Новому событию даём
    отдельный ключ, чтобы оно попало в ленту самостоятельной карточкой.
    Ключ детерминирован: повторное чтение того же сообщения не создаст
    ещё одну запись.
    """
    raw = f"{content_hash}|{event_at.isoformat()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


async def _is_stale_duplicate(
    session: AsyncSession,
    *,
    region_id: int,
    candidate: Alert,
    event_at: datetime,
) -> bool:
    """Проверяет, что найденный дубль — не устаревшая запись.

    Дубли склеиваются только с последним событием региона. Если после
    кандидата уже было другое событие, а новое сообщение пришло позже,
    это новая тревога/отбой, а не подтверждение старой записи. Иначе
    повторный «отбой» склеивался бы с отбоем часовой давности и не
    закрывал актуальную тревогу, а лента «замерзала» на старых событиях.
    """
    if event_at <= candidate.event_at:
        # Историческое сообщение из backfill — прикрепляем к найденному.
        return False

    latest = await session.scalar(
        select(func.max(Alert.event_at)).where(Alert.region_id == region_id)
    )
    return latest is not None and latest > candidate.event_at


async def _attach_source(
    session: AsyncSession,
    *,
    alert: Alert,
    source: Source,
    message_external_id: str,
    raw_text: str,
    message_url: str | None,
    posted_at: datetime,
    now: datetime,
) -> bool:
    """Прикрепляет сообщение к алерту. False — такое сообщение уже учтено."""
    message_external_id = (message_external_id or "unknown")[:128]

    if alert.id is not None:
        exists = await session.scalar(
            select(AlertSource.id).where(
                AlertSource.alert_id == alert.id,
                AlertSource.source_id == source.id,
                AlertSource.external_message_id == message_external_id,
            )
        )
        if exists is not None:
            return False

    # Не трогаем alert.sources: в async-сессии обращение к незагруженной
    # коллекции вызовет lazy load. Создаём связь через внешний ключ.
    link = AlertSource(
        alert=alert,
        source=source,
        external_message_id=message_external_id,
        message_url=(message_url or None),
        raw_text=raw_text[:4000],
        posted_at=posted_at,
    )
    session.add(link)
    alert.merged_count += 1
    alert.last_seen_at = now
    return True


async def _clear_region_alerts(
    session: AsyncSession, *, region_id: int, clear_at: datetime
) -> None:
    """Отбой закрывает только те алерты региона, которые были раньше отбоя.

    Сравниваем по времени события, а не по времени обработки: при backfill
    сообщения из разных каналов приходят в произвольном порядке, и старый
    отбой не должен закрывать более новую тревогу.
    """
    await session.execute(
        update(Alert)
        .where(
            Alert.region_id == region_id,
            Alert.status.in_(("active", "warning")),
            Alert.event_at <= clear_at,
        )
        .values(status="cleared", cleared_at=clear_at)
    )


async def _close_if_cleared_later(session: AsyncSession, *, alert: Alert) -> None:
    """Закрывает тревогу, если в регионе уже есть более поздний отбой.

    Нужно для backfill: сообщение об отбое могло быть обработано раньше
    тревоги, потому что каналы читаются по очереди.
    """
    if alert.region_id is None or alert.status not in ("active", "warning"):
        return

    clear_at = await session.scalar(
        select(func.max(Alert.event_at)).where(
            Alert.region_id == alert.region_id,
            Alert.status == "all_clear",
            Alert.event_at >= alert.event_at,
        )
    )
    if clear_at is not None:
        alert.status = "cleared"
        alert.cleared_at = clear_at


def _merge_places(
    existing: list[dict] | None, incoming: list[dict] | None
) -> list[dict]:
    """Объединяет точки назначения без дублей (по названию и координатам)."""
    merged: dict[tuple[str, float, float], dict] = {}
    for item in (existing or []):
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        key = (name.casefold(), round(float(item.get("lat", 0)), 4), round(float(item.get("lon", 0)), 4))
        merged[key] = item
    for item in (incoming or []):
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        key = (name.casefold(), round(float(item.get("lat", 0)), 4), round(float(item.get("lon", 0)), 4))
        merged.setdefault(key, item)
    return sorted(merged.values(), key=lambda item: str(item.get("name", "")))


async def upsert_alert(
    session: AsyncSession,
    *,
    region: Region,
    classification: Classification,
    norm_text: str,
    raw_text: str,
    source: Source,
    message_external_id: str,
    message_url: str | None,
    event_at: datetime,
    is_demo: bool = False,
    places: list[dict] | None = None,
    now: datetime | None = None,
) -> tuple[Alert, bool]:
    """Создаёт алерт или прикрепляет сообщение к найденному дублю.

    Возвращает (alert, created). Коммит делает вызывающий pipeline.
    """
    now = now or utcnow()
    event_at = to_naive_utc(event_at) or now
    bucket = bucket_for(event_at, settings.dedup_window_minutes)
    content_hash = build_content_hash(norm_text, region.code, classification.threat_type, classification.status)

    existing = await _exact_duplicate(session, content_hash, bucket)
    if existing is None:
        existing = await _fuzzy_duplicate(
            session,
            region_id=region.id,
            classification=classification,
            norm_text=norm_text,
            now=now,
        )

    if existing is not None and await _is_stale_duplicate(
        session, region_id=region.id, candidate=existing, event_at=event_at
    ):
        existing = None
        # Новое событие с тем же текстом: уводим ключ от старой записи,
        # иначе уникальный индекс не даст сохранить новую карточку.
        content_hash = _occurrence_hash(content_hash, event_at)

    if existing is not None:
        existing.places = _merge_places(existing.places, places)
        if existing.status == "cleared" and classification.status in ("active", "warning"):
            # Повторная тревога после отбоя в пределах окна дедупликации:
            # возвращаем запись в работу, иначе новое сообщение «потеряется».
            existing.status = classification.status
            existing.threat_type = classification.threat_type
            existing.priority = classification.priority
            existing.confidence = max(existing.confidence, classification.confidence)
            existing.title = title_for(classification, region.name)
            existing.message = raw_text[:4000]
            existing.norm_text = norm_text
            existing.cleared_at = None
            if event_at > existing.event_at:
                existing.event_at = event_at
        # Если пришло более сильное подтверждение (warning → active), обновляем карточку.
        elif classification.priority > existing.priority and existing.status != "all_clear":
            existing.threat_type = classification.threat_type
            existing.status = classification.status
            existing.priority = classification.priority
            existing.confidence = max(existing.confidence, classification.confidence)
            existing.title = title_for(classification, region.name)
            existing.message = raw_text[:4000]
            existing.norm_text = norm_text
            if event_at > existing.event_at:
                existing.event_at = event_at

        await _attach_source(
            session,
            alert=existing,
            source=source,
            message_external_id=message_external_id,
            raw_text=raw_text,
            message_url=message_url,
            posted_at=event_at,
            now=now,
        )
        if classification.status == "all_clear":
            # Отбой должен закрывать тревоги даже когда сообщение
            # склеилось с предыдущим отбоем: иначе тревога останется
            # висеть на карте.
            await _clear_region_alerts(
                session, region_id=region.id, clear_at=event_at
            )
        await _close_if_cleared_later(session, alert=existing)
        return existing, False

    alert = Alert(
        region=region,
        threat_type=classification.threat_type,
        status=classification.status,
        title=title_for(classification, region.name),
        message=raw_text[:4000],
        norm_text=norm_text,
        content_hash=content_hash,
        dedup_bucket=bucket,
        priority=classification.priority,
        confidence=classification.confidence,
        lat=region.lat,
        lon=region.lon,
        event_at=event_at,
        first_seen_at=now,
        last_seen_at=now,
        merged_count=0,
        is_demo=is_demo,
        places=list(places or []),
    )

    try:
        async with session.begin_nested():
            session.add(alert)
            await session.flush()
    except IntegrityError:
        # Гонка: другой воркер успел вставить этот же hash.
        if alert in session:
            session.expunge(alert)
        existing = await _exact_duplicate(session, content_hash, bucket)
        if existing is None:
            raise
        await _attach_source(
            session,
            alert=existing,
            source=source,
            message_external_id=message_external_id,
            raw_text=raw_text,
            message_url=message_url,
            posted_at=event_at,
            now=now,
        )
        if classification.status == "all_clear":
            # Отбой должен закрывать тревоги даже когда сообщение
            # склеилось с предыдущим отбоем: иначе тревога останется
            # висеть на карте.
            await _clear_region_alerts(
                session, region_id=region.id, clear_at=event_at
            )
        await _close_if_cleared_later(session, alert=existing)
        return existing, False

    await _attach_source(
        session,
        alert=alert,
        source=source,
        message_external_id=message_external_id,
        raw_text=raw_text,
        message_url=message_url,
        posted_at=event_at,
        now=now,
    )

    if alert.status == "all_clear":
        await _clear_region_alerts(session, region_id=region.id, clear_at=event_at)
    else:
        await _close_if_cleared_later(session, alert=alert)

    return alert, True


async def latest_region_alerts(
    session: AsyncSession,
    *,
    since: datetime,
    statuses: tuple[str, ...] = ("active", "warning", "all_clear"),
) -> list[Alert]:
    """Возвращает последний по времени алерт по каждому региону.

    Строки со статусом ``cleared`` — это исторические записи, закрытые
    отбоем, поэтому текущий статус региона по ним не определяется.
    ``row_number()`` переносимо между PostgreSQL и SQLite.
    """
    ranked = (
        select(
            Alert.id.label("alert_id"),
            func.row_number()
            .over(
                partition_by=Alert.region_id,
                order_by=(Alert.event_at.desc(), Alert.id.desc()),
            )
            .label("rn"),
        )
        .where(
            Alert.event_at >= since,
            Alert.status.in_(statuses),
        )
        .subquery()
    )

    return list(
        (
            await session.scalars(
                select(Alert)
                .join(ranked, Alert.id == ranked.c.alert_id)
                .where(ranked.c.rn == 1)
                .order_by(Alert.priority.desc(), Alert.event_at.desc())
            )
        ).all()
    )
