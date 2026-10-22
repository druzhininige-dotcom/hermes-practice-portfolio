"""Тесты конвейера обработки сообщения на фейковом LLM (без сети)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import db
from app.config import Settings
from app.pipeline.llm import Usage
from app.pipeline.processor import IncomingMessage, async_process_message
from app.pipeline.prompt import build_system_prompt
from app.profiles import load_profiles
from app.schemas import LLMAnswer

PROFILES = load_profiles(Path(__file__).resolve().parent.parent / "profiles")


class FakeLLM:
    def __init__(self, answer: LLMAnswer | Exception):
        self.answer = answer
        self.calls = 0

    async def classify(self, system, user, slugs, **kw):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer, Usage(prompt_tokens=100, completion_tokens=50, cost_usd=0.0001)


def make_answer(**scores) -> LLMAnswer:
    from app.schemas import ProfileJudgement
    profiles = {p.slug: ProfileJudgement(intent="low", domain="none", specificity="low",
                                         urgency="low", reason="нет") for p in PROFILES}
    for slug, vals in scores.items():
        profiles[slug] = ProfileJudgement(**vals)
    return LLMAnswer(profiles=profiles, lead_type="request")


def strong(slug="ai"):
    return {slug: {"intent": "high", "domain": "full", "specificity": "high",
                   "urgency": "medium", "reason": "просит исполнителя"}}


@pytest.fixture()
def env():
    conn = db.connect(":memory:")
    ids = db.upsert_profiles(conn, PROFILES)
    settings = Settings()
    settings.dry_run = True
    settings.db_path = Path(":memory:")
    yield conn, ids, settings, build_system_prompt(PROFILES)
    conn.close()


def msg(text="Нужен телеграм-бот под ключ для приёма заявок и Битрикс24", mid=1):
    return IncomingMessage(chat_id=-100500, message_id=mid, text=text,
                           chat_title="Автоматизация", sender_username="ivan",
                           sender_name="Иван")


def run(env, answer, message=None):
    conn, ids, settings, prompt = env
    llm = FakeLLM(answer)
    return asyncio.run(async_process_message(
        message or msg(), settings=settings, profiles=PROFILES, profile_ids=ids,
        conn=conn, llm=llm, system_prompt=prompt))


def test_lead_notified_and_saved(env):
    conn, ids, _, _ = env
    res = run(env, make_answer(**strong("ai")))
    assert res.action == "notified", res.reason
    assert res.ranking.best.slug == "ai"
    lead = conn.execute("SELECT * FROM leads").fetchone()
    assert lead["profile_id"] == ids["ai"]
    assert lead["status"] == "new"
    assert lead["notify_state"] == "pending"
    assert "телеграм-бот" in lead["text"]
    n_scores = conn.execute("SELECT COUNT(*) AS n FROM lead_profile_scores").fetchone()["n"]
    assert n_scores == 4
    outbox = conn.execute("SELECT * FROM outbox").fetchone()
    assert outbox["status"] == "pending"


def test_duplicate_not_processed_twice(env):
    conn, _, _, _ = env
    first = run(env, make_answer(**strong("ai")))
    second = run(env, make_answer(**strong("ai")))
    assert first.action == "notified"
    assert second.action == "duplicate"
    assert conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"] == 1


def test_service_offer_rejected(env):
    conn, _, _, _ = env
    answer = make_answer(**strong("ai"))
    answer.is_service_offer = True
    res = run(env, answer)
    assert res.action == "rejected"
    assert conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"] == 0


def test_below_threshold_saved_but_not_notified(env):
    conn, _, _, _ = env
    answer = make_answer(mebel={"intent": "medium", "domain": "partial", "specificity": "low",
                                "urgency": "low", "reason": "интересуется"})
    res = run(env, answer, msg("Подскажите, какой ЛДСП лучше для кухни 16 или 18 мм?"))
    assert res.action == "rejected"
    row = conn.execute("SELECT * FROM leads").fetchone()
    assert row["status"] == "rejected_auto"
    assert row["notify_state"] == "skipped"
    assert conn.execute("SELECT COUNT(*) AS n FROM outbox").fetchone()["n"] == 0


def test_bot_author_skipped(env):
    conn, _, _, _ = env
    m = msg()
    m.is_bot = True
    assert run(env, make_answer(**strong("ai")), m).action == "skipped"


def test_prefilter_skip_marks_seen(env):
    conn, _, _, _ = env
    res = run(env, make_answer(**strong("ai")), msg("доброе утро"))
    assert res.action == "skipped"
    assert conn.execute("SELECT * FROM seen_messages").fetchone()["skip_reason"] == "short"


def test_llm_error_not_marked_seen(env):
    conn, _, _, _ = env
    from app.pipeline.llm import LLMError
    res = run(env, LLMError("timeout"))
    assert res.action == "error"
    assert conn.execute("SELECT COUNT(*) AS n FROM seen_messages").fetchone()["n"] == 0


def test_secondary_profile_saved_for_cross_case(env):
    conn, ids, _, _ = env
    answer = make_answer(
        ai={"intent": "high", "domain": "full", "specificity": "high", "urgency": "low",
            "reason": "бот под ключ"},
        mp={"intent": "high", "domain": "partial", "specificity": "medium", "urgency": "low",
            "reason": "отзывы Ozon"},
    )
    res = run(env, answer, msg("Нужен бот для автоответов на отзывы Ozon, 400 в месяц"))
    assert res.action == "notified"
    lead = conn.execute("SELECT * FROM leads").fetchone()
    assert lead["profile_id"] == ids["ai"]
    assert "#маркетплейсы" in lead["secondary_json"]
    second = conn.execute(
        "SELECT * FROM lead_profile_scores WHERE is_secondary=1").fetchone()
    assert second["profile_id"] == ids["mp"]
