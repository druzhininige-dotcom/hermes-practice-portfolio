"""Тесты скорера: формула, капы для «сам предлагает услугу», порог, второй профиль."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.pipeline.scorer import NEW, REJECTED_AUTO, decide_status, rank
from app.profiles import load_profiles
from app.schemas import LLMAnswer, ProfileJudgement, judgement_score, parse_answer

PROFILES = load_profiles(Path(__file__).resolve().parent.parent / "profiles")
BY_SLUG = {p.slug: p for p in PROFILES}


def j(intent="low", domain="none", spec="low", urgency="low", reason=""):
    return ProfileJudgement(intent=intent, domain=domain, specificity=spec, urgency=urgency,
                            reason=reason)


def answer(profiles: dict[str, ProfileJudgement], **kw) -> LLMAnswer:
    return LLMAnswer(profiles=profiles, lead_type=kw.pop("lead_type", "request"), **kw)


def full_profiles(**overrides) -> dict[str, ProfileJudgement]:
    base = {p.slug: j() for p in PROFILES}
    base.update(overrides)
    return base


# ── формула ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("labels,expected", [
    (("high", "full", "low", "low"), 7.0),
    (("high", "partial", "low", "low"), 5.5),
    (("high", "full", "medium", "medium"), 8.5),
    (("high", "full", "high", "medium"), 9.5),
    (("high", "full", "high", "high"), 10.0),
    (("medium", "partial", "low", "low"), 3.5),
    (("low", "none", "low", "low"), 0.0),
])
def test_formula(labels, expected):
    intent, domain, spec, urgency = labels
    assert judgement_score(j(intent, domain, spec, urgency)) == expected


def test_weights_only_from_code():
    # 0.4*1 + 0.3*1 + 0.2*0 + 0.1*0 = 0.7 → 7.0
    assert judgement_score(j("high", "full", "low", "low")) == 7.0


# ── решения ────────────────────────────────────────────────────────────────

def test_best_profile_chosen():
    r = rank(answer(full_profiles(
        log=j("high", "full", "high", "medium"),
        mp=j("high", "partial", "low", "low"),
    )), PROFILES)
    assert r.is_lead and r.best.slug == "log"
    assert r.best_score == 9.5
    assert decide_status(r) == NEW


def test_service_offer_is_not_lead():
    r = rank(answer(full_profiles(log=j("high", "full", "high", "high")),
                    is_service_offer=True), PROFILES)
    assert not r.is_lead
    assert r.best is None
    assert max(s.score for s in r.scores) <= 2
    assert decide_status(r) == REJECTED_AUTO


def test_job_seeker_is_not_lead():
    r = rank(answer(full_profiles(mp=j("high", "full", "high", "high")),
                    is_job_seeker=True), PROFILES)
    assert not r.is_lead


def test_discussion_is_not_lead():
    r = rank(answer(full_profiles(mebel=j("medium", "full", "low", "low")),
                    lead_type="discussion"), PROFILES)
    assert not r.is_lead


def test_below_threshold_not_notified():
    # балл 3.5 при стартовом пороге 5 — запрос есть, но в группу не уходит
    r = rank(answer(full_profiles(mebel=j("medium", "partial", "low", "low"))), PROFILES)  # 3.5
    assert r.is_lead is True
    assert r.best_score == 3.5
    assert decide_status(r) == REJECTED_AUTO


def test_threshold_is_compared_with_raw_score():
    # 5.5 при пороге 6: сырое значение 5.5 < 6, хотя в карточке это «6» после округления
    r = rank(answer(full_profiles(mebel=j("high", "partial", "low", "low"))), PROFILES)  # 5.5
    assert r.best_score == 5.5
    assert decide_status(r, best_threshold=6.0) == REJECTED_AUTO
    assert decide_status(r, best_threshold=5.0) == NEW


def test_secondary_profile_close_scores():
    # бот для отзывов Ozon: ai 9.0, mp 7.0 — разница 2.0 ≤ 2.5
    r = rank(answer(full_profiles(
        ai=j("high", "full", "high", "low"),
        mp=j("high", "partial", "medium", "low"),
    )), PROFILES)
    assert r.best.slug == "ai"
    assert [s.slug for s in r.secondary] == ["mp"]


def test_secondary_profile_far_scores_not_shown():
    # ai 9.5, mp 6.0 — разница 3.5 > 2.5
    r = rank(answer(full_profiles(
        ai=j("high", "full", "high", "medium"),
        mp=j("high", "partial", "low", "low"),
    )), PROFILES)
    assert r.best.slug == "ai"
    assert r.secondary == []


def test_secondary_must_pass_its_threshold():
    # пороги задаём явно (6.0): mp 5.0 свой порог не прошёл → второго профиля нет
    from dataclasses import replace
    strict = [replace(p, threshold=6.0) for p in PROFILES]
    r = rank(answer(full_profiles(
        ai=j("high", "full", "high", "medium"),   # 9.5
        mp=j("medium", "full", "low", "low"),     # 5.0
    )), strict)
    assert r.best.slug == "ai"
    assert r.secondary == []


def test_all_profiles_scored_even_if_missing_in_answer():
    data = {"is_service_offer": False, "is_job_seeker": False, "lead_type": "request",
            "profiles": {"log": {"intent": "high", "domain": "full", "specificity": "high",
                                 "urgency": "low", "reason": "тест"}},
            "extracted": {}, "summary": "тест"}
    a = LLMAnswer(**data)
    # parse_answer добивает пропущенные профили нулями
    a2 = parse_answer(
        '{"is_service_offer":false,"is_job_seeker":false,"lead_type":"request",'
        '"profiles":{"log":{"intent":"high","domain":"full","specificity":"high",'
        '"urgency":"low","reason":"тест"}},"extracted":{},"summary":"тест"}',
        [p.slug for p in PROFILES])
    assert set(a2.profiles) == {"mebel", "mp", "log", "ai"}
    assert a2.profiles["mebel"].domain == "none"
    assert a.profiles["log"].intent == "high"
