"""Тесты схемы БД: мультиклиентность, дедуп, оценки профилей."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import db
from app.profiles import load_profiles

PROFILES = load_profiles(Path(__file__).resolve().parent.parent / "profiles")
BY_SLUG = {p.slug: p for p in PROFILES}


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_leads_have_profile_id(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
    assert "profile_id" in cols
    assert "score" in cols and "status" in cols and "secondary_json" in cols


def test_scores_table_links_lead_and_profile(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(lead_profile_scores)")}
    assert {"lead_id", "profile_id", "score", "is_best", "is_secondary"} <= cols


def test_profiles_upsert_is_idempotent(conn):
    ids1 = db.upsert_profiles(conn, PROFILES)
    ids2 = db.upsert_profiles(conn, PROFILES)
    assert ids1 == ids2
    assert len(db.profile_rows(conn)) == 4
    row = conn.execute("SELECT * FROM profiles WHERE slug='log'").fetchone()
    assert row["threshold"] == BY_SLUG["log"].threshold
    assert "китая" in row["keywords_json"]


def test_save_lead_and_scores(conn):
    ids = db.upsert_profiles(conn, PROFILES)
    lead = {
        "profile_id": ids["ai"], "chat_id": -100500, "message_id": 7,
        "text": "нужен бот", "score": 9.0, "chat_title": "Чат", "message_link": "",
        "extracted": {"city": "Москва"}, "secondary": [{"slug": "mp", "score": 7.0}],
        "status": "new", "prompt_version": "ai-v1", "llm_model": "deepseek-chat",
    }
    scores = [
        {"profile_id": ids["ai"], "score": 9.0, "is_best": True, "reason": "бот"},
        {"profile_id": ids["mp"], "score": 7.0, "is_secondary": True, "reason": "кабинет"},
        {"profile_id": ids["log"], "score": 0.0},
        {"profile_id": ids["mebel"], "score": 0.0},
    ]
    lead_id = db.save_lead(conn, lead, scores)
    assert lead_id
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    assert row["profile_id"] == ids["ai"]
    assert row["score"] == 9.0
    saved = conn.execute(
        "SELECT COUNT(*) AS n FROM lead_profile_scores WHERE lead_id=?", (lead_id,)).fetchone()
    assert saved["n"] == 4


def test_same_message_saved_once(conn):
    ids = db.upsert_profiles(conn, PROFILES)
    lead = {"profile_id": ids["log"], "chat_id": -100, "message_id": 1, "text": "x",
            "score": 7.0}
    assert db.save_lead(conn, lead, []) is not None
    assert db.save_lead(conn, lead, []) is None
    n = conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"]
    assert n == 1


def test_seen_messages_dedup(conn):
    assert not db.is_seen(conn, -100, 5)
    db.mark_seen(conn, -100, 5, "hash1", skip_reason="short")
    assert db.is_seen(conn, -100, 5)
    db.mark_seen(conn, -100, 5, "hash2")  # повтор не перезаписывает
    row = conn.execute("SELECT * FROM seen_messages WHERE chat_id=-100 AND message_id=5").fetchone()
    assert row["text_hash"] == "hash1"


def test_chats_linked_to_profiles(conn):
    ids = db.upsert_profiles(conn, PROFILES)
    cid = db.upsert_chat(conn, -1001234567890, "Мебельщики", "mebel_chat", "group")
    db.link_chat(conn, ids["mebel"], cid)
    db.link_chat(conn, ids["mp"], cid)
    chats = db.active_chats(conn)
    assert len(chats) == 1
    assert set(chats[0]["profiles"]) == {"mebel", "mp"}


def test_outbox_and_llm_cost(conn):
    ids = db.upsert_profiles(conn, PROFILES)
    lead_id = db.save_lead(conn, {"profile_id": ids["mebel"], "chat_id": -1, "message_id": 2,
                                  "text": "t", "score": 6.0}, [])
    db.enqueue_outbox(conn, lead_id, {"lead_id": lead_id, "profile_slug": "mebel"})
    row = conn.execute("SELECT * FROM outbox WHERE lead_id=?", (lead_id,)).fetchone()
    assert row["status"] == "pending"

    db.log_llm_call(conn, model="deepseek-chat", prompt_tokens=1000, completion_tokens=200,
                    cost_usd=0.0005, latency_ms=900, ok=True)
    assert db.cost_today(conn) == pytest.approx(0.0005)


def test_status_values_allowed(conn):
    """Статусы не ограничены CHECK, но набор должен быть известен коду."""
    from app.pipeline import scorer
    assert {scorer.NEW, scorer.REJECTED, scorer.REJECTED_AUTO, scorer.PARSE_ERROR} == {
        "new", "rejected", "rejected_auto", "parse_error"}
