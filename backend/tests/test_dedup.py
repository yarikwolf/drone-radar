"""Тесты дедупликации и жизненного цикла алертов."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.ingestion.pipeline import IngestPayload, ingest_message
from app.models import Alert, AlertSource
from app.services.dedup import jaccard, tokenize
from app.utils.time import utcnow


def test_tokenize_stems_inflections() -> None:
    assert jaccard(
        tokenize("Угроза БПЛА в Белгородской области"),
        tokenize("Белгородская область: угроза БПЛА"),
    ) == 1.0


async def test_exact_duplicate_from_two_channels(session) -> None:
    text = "Угроза БПЛА в Белгородской области"
    first = await ingest_message(
        session,
        IngestPayload(
            text=text,
            source_kind="telegram",
            source_external_id="channel-a",
            source_name="Канал A",
            message_external_id="1",
        ),
    )
    second = await ingest_message(
        session,
        IngestPayload(
            text=text,
            source_kind="telegram",
            source_external_id="channel-b",
            source_name="Канал B",
            message_external_id="1",
        ),
    )

    assert first.created == 1
    assert second.created == 0
    assert second.merged == 1

    alerts = (await session.scalars(select(Alert))).all()
    assert len(alerts) == 1
    assert alerts[0].merged_count == 2

    links = (await session.scalars(select(AlertSource))).all()
    assert len(links) == 2


async def test_fuzzy_duplicate_with_different_word_order(session) -> None:
    first = await ingest_message(
        session,
        IngestPayload(
            text="Угроза БПЛА в Белгородской области",
            source_kind="telegram",
            source_external_id="channel-a",
            source_name="Канал A",
            message_external_id="10",
        ),
    )
    second = await ingest_message(
        session,
        IngestPayload(
            text="Белгородская область: угроза БПЛА",
            source_kind="telegram",
            source_external_id="channel-b",
            source_name="Канал B",
            message_external_id="11",
        ),
    )

    assert first.created == 1
    assert second.merged == 1
    assert len((await session.scalars(select(Alert))).all()) == 1


async def test_all_clear_closes_previous_alert(session) -> None:
    await ingest_message(
        session,
        IngestPayload(
            text="Ракетная опасность в Курской области",
            source_kind="telegram",
            source_external_id="channel-a",
            source_name="Канал A",
            message_external_id="20",
        ),
    )
    await ingest_message(
        session,
        IngestPayload(
            text="Отбой тревоги в Курской области",
            source_kind="telegram",
            source_external_id="channel-a",
            source_name="Канал A",
            message_external_id="21",
        ),
    )

    alerts = (
        await session.scalars(select(Alert).order_by(Alert.id.asc()))
    ).all()
    assert len(alerts) == 2
    assert alerts[0].status == "cleared"
    assert alerts[0].cleared_at is not None
    assert alerts[1].status == "all_clear"


async def test_repeated_clear_after_new_alert_starts_new_event(session) -> None:
    """Повторный отбой не должен склеиваться с отбоем прошлого цикла.

    Иначе новая тревога останется висеть на карте, потому что отбой
    прикрепится к старой записи и никого не закроет.
    """
    base = utcnow() - timedelta(minutes=30)

    def payload(text: str, message_id: str, minutes: int) -> IngestPayload:
        return IngestPayload(
            text=text,
            source_kind="telegram",
            source_external_id="channel-a",
            source_name="Канал A",
            message_external_id=message_id,
            posted_at=base + timedelta(minutes=minutes),
        )

    await ingest_message(session, payload("Отбой тревоги в Курской области", "40", 0))
    await ingest_message(session, payload("Ракетная опасность в Курской области", "41", 10))
    result = await ingest_message(
        session, payload("Отбой тревоги в Курской области", "42", 20)
    )

    assert result.created == 1
    alerts = (await session.scalars(select(Alert).order_by(Alert.id.asc()))).all()
    assert len(alerts) == 3
    assert [alert.status for alert in alerts] == ["all_clear", "cleared", "all_clear"]
    assert alerts[1].cleared_at is not None


async def test_district_name_maps_to_region(session) -> None:
    """«Белгородский район» относится к Белгородской области."""
    result = await ingest_message(
        session,
        IngestPayload(
            text="Белгородский район\nОпасность по БПЛА",
            source_kind="telegram",
            source_external_id="channel-a",
            source_name="Канал A",
            message_external_id="50",
        ),
    )

    assert result.created == 1
    alert = (await session.scalars(select(Alert))).one()
    assert alert.region.code == "RU-BELGOROD"


async def test_irrelevant_message_is_ignored(session) -> None:
    result = await ingest_message(
        session,
        IngestPayload(
            text="Сегодня хорошая погода в Москве",
            source_kind="telegram",
            source_external_id="channel-a",
            source_name="Канал A",
            message_external_id="30",
        ),
    )
    assert result.ignored == 1
    assert result.reason == "no_keywords"
    assert (await session.scalars(select(Alert))).all() == []
