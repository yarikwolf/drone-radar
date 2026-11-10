"""Тесты нормализации, классификации и геолокации."""

from __future__ import annotations

from app.ingestion.classifier import classify
from app.ingestion.geo import match_regions
from app.ingestion.keywords import detect_keywords, normalize_text


def test_normalize_strips_links_mentions_and_markup() -> None:
    text = "⚠️ СРОЧНО! @monitor https://t.me/x #тревога **Москва**"
    normalized = normalize_text(text)
    assert "http" not in normalized
    assert "@monitor" not in normalized
    assert "#" not in normalized
    assert "москва" in normalized


def test_drone_and_missile_are_mixed() -> None:
    result = classify("Угроза БПЛА и ракетный удар по региону")
    assert result.threat_type == "mixed"
    assert result.status == "active"
    assert result.priority == 4


def test_missile_alert() -> None:
    result = classify("Ракетная опасность! Крылатые ракеты в воздухе")
    assert result.threat_type == "missile"
    assert result.status == "active"


def test_generic_warning() -> None:
    result = classify("Воздушная тревога в области")
    assert result.threat_type == "unknown"
    assert result.status == "warning"


def test_all_clear_wins_over_trevoga() -> None:
    hits = detect_keywords("Отбой воздушной тревоги")
    assert hits.all_clear is True
    result = classify("Отбой воздушной тревоги")
    assert result.status == "all_clear"
    assert result.priority == 0


def test_city_vs_oblast() -> None:
    city = match_regions("Тревога в Москве")
    oblast = match_regions("Тревога в Московской области")

    assert [item.region.code for item in city] == ["RU-MOSCOW-CITY"]
    assert [item.region.code for item in oblast] == ["RU-MOSCOW"]


def test_multiple_regions_in_one_message() -> None:
    matches = match_regions("Угроза БПЛА: Белгородская область, Курская область")
    codes = {item.region.code for item in matches}
    assert "RU-BELGOROD" in codes
    assert "RU-KURSK" in codes


def test_no_region_returns_empty() -> None:
    assert match_regions("Ракетная опасность. Укрывайтесь.") == []
