"""Скорер: баллы, лучший профиль, второй близкий профиль, статус.

Числа считает код по формуле, модель даёт только ярлыки.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.profiles import Profile
from app.schemas import LLMAnswer, ProfileJudgement, judgement_score

# Статусы
NEW = "new"
REJECTED = "rejected"
REJECTED_AUTO = "rejected_auto"
PARSE_ERROR = "parse_error"

NEGATIVE_CAP = 2.0   # «сам предлагает услугу» / «ищу работу» / не запрос — не выше 2


@dataclass(slots=True)
class ProfileScore:
    profile: Profile
    score: float
    judgement: ProfileJudgement
    is_best: bool = False
    is_secondary: bool = False

    @property
    def slug(self) -> str:
        return self.profile.slug


@dataclass(slots=True)
class Ranking:
    scores: list[ProfileScore]
    is_lead: bool
    reason_not_lead: str = ""
    best: ProfileScore | None = None
    secondary: list[ProfileScore] = field(default_factory=list)

    def by_slug(self, slug: str) -> ProfileScore | None:
        for s in self.scores:
            if s.slug == slug:
                return s
        return None

    @property
    def best_score(self) -> float:
        return self.best.score if self.best else 0.0

    def as_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "slug": s.slug,
                "score": s.score,
                "intent": s.judgement.intent,
                "domain": s.judgement.domain,
                "specificity": s.judgement.specificity,
                "urgency": s.judgement.urgency,
                "is_best": s.is_best,
                "is_secondary": s.is_secondary,
                "reason": s.judgement.reason,
            }
            for s in self.scores
        ]


def rank(answer: LLMAnswer, profiles: list[Profile], secondary_max_gap: float = 2.5) -> Ranking:
    """Считает баллы по всем профилям, выбирает лучший и близкий второй.

    is_lead означает «это запрос исполнителя» (а не «прошёл порог»): сообщения
    ниже порога тоже сохраняются в БД как rejected_auto — их видно в статистике.
    """
    scored: list[ProfileScore] = []
    for p in profiles:
        j = answer.profiles.get(p.slug) or ProfileJudgement(
            intent="low", domain="none", specificity="low", urgency="low", reason="нет оценки")
        score = judgement_score(j)
        scored.append(ProfileScore(profile=p, score=score, judgement=j))

    not_lead_reason = ""
    if answer.is_service_offer:
        not_lead_reason = "автор сам предлагает услугу"
    elif answer.is_job_seeker:
        not_lead_reason = "автор ищет работу"
    elif answer.lead_type not in ("request", "question"):
        not_lead_reason = "не запрос исполнителя"

    if not_lead_reason:
        for s in scored:
            s.score = min(s.score, NEGATIVE_CAP)
        return Ranking(scores=_sorted(scored), is_lead=False, reason_not_lead=not_lead_reason)

    ordered = _sorted(scored)
    best = ordered[0]
    best.is_best = True

    secondaries: list[ProfileScore] = []
    # второй профиль показываем только если лучший прошёл свой порог
    if best.score >= best.profile.threshold:
        for s in ordered[1:]:
            if s.score >= s.profile.threshold and (best.score - s.score) <= secondary_max_gap:
                s.is_secondary = True
                secondaries.append(s)

    return Ranking(scores=ordered, is_lead=True, best=best, secondary=secondaries)


def _sorted(scores: list[ProfileScore]) -> list[ProfileScore]:
    """Сортировка по баллу; при равенстве — по порядку профилей (стабильно)."""
    order = {s.slug: i for i, s in enumerate(scores)}
    return sorted(scores, key=lambda s: (-s.score, order[s.slug]))


def decide_status(ranking: Ranking, best_threshold: float | None = None) -> str:
    """new — уходит в группу; rejected_auto — сохранили, но не уведомляем."""
    if not ranking.is_lead:
        return REJECTED_AUTO
    threshold = best_threshold if best_threshold is not None else (
        ranking.best.profile.threshold if ranking.best else 6.0)
    if ranking.best and ranking.best.score >= threshold:
        return NEW
    return REJECTED_AUTO
