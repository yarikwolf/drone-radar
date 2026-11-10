"""Классификация угрозы по ключевым словам."""

from __future__ import annotations

from dataclasses import dataclass

from app.ingestion.keywords import KeywordHits, detect_keywords

THREAT_LABELS = {
    "drone": "Угроза БПЛА",
    "missile": "Ракетная опасность",
    "mixed": "Смешанная атака",
    "unknown": "Воздушная тревога",
}

STATUS_LABELS = {
    "active": "Активная угроза",
    "warning": "Предупреждение",
    "all_clear": "Отбой",
    "cleared": "Завершено",
}


@dataclass(frozen=True, slots=True)
class Classification:
    threat_type: str
    status: str
    priority: int
    confidence: float


def classify_hits(hits: KeywordHits) -> Classification:
    """Приоритет: отбой > смешанная > ракета > БПЛА > общая тревога."""
    score = 0.62 + 0.08 * sum((hits.all_clear, hits.missile, hits.drone, hits.warning))
    confidence = round(min(score, 0.95), 2)

    if hits.all_clear:
        return Classification("unknown", "all_clear", 0, max(confidence, 0.85))
    if hits.missile and hits.drone:
        return Classification("mixed", "active", 4, confidence)
    if hits.missile:
        return Classification("missile", "active", 3, confidence)
    if hits.drone:
        return Classification("drone", "active", 2, confidence)
    return Classification("unknown", "warning", 1, confidence)


def classify(text: str) -> Classification:
    return classify_hits(detect_keywords(text))


def title_for(classification: Classification, region_name: str | None) -> str:
    place = region_name or "регион не определён"
    if classification.status == "all_clear":
        return f"Отбой тревоги — {place}"
    if classification.status == "warning":
        return f"Воздушная тревога — {place}"
    return f"{THREAT_LABELS.get(classification.threat_type, 'Тревога')} — {place}"
