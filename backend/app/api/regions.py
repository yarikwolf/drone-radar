"""Справочник регионов для фильтров и карты."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Region
from app.schemas import RegionListOut, RegionOut

router = APIRouter(prefix="/regions", tags=["regions"])


@router.get("", response_model=RegionListOut, summary="Список регионов")
async def list_regions(session: AsyncSession = Depends(get_session)) -> RegionListOut:
    regions = (
        await session.scalars(
            select(Region).where(Region.is_active.is_(True)).order_by(Region.name)
        )
    ).all()
    return RegionListOut(
        items=[
            RegionOut(
                code=item.code,
                name=item.name,
                region_type=item.region_type,
                country=item.country,
                lat=item.lat,
                lon=item.lon,
            )
            for item in regions
        ],
        total=len(regions),
    )
