"""Предфильтр без LLM: отсекаем болтовню, оставляем всё, что похоже на запрос.

Принцип: лучше пропустить лишнее сообщение (стоит доли копейки), чем потерять
лид. Поэтому фильтр «мягкий»: пропускаем, если есть ключевое слово ЛЮБОГО
профиля ИЛИ вопросительно-поисковый триггер («кто может», «ищу», «нужен»).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.profiles import Profile

MIN_LEN = 18
MAX_LEN = 4000

# Триггеры запроса исполнителя (после нормализации, без ё)
TRIGGERS = (
    "кто может", "кто занимается", "кто делает", "кто поможет", "кто возьмет",
    "кто возьмёт", "кто ведет", "кто ведёт", "кто подскажет", "кто посчитает",
    "кто нарисует", "кто рассчитает", "кто сделает",
    "ищу ", "ищем ", "ищет ", "нужен ", "нужна ", "нужно ", "нужны ",
    "подскажите", "посоветуйте", "порекомендуйте", "где найти", "где заказать",
    "требуется ", "кто-нибудь", "кто нибудь", "есть кто", "посоветуйте",
    "help", "нужна помощь", "нужен человек", "нужен специалист",
)

# Жёсткие маркеры болтовни — не лид ни по одному профилю
NOISE = (
    "доброе утро", "всем привет", "спасибо", "благодарю", "ахаха", "+1",
    "подписался", "с днем рождения",
)

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s#@+\-]")
_TOKEN = re.compile(r"[\w\-]+", re.UNICODE)


def normalize(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:32]


def _root(word: str) -> str:
    """Грубая основа слова: для коротких — точное совпадение, для длинных — 4 буквы."""
    w = word.strip("-")
    if len(w) <= 4:
        return w
    return w[:4]


def _keyword_hit(norm_text: str, keyword: str) -> bool:
    """Ключ считается найденным, если в тексте есть ВСЕ его значимые слова.

    Слова короче 4 символов («3d», «вэд», «gpt», «1688») должны совпасть точно —
    иначе «3d проект» срабатывал бы на любое слово «проект».
    """
    kw = normalize(keyword)
    if not kw:
        return False
    if kw in norm_text:
        return True
    tokens = _TOKEN.findall(norm_text)
    words = [w for w in _TOKEN.findall(kw) if w]
    if not words:
        return False
    for word in words:
        if len(word) <= 4:
            if word not in tokens:
                return False
        else:
            root = _root(word)
            if not any(t.startswith(root) for t in tokens):
                return False
    return True


@dataclass(slots=True)
class PrefilterResult:
    passed: bool
    reason: str = ""
    hits: list[str] = field(default_factory=list)       # slug профилей с ключевым попаданием
    has_trigger: bool = False

    @property
    def text_hash(self) -> str:
        return ""


def prefilter(text: str, profiles: list[Profile]) -> PrefilterResult:
    norm = normalize(text)
    if len(norm) < MIN_LEN:
        return PrefilterResult(False, "short")
    if len(text) > MAX_LEN:
        return PrefilterResult(False, "too_long")
    if any(n in norm for n in NOISE) and len(norm) < 60:
        return PrefilterResult(False, "noise")

    hits: list[str] = []
    for p in profiles:
        if any(_keyword_hit(norm, kw) for kw in p.keywords):
            hits.append(p.slug)

    has_trigger = any(t in norm or norm.startswith(t.strip()) for t in TRIGGERS)

    # Стоп-слова профиля блокируют только этот профиль и только без ключевого попадания
    if hits:
        blocked = set()
        for p in profiles:
            if p.slug in hits and any(s in norm for s in p.stopwords):
                # стоп-слово есть, но ключевое тоже — отдаём модели, она разберёт контекст
                continue
        hits = [h for h in hits if h not in blocked]

    if hits:
        return PrefilterResult(True, "keyword", hits, has_trigger)
    if has_trigger:
        return PrefilterResult(True, "trigger", [], True)
    return PrefilterResult(False, "no_keyword_no_trigger")
