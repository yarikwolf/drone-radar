"""Нормализация текста и словарь ключевых слов.

Осознанно используется regex, а не тяжёлая модель:
- работает мгновенно и без GPU;
- решения объяснимы (видно, какое слово сработало);
- легко расширять под новые формулировки.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

URL_RE = re.compile(r"(?:https?://|www\.)\S+|t\.me/\S+", re.IGNORECASE)
MENTION_RE = re.compile(r"@\w+")
NOISE_RE = re.compile(
    r"(?:подписывайтесь|подписаться|наш\s+канал|источник\s*:|читать\s+далее)[^.!?\n]*",
    re.IGNORECASE,
)
MARKUP_RE = re.compile(r"[#*_`>|]+")
PUNCT_RE = re.compile(r"[^\w\s-]", re.UNICODE)
SPACE_RE = re.compile(r"\s+")

# --- Ключевые слова ---
ALL_CLEAR_RE = re.compile(
    r"(отбо[йяе]|отмен\w*\s+(?:воздушн\w*\s+)?тревог\w*|угроза\s+миновала|"
    r"опасность\s+прошла|все\s+чисто|конец\s+тревог\w*)",
    re.IGNORECASE,
)
MISSILE_RE = re.compile(
    r"(ракет\w*|крылат\w*|баллистик\w*|кинжал\w*|калибр\w*|искандер\w*|"
    r"циркон\w*|с-?\s?300|с-?\s?400|х-?\s?101|х-?\s?555)",
    re.IGNORECASE,
)
DRONE_RE = re.compile(
    r"(\bбпла\b|беспилот\w*|\bдрон\w*|шахед\w*|герань\w*|гербера\w*|"
    r"\buav\b|\bfpv\b|камикадзе|мавик\w*)",
    re.IGNORECASE,
)
WARNING_RE = re.compile(
    r"(тревог\w*|угроз\w*|опасност\w*|предупрежд\w*|сирен\w*|воздушн\w*)",
    re.IGNORECASE,
)

STOPWORDS = frozenset(
    {
        "это", "как", "так", "что", "для", "при", "над", "под", "без", "или", "либо",
        "уже", "еще", "ещё", "был", "была", "были", "быть", "есть", "нет", "все", "всё",
        "они", "она", "оно", "его", "ее", "её", "их", "мы", "вы", "вас", "нам", "вам",
        "по", "на", "в", "во", "с", "со", "к", "ко", "из", "от", "до", "за", "не", "ни",
        "и", "а", "но", "же", "ли", "бы", "то", "об", "обо", "про", "через", "после",
        "также", "только", "очень", "может", "можно", "нужно", "сейчас", "сегодня",
        "сообщает", "сообщение", "канал", "источник", "срочно", "внимание",
    }
)


def normalize_text(text: str) -> str:
    """Канонический вид текста дляkeywords, геолокации и хеша."""
    value = (text or "").replace("ё", "е").replace("Ё", "Е")
    value = URL_RE.sub(" ", value)
    value = MENTION_RE.sub(" ", value)
    value = NOISE_RE.sub(" ", value)
    value = MARKUP_RE.sub(" ", value)
    value = value.replace("_", " ")
    value = PUNCT_RE.sub(" ", value)
    value = SPACE_RE.sub(" ", value)
    return value.strip().lower()


@dataclass(frozen=True, slots=True)
class KeywordHits:
    all_clear: bool
    missile: bool
    drone: bool
    warning: bool

    @property
    def relevant(self) -> bool:
        return self.all_clear or self.missile or self.drone or self.warning


def detect_keywords(text: str) -> KeywordHits:
    """Ищет категории угроз. Ожидает уже нормализованный текст (или нормализует сам)."""
    value = normalize_text(text)
    return KeywordHits(
        all_clear=bool(ALL_CLEAR_RE.search(value)),
        missile=bool(MISSILE_RE.search(value)),
        drone=bool(DRONE_RE.search(value)),
        warning=bool(WARNING_RE.search(value)),
    )
