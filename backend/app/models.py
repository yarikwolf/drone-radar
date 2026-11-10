"""ORM-схема: regions, sources, alerts, alert_sources."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.utils.time import utcnow


class Region(Base):
    """Справочник регионов: каноническое имя, алиасы и координаты."""

    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    region_type: Mapped[str] = mapped_column(String(24), default="region")
    country: Mapped[str] = mapped_column(String(8), default="RU")
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Source(Base):
    """Канал/лента/API, из которого приходят сообщения."""

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("kind", "external_id", name="uq_sources_kind_external"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # telegram | rss | official_api | demo
    external_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    trust: Mapped[float] = mapped_column(Float, default=0.7)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Alert(Base):
    """Событие тревоги. Одна строка = одно уникальное событие."""

    __tablename__ = "alerts"
    __table_args__ = (
        # Защита от гонки воркеров: одинаковый текст в пределах окна.
        UniqueConstraint("content_hash", "dedup_bucket", name="uq_alerts_content_bucket"),
        Index("ix_alerts_region_event", "region_id", "event_at"),
        Index("ix_alerts_status_event", "status", "event_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    region_id: Mapped[int | None] = mapped_column(
        ForeignKey("regions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    threat_type: Mapped[str] = mapped_column(String(16), index=True)  # drone | missile | mixed | unknown
    status: Mapped[str] = mapped_column(String(16), index=True)  # active | warning | all_clear | cleared
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    norm_text: Mapped[str] = mapped_column(Text)

    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    dedup_bucket: Mapped[int] = mapped_column(Integer, index=True)

    priority: Mapped[int] = mapped_column(SmallInteger, default=1)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)

    # Снимок координат на момент события: регион могут переименовать/переместить.
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Населённые пункты/районы, названные в сообщении:
    # [{"name": "Шебекино", "lat": 50.4, "lon": 36.88, "kind": "city"}]
    places: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True, default=list)

    event_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    merged_count: Mapped[int] = mapped_column(Integer, default=1)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    region: Mapped[Region | None] = relationship(lazy="selectin")
    sources: Mapped[list["AlertSource"]] = relationship(
        back_populates="alert",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class AlertSource(Base):
    """Связь алерта с конкретным сообщением источника (для доверия и аудита)."""

    __tablename__ = "alert_sources"
    __table_args__ = (
        UniqueConstraint(
            "alert_id", "source_id", "external_message_id", name="uq_alert_source_message"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    external_message_id: Mapped[str] = mapped_column(String(128))
    message_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text)
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    alert: Mapped[Alert] = relationship(back_populates="sources")
    source: Mapped[Source] = relationship(lazy="joined")
