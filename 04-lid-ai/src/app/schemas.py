"""Строгая схема ответа LLM и разбор ярлыков в числа.

Модель НЕ выставляет балл — только ярлыки. Балл считает код (scorer.py),
поэтому оценка воспроизводима и её можно проверить тестом.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

Level3 = Literal["high", "medium", "low"]
Domain3 = Literal["full", "partial", "none"]

LEVEL_VALUE = {
    "high": 1.0, "full": 1.0,
    "medium": 0.5, "partial": 0.5,
    "low": 0.0, "none": 0.0,
}

WEIGHTS = {"intent": 0.40, "domain": 0.30, "specificity": 0.20, "urgency": 0.10}


class ProfileJudgement(BaseModel):
    """Оценка одного профиля. Ярлыки + короткое обоснование."""
    intent: Level3
    domain: Domain3
    specificity: Level3
    urgency: Level3
    reason: str = Field(default="", max_length=400)

    @field_validator("reason", mode="before")
    @classmethod
    def _clean_reason(cls, v):
        return ("" if v is None else str(v)).strip()[:400]


class Extracted(BaseModel):
    city: str | None = None
    product: str | None = None
    volume: str | None = None
    budget: str | None = None
    contact: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _short(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if s.lower() in {"", "null", "none", "не указано", "неизвестно", "-"}:
            return None
        return s[:120]


class LLMAnswer(BaseModel):
    """Полный ответ классификатора: одно сообщение — все профили."""
    is_service_offer: bool = False   # автор сам предлагает услугу
    is_job_seeker: bool = False      # автор ищет работу
    lead_type: Literal["request", "question", "discussion", "none"] = "none"
    profiles: dict[str, ProfileJudgement]
    extracted: Extracted = Field(default_factory=Extracted)
    summary: str = Field(default="", max_length=300)

    @field_validator("profiles", mode="before")
    @classmethod
    def _drop_unknown(cls, v):
        if not isinstance(v, dict):
            raise ValueError("profiles должен быть объектом")
        return v

    @field_validator("summary", mode="before")
    @classmethod
    def _clean_summary(cls, v):
        return ("" if v is None else str(v)).strip()[:300]


class ParseError(ValueError):
    pass


_JSON_OBJECT = re.compile(r"\{.*\}", re.S)


def parse_answer(raw: str, expected_slugs: list[str]) -> LLMAnswer:
    """Разбирает ответ модели: снимает ```json, вырезает объект, валидирует.

    Отсутствующие профили считаются оценкой none/none/low/low (0 баллов) — так
    сообщение всё равно проверено по всем профилям, даже если модель что-то забыла.
    """
    if not raw or not raw.strip():
        raise ParseError("пустой ответ модели")
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_OBJECT.search(text)
        if not m:
            raise ParseError(f"в ответе нет JSON: {raw[:200]!r}") from None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            raise ParseError(f"битый JSON: {exc}; {raw[:200]!r}") from None

    if not isinstance(data, dict):
        raise ParseError("ожидался JSON-объект")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise ParseError("нет объекта profiles")

    filled: dict[str, dict] = {}
    for slug in expected_slugs:
        j = profiles.get(slug)
        if isinstance(j, dict):
            filled[slug] = j
        else:
            filled[slug] = {"intent": "low", "domain": "none", "specificity": "low",
                            "urgency": "low", "reason": "профиль не оценён моделью"}
    data["profiles"] = filled
    try:
        return LLMAnswer(**data)
    except ValidationError as exc:
        raise ParseError(f"ответ не соответствует схеме: {exc.errors()[:3]}") from None


def judgement_score(j: ProfileJudgement) -> float:
    """Взвешенная формула 0–10, округление half-up до целого."""
    raw = 10.0 * (
        WEIGHTS["intent"] * LEVEL_VALUE[j.intent]
        + WEIGHTS["domain"] * LEVEL_VALUE[j.domain]
        + WEIGHTS["specificity"] * LEVEL_VALUE[j.specificity]
        + WEIGHTS["urgency"] * LEVEL_VALUE[j.urgency]
    )
    return round_half_up(raw, 1)


def round_half_up(value: float, digits: int = 0) -> float:
    from decimal import ROUND_HALF_UP, Decimal
    q = Decimal("1").scaleb(-digits)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))
