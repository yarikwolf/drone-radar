"""Сидирование справочника регионов из app/data/regions.json."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.geo import REGION_CATALOG
from app.models import Region

logger = logging.getLogger(__name__)


async def seed_regions(session: AsyncSession) -> int:
    """Создаёт/обновляет регионы. Возвращает количество новых записей."""
    existing = {
        region.code: region for region in (await session.scalars(select(Region))).all()
    }

    catalog_codes = {definition.code for definition in REGION_CATALOG}

    created = 0
    deactivated = 0
    for definition in REGION_CATALOG:
        region = existing.get(definition.code)
        if region is None:
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
            created += 1
        else:
            # JSON — источник истины для справочника.
            region.name = definition.name
            region.region_type = definition.region_type
            region.country = definition.country
            region.lat = definition.lat
            region.lon = definition.lon
            region.aliases = list(definition.aliases)
            region.is_active = True

    # Регионы, которых больше нет в справочнике, не удаляем: на них могут
    # ссылаться исторические алерты. Просто скрываем из выдачи и фильтров.
    for code, region in existing.items():
        if code not in catalog_codes and region.is_active:
            region.is_active = False
            deactivated += 1

    await session.commit()
    if created:
        logger.info("Добавлено регионов: %s", created)
    if deactivated:
        logger.info("Деактивировано регионов вне справочника: %s", deactivated)
    return created
