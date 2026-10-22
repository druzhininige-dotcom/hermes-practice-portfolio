"""Схема БД и доступ к ней.

Мультиклиентность заложена с этапа 1:
  profiles          — «клиент» = профиль услуг (mebel / mp / log / ai), свой промт,
                      ключевые слова, порог, группа и топик;
  profile_chats     — какие чаты слушает каждый профиль;
  leads.profile_id  — профиль, по которому лид признан лучшим (карточка);
  lead_profile_scores — оценки ВСЕХ профилей по каждому сообщению.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    tag             TEXT NOT NULL,
    services        TEXT NOT NULL,
    looking_for     TEXT NOT NULL,
    not_lead        TEXT NOT NULL DEFAULT '',
    keywords_json   TEXT NOT NULL DEFAULT '[]',
    weak_keywords_json TEXT NOT NULL DEFAULT '[]',
    stopwords_json  TEXT NOT NULL DEFAULT '[]',
    threshold       REAL NOT NULL DEFAULT 6.0,
    tg_group_id     INTEGER NOT NULL DEFAULT 0,
    tg_topic_id     INTEGER NOT NULL DEFAULT 0,
    prompt_version  TEXT NOT NULL DEFAULT 'v1',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_chat_id   INTEGER NOT NULL UNIQUE,
    title        TEXT NOT NULL DEFAULT '',
    username     TEXT,
    chat_type    TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS profile_chats (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    weight     REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (profile_id, chat_id)
);

CREATE TABLE IF NOT EXISTS seen_messages (
    chat_id        INTEGER NOT NULL,
    message_id     INTEGER NOT NULL,
    text_hash      TEXT NOT NULL,
    profile_hits   TEXT NOT NULL DEFAULT '[]',
    skip_reason    TEXT,
    processed_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS leads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id     INTEGER NOT NULL REFERENCES profiles(id),
    chat_id        INTEGER NOT NULL,
    chat_title     TEXT NOT NULL DEFAULT '',
    message_id     INTEGER NOT NULL,
    message_link   TEXT NOT NULL DEFAULT '',
    sender_id      INTEGER,
    sender_username TEXT,
    sender_name    TEXT,
    text           TEXT NOT NULL,
    score          REAL NOT NULL,
    is_lead        INTEGER NOT NULL DEFAULT 1,
    lead_type      TEXT NOT NULL DEFAULT 'none',
    intent         TEXT,
    domain         TEXT,
    specificity    TEXT,
    urgency        TEXT,
    extracted_json TEXT NOT NULL DEFAULT '{}',
    analysis       TEXT NOT NULL DEFAULT '',
    secondary_json TEXT NOT NULL DEFAULT '[]',
    status         TEXT NOT NULL DEFAULT 'new',
    prompt_version TEXT NOT NULL DEFAULT '',
    llm_model      TEXT NOT NULL DEFAULT '',
    notify_state   TEXT NOT NULL DEFAULT 'pending',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS lead_profile_scores (
    lead_id     INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    profile_id  INTEGER NOT NULL REFERENCES profiles(id),
    score       REAL NOT NULL,
    intent      TEXT,
    domain      TEXT,
    specificity TEXT,
    urgency     TEXT,
    is_best     INTEGER NOT NULL DEFAULT 0,
    is_secondary INTEGER NOT NULL DEFAULT 0,
    reason      TEXT,
    PRIMARY KEY (lead_id, profile_id)
);

CREATE TABLE IF NOT EXISTS lead_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    event       TEXT NOT NULL,
    old_status  TEXT,
    new_status  TEXT,
    actor_tg_id INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT 'lead_card',
    payload_json TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    next_try_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at     TEXT
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id        INTEGER,
    chat_id           INTEGER,
    message_id        INTEGER,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL NOT NULL DEFAULT 0,
    latency_ms        INTEGER NOT NULL DEFAULT 0,
    ok                INTEGER NOT NULL DEFAULT 1,
    error             TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_leads_status      ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_created     ON leads(created_at);
CREATE INDEX IF NOT EXISTS idx_leads_profile     ON leads(profile_id, created_at);
CREATE INDEX IF NOT EXISTS idx_leads_chat        ON leads(chat_id);
CREATE INDEX IF NOT EXISTS idx_outbox_due        ON outbox(status, next_try_at);
CREATE INDEX IF NOT EXISTS idx_llm_calls_created ON llm_calls(created_at);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


# ── профили ────────────────────────────────────────────────────────────────

def upsert_profiles(conn: sqlite3.Connection, profiles: Iterable[Any]) -> dict[str, int]:
    """Синхронизирует таблицу profiles с YAML. Возвращает slug -> id."""
    ids: dict[str, int] = {}
    for p in profiles:
        conn.execute(
            """
            INSERT INTO profiles (slug, name, tag, services, looking_for, not_lead,
                                  keywords_json, weak_keywords_json, stopwords_json,
                                  threshold, tg_group_id, tg_topic_id, prompt_version, is_active)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(slug) DO UPDATE SET
                name=excluded.name, tag=excluded.tag, services=excluded.services,
                looking_for=excluded.looking_for, not_lead=excluded.not_lead,
                keywords_json=excluded.keywords_json,
                weak_keywords_json=excluded.weak_keywords_json,
                stopwords_json=excluded.stopwords_json, threshold=excluded.threshold,
                tg_group_id=excluded.tg_group_id, tg_topic_id=excluded.tg_topic_id,
                prompt_version=excluded.prompt_version, is_active=excluded.is_active,
                updated_at=datetime('now')
            """,
            (
                p.slug, p.name, p.tag, p.services, p.looking_for, p.not_lead,
                json.dumps(p.keywords, ensure_ascii=False),
                json.dumps(p.weak_keywords, ensure_ascii=False),
                json.dumps(p.stopwords, ensure_ascii=False),
                p.threshold, p.tg_group_id, p.tg_topic_id, p.prompt_version, int(p.is_active),
            ),
        )
        row = conn.execute("SELECT id FROM profiles WHERE slug=?", (p.slug,)).fetchone()
        ids[p.slug] = int(row["id"])
    return ids


def profile_rows(conn: sqlite3.Connection, active_only: bool = True) -> list[sqlite3.Row]:
    sql = "SELECT * FROM profiles"
    if active_only:
        sql += " WHERE is_active=1"
    return list(conn.execute(sql + " ORDER BY id").fetchall())


# ── чаты ───────────────────────────────────────────────────────────────────

def upsert_chat(conn: sqlite3.Connection, tg_chat_id: int, title: str = "",
                username: str | None = None, chat_type: str | None = None) -> int:
    conn.execute(
        """INSERT INTO chats (tg_chat_id, title, username, chat_type)
           VALUES (?,?,?,?)
           ON CONFLICT(tg_chat_id) DO UPDATE SET
             title=CASE WHEN excluded.title<>'' THEN excluded.title ELSE chats.title END,
             username=COALESCE(excluded.username, chats.username),
             chat_type=COALESCE(excluded.chat_type, chats.chat_type)""",
        (tg_chat_id, title, username, chat_type),
    )
    row = conn.execute("SELECT id FROM chats WHERE tg_chat_id=?", (tg_chat_id,)).fetchone()
    return int(row["id"])


def link_chat(conn: sqlite3.Connection, profile_id: int, chat_id: int, weight: float = 1.0) -> None:
    conn.execute(
        """INSERT INTO profile_chats (profile_id, chat_id, weight) VALUES (?,?,?)
           ON CONFLICT(profile_id, chat_id) DO UPDATE SET weight=excluded.weight""",
        (profile_id, chat_id, weight),
    )


def active_chats(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Чаты, которые слушаем, с профилями, которым они принадлежат."""
    rows = conn.execute(
        """SELECT c.tg_chat_id, c.title, c.username, c.chat_type,
                  GROUP_CONCAT(p.slug) AS profiles
           FROM chats c
           JOIN profile_chats pc ON pc.chat_id = c.id
           JOIN profiles p ON p.id = pc.profile_id AND p.is_active = 1
           WHERE c.is_active = 1
           GROUP BY c.id"""
    ).fetchall()
    return [dict(r, profiles=(r["profiles"] or "").split(",")) for r in rows]


