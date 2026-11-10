"""Точка входа FastAPI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import alerts, regions, system
from app.config import settings
from app.db import SessionLocal, engine, init_db
from app.seed import seed_regions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auto_create_tables:
        await init_db()

    async with SessionLocal() as session:
        await seed_regions(session)

    scheduler = None
    if settings.notify_scheduler_enabled and settings.notify_configured:
        from app.workers.notifier import create_scheduler

        scheduler = create_scheduler()
        scheduler.start()
        logger.info("Notifier запущен внутри API (интервал %s c)", settings.notify_poll_seconds)

    app.state.scheduler = scheduler
    yield

    if scheduler is not None:
        scheduler.shutdown(wait=False)
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version=system.API_VERSION,
    description=(
        "Агрегация открытых сигналов о воздушных тревогах, БПЛА и ракетной опасности. "
        "Данные не являются официальным оповещением."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(alerts.router, prefix="/api")
app.include_router(regions.router, prefix="/api")
app.include_router(system.router, prefix="/api")


@app.get("/", tags=["system"], summary="Корень API")
async def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": system.API_VERSION,
        "docs": "/docs",
        "alerts": "/api/alerts",
        "health": "/api/health",
    }
