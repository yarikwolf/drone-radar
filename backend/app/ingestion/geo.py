"""Извлечение региона/города из текста по словарю алиасов.

Словарь лежит в app/data/regions.json и используется одновременно
для NLP и для сидирования таблицы regions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.ingestion.keywords import normalize_text

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "regions.json"
PLACES_PATH = Path(__file__).resolve().parent.parent / "data" / "places.json"


@dataclass(frozen=True, slots=True)
class RegionDef:
    code: str
    name: str
    region_type: str
    country: str
    lat: float
    lon: float
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GeoMatch:
    region: RegionDef
    alias: str


def _load_catalog() -> tuple[RegionDef, ...]:
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    catalog: list[RegionDef] = []
    for item in raw:
        catalog.append(
            RegionDef(
                code=item["code"],
                name=item["name"],
                region_type=item.get("region_type", "region"),
                country=item.get("country", "RU"),
                lat=float(item["lat"]),
                lon=float(item["lon"]),
                aliases=tuple(dict.fromkeys(item.get("aliases", []))),
            )
        )
    return tuple(catalog)


REGION_CATALOG: tuple[RegionDef, ...] = _load_catalog()
REGION_BY_CODE: dict[str, RegionDef] = {region.code: region for region in REGION_CATALOG}


@lru_cache(maxsize=1)
def _alias_patterns() -> tuple[tuple[str, RegionDef, re.Pattern[str]], ...]:
    """Компилирует алиасы один раз. Длинные — первыми, чтобы «область» была точнее."""
    compiled: list[tuple[str, RegionDef, re.Pattern[str]]] = []
    seen: set[tuple[str, str]] = set()
    for region in REGION_CATALOG:
        for raw_alias in region.aliases:
            alias = normalize_text(raw_alias)
            if not alias or (alias, region.code) in seen:
                continue
            seen.add((alias, region.code))
            pattern = re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)", re.UNICODE)
            compiled.append((alias, region, pattern))
    compiled.sort(key=lambda item: len(item[0]), reverse=True)
    return tuple(compiled)


def match_regions(text: str, limit: int = 4) -> list[GeoMatch]:
    """Возвращает найденные регионы в порядке появления (не больше limit)."""
    value = normalize_text(text)
    if not value:
        return []

    found: list[GeoMatch] = []
    seen_codes: set[str] = set()
    for alias, region, pattern in _alias_patterns():
        if region.code in seen_codes:
            continue
        if pattern.search(value):
            found.append(GeoMatch(region=region, alias=alias))
            seen_codes.add(region.code)
            if len(found) >= limit:
                break
    return found


@dataclass(frozen=True, slots=True)
class PlaceDef:
    """Населённый пункт или районный центр внутри региона."""

    name: str
    region_code: str
    lat: float
    lon: float
    kind: str  # city | settlement | district
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlaceMatch:
    place: PlaceDef
    alias: str


def _load_places() -> dict[str, tuple[PlaceDef, ...]]:
    if not PLACES_PATH.exists():
        return {}
    raw = json.loads(PLACES_PATH.read_text(encoding="utf-8"))
    grouped: dict[str, list[PlaceDef]] = {}
    for item in raw:
        code = item.get("region")
        if not code:
            continue
        grouped.setdefault(code, []).append(
            PlaceDef(
                name=item["name"],
                region_code=code,
                lat=float(item["lat"]),
                lon=float(item["lon"]),
                kind=item.get("kind", "settlement"),
                aliases=tuple(dict.fromkeys(item.get("aliases", []))),
            )
        )
    return {code: tuple(items) for code, items in grouped.items()}


PLACES_BY_REGION: dict[str, tuple[PlaceDef, ...]] = _load_places()


def _place_forms(name: str, aliases: tuple[str, ...]) -> tuple[str, ...]:
    """Склоняемые формы названия: «Шебекино», «в Шебекине», «Суджанский».

    Полноценная морфология здесь избыточна: для карты достаточно
    номинатива и типовых падежных окончаний.
    """
    base = normalize_text(name)
    forms: set[str] = set()
    if base:
        forms.add(base)
    for alias in aliases:
        value = normalize_text(alias)
        if value:
            forms.add(value)

    if not base:
        return tuple(forms)

    last = base[-1]
    if last in "ая":
        stem = base[:-1]
        for ending in ("ы", "и", "е", "у", "ю", "ой", "ей"):
            forms.add(stem + ending)
    elif last not in "еёиоуыэюяйьъ-":
        for ending in ("а", "у", "ом", "ем", "е", "ы", "и"):
            forms.add(base + ending)

    return tuple(sorted(forms, key=len, reverse=True))


@lru_cache(maxsize=None)
def _place_patterns(region_code: str) -> tuple[tuple[PlaceDef, re.Pattern[str]], ...]:
    compiled: list[tuple[PlaceDef, re.Pattern[str]]] = []
    for place in PLACES_BY_REGION.get(region_code, ()):
        forms = _place_forms(place.name, place.aliases)
        if not forms:
            continue
        alternatives = "|".join(re.escape(form) for form in forms)
        pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.UNICODE)
        compiled.append((place, pattern))
    # Длинные названия раньше: «Красный Луч» должен победить «Луч».
    compiled.sort(key=lambda item: (-len(item[0].name), item[0].name))
    return tuple(compiled)


def match_places(
    text: str, region_codes: list[str] | tuple[str, ...], limit: int = 6
) -> list[PlaceMatch]:
    """Ищет населённые пункты только среди указанных регионов.

    Ограничение по регионам снимает неоднозначность: «Донецк» в Ростовской
    области и «Донецк» в ДНР — разные точки.
    """
    value = normalize_text(text)
    if not value or not region_codes:
        return []

    found: list[PlaceMatch] = []
    seen: set[tuple[str, str]] = set()
    for code in dict.fromkeys(region_codes):
        hits: list[tuple[int, int, PlaceDef, str]] = []
        for place, pattern in _place_patterns(code):
            match = pattern.search(value)
            if match is None:
                continue
            hits.append((match.start(), len(match.group(0)), place, match.group(0)))

        hits.sort(key=lambda item: (item[0], -item[1]))
        used_spans: list[tuple[int, int]] = []
        taken = 0
        for start, length, place, alias in hits:
            key = (code, place.name)
            if key in seen:
                continue
            end = start + length
            if any(start < span_end and end > span_start for span_start, span_end in used_spans):
                continue
            seen.add(key)
            used_spans.append((start, end))
            found.append(PlaceMatch(place=place, alias=alias))
            taken += 1
            if taken >= 5:
                break

    found.sort(key=lambda item: item.place.name)
    return found[:limit]


def match_places_anywhere(text: str, limit: int = 6) -> list[PlaceMatch]:
    """Ищет населённые пункты по всей стране (для сообщений без региона)."""
    return match_places(text, tuple(PLACES_BY_REGION.keys()), limit)