# ── дедуп ──────────────────────────────────────────────────────────────────

def is_seen(conn: sqlite3.Connection, chat_id: int, message_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM seen_messages WHERE chat_id=? AND message_id=?", (chat_id, message_id)
    ).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, chat_id: int, message_id: int, text_hash: str,
              profile_hits: list[str] | None = None, skip_reason: str | None = None) -> None:
    conn.execute(
        """INSERT INTO seen_messages (chat_id, message_id, text_hash, profile_hits, skip_reason)
           VALUES (?,?,?,?,?)
           ON CONFLICT(chat_id, message_id) DO NOTHING""",
        (chat_id, message_id, text_hash, json.dumps(profile_hits or [], ensure_ascii=False),
         skip_reason),
    )


# ── лиды ───────────────────────────────────────────────────────────────────

def save_lead(conn: sqlite3.Connection, lead: dict[str, Any], scores: list[dict[str, Any]]) -> int | None:
    """Сохраняет лид и оценки профилей. Возвращает id или None, если уже был."""
    existing = conn.execute(
        "SELECT id FROM leads WHERE chat_id=? AND message_id=?",
        (lead["chat_id"], lead["message_id"]),
    ).fetchone()
    if existing:
        return None
    cur = conn.execute(
        """INSERT INTO leads (profile_id, chat_id, chat_title, message_id, message_link,
                              sender_id, sender_username, sender_name, text, score, is_lead,
                              lead_type, intent, domain, specificity, urgency,
                              extracted_json, analysis, secondary_json, status,
                              prompt_version, llm_model, notify_state)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            lead["profile_id"], lead["chat_id"], lead.get("chat_title", ""), lead["message_id"],
            lead.get("message_link", ""), lead.get("sender_id"), lead.get("sender_username"),
            lead.get("sender_name"), lead["text"], lead["score"], int(lead.get("is_lead", True)),
            lead.get("lead_type", "none"), lead.get("intent"), lead.get("domain"),
            lead.get("specificity"), lead.get("urgency"),
            json.dumps(lead.get("extracted", {}), ensure_ascii=False),
            lead.get("analysis", ""), json.dumps(lead.get("secondary", []), ensure_ascii=False),
            lead.get("status", "new"), lead.get("prompt_version", ""), lead.get("llm_model", ""),
            lead.get("notify_state", "pending"),
        ),
    )
    lead_id = int(cur.lastrowid)
    for s in scores:
        conn.execute(
            """INSERT INTO lead_profile_scores (lead_id, profile_id, score, intent, domain,
                   specificity, urgency, is_best, is_secondary, reason)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (lead_id, s["profile_id"], s["score"], s.get("intent"), s.get("domain"),
             s.get("specificity"), s.get("urgency"), int(s.get("is_best", False)),
             int(s.get("is_secondary", False)), s.get("reason", "")),
        )
    return lead_id


