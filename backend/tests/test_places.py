"""Тесты точек назначения: район/город в тексте сообщения."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.ingestion.pipeline import IngestPayload, ingest_message
from app.models import Alert
from app.utils.time import utcnow


def _payload(text: str, message_id: str, minutes: int = 0) -> IngestPayload:
    return IngestPayload(
        text=text,
        source_kind="telegram",
        source_external_id="channel-a",
        source_name="Канал A",
        message_external_id=message_id,
        posted_at=utcnow() - timedelta(minutes=10) + timedelta(minutes=minutes),
    )


async def test_place_is_attached_to_alert(session) -> None:
    result = await ingest_message(session, _payload("Шебекино\nОпасность по БПЛА", "1"))

    assert result.created == 1
    alert = (await session.scalars(select(Alert))).one()
    assert alert.region.code == "RU-BELGOROD"
    assert [item["name"] for item in alert.places] == ["Шебекино"]
    assert alert.places[0]["lat"] > 50


async def test_district_name_maps_to_center(session) -> None:
    result = await ingest_message(
        session, _payload("Суджанский район\nОпасность по БПЛА", "2")
    )

    assert result.created == 1
    alert = (await session.scalars(select(Alert))).one()
    assert alert.region.code == "RU-KURSK"
    assert [item["name"] for item in alert.places] == ["Суджа"]


async def test_new_regions_are_recognised(session) -> None:
    cases = {
        "Геническ\nОпасность по БПЛА": ("RU-KHERSON", "Геническ"),
        "Донецк\nОпасность по БПЛА": ("RU-DNR", "Донецк"),
        "Луганск\nОпасность по БПЛА": ("RU-LNR", "Луганск"),
        "Бердянск\nОпасность по БПЛА": ("RU-ZAPORIZHIA", "Бердянск"),
    }
    for index, (text, expected) in enumerate(cases.items()):
        result = await ingest_message(session, _payload(text, f"30{index}"))
        assert result.created == 1
        alert = result.alerts[0]
        assert (alert.region.code, alert.places[0]["name"]) == expected


async def test_places_are_merged_on_duplicate(session) -> None:
    await ingest_message(session, _payload("Опасность БПЛА Белгородская область", "40"))
    await ingest_message(
        session, _payload("Опасность БПЛА Белгородская область Шебекино", "41")
    )

    alerts = (await session.scalars(select(Alert))).all()
    assert len(alerts) == 1
    names = [item["name"] for item in alerts[0].places]
    assert names == ["Шебекино"]