def enqueue_outbox(conn: sqlite3.Connection, lead_id: int, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO outbox (lead_id, kind, payload_json) VALUES (?, 'lead_card', ?)",
        (lead_id, json.dumps(payload, ensure_ascii=False)),
    )


def set_notify_state(conn: sqlite3.Connection, lead_id: int, state: str) -> None:
    conn.execute("UPDATE leads SET notify_state=?, updated_at=datetime('now') WHERE id=?",
                 (state, lead_id))


def log_llm_call(conn: sqlite3.Connection, *, model: str, prompt_tokens: int, completion_tokens: int,
                 cost_usd: float, latency_ms: int, ok: bool, error: str | None = None,
                 chat_id: int | None = None, message_id: int | None = None,
                 profile_id: int | None = None) -> None:
    conn.execute(
        """INSERT INTO llm_calls (profile_id, chat_id, message_id, model, prompt_tokens,
                                  completion_tokens, cost_usd, latency_ms, ok, error)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (profile_id, chat_id, message_id, model, prompt_tokens, completion_tokens,
         cost_usd, latency_ms, int(ok), error),
    )


def cost_today(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd),0) AS s FROM llm_calls WHERE date(created_at)=date('now')"
    ).fetchone()
    return float(row["s"] or 0.0)


def leads_today(conn: sqlite3.Connection, tz_offset_hours: int = 10) -> list[sqlite3.Row]:
    """Лиды за сегодня по Владивостоку."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=tz_offset_hours)
    day = now_local.date().isoformat()
    return list(conn.execute(
        """SELECT l.*, p.slug AS profile_slug, p.name AS profile_name, p.tag AS profile_tag
           FROM leads l JOIN profiles p ON p.id = l.profile_id
           WHERE date(l.created_at, ?) = ?
           ORDER BY l.score DESC, l.id DESC""",
        (f"{tz_offset_hours:+d} hours", day),
    ).fetchall())
