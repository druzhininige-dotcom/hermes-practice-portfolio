"""Обработка одного сообщения: префильтр → дедуп → LLM → скорер → БД → очередь отправки."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from app import db
from app.config import Settings
from app.pipeline import prefilter as pf
from app.pipeline.llm import LLMClient, LLMError
from app.pipeline.prompt import build_user_prompt
from app.pipeline.scorer import NEW, PARSE_ERROR, Ranking, decide_status, rank
from app.profiles import Profile
from app.schemas import ParseError

log = logging.getLogger(__name__)


@dataclass(slots=True)
class IncomingMessage:
    chat_id: int
    message_id: int
    text: str
    chat_title: str = ""
    chat_username: str | None = None
    chat_type: str = "group"
    sender_id: int | None = None
    sender_username: str | None = None
    sender_name: str | None = None
    is_bot: bool = False
    is_forward: bool = False


@dataclass(slots=True)
class ProcessResult:
    action: str                      # skipped | rejected | notified | duplicate | error
    reason: str = ""
    lead_id: int | None = None
    ranking: Ranking | None = None
    card: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def process_message(*args, **kwargs):
    """Совместимый вход: синхронного варианта нет, только async."""
    raise RuntimeError("используйте await async_process_message(...)")


async def async_process_message(
    msg: IncomingMessage,
    *,
    settings: Settings,
    profiles: list[Profile],
    profile_ids: dict[str, int],
    conn: sqlite3.Connection,
    llm: LLMClient,
    system_prompt: str,
) -> ProcessResult:
    if msg.is_bot:
        return ProcessResult("skipped", "bot_author")
    if not (msg.text or "").strip():
        return ProcessResult("skipped", "empty")

    if db.is_seen(conn, msg.chat_id, msg.message_id):
        return ProcessResult("duplicate", "already_seen")

    pre = pf.prefilter(msg.text, profiles)
    if not pre.passed:
        db.mark_seen(conn, msg.chat_id, msg.message_id, pf.text_hash(msg.text),
                     skip_reason=pre.reason)
        return ProcessResult("skipped", pre.reason)

    link = _link(msg)
    user_prompt = build_user_prompt(msg.text, msg.chat_title,
                                    msg.sender_username or msg.sender_name or "")
    try:
        answer, usage = await llm.classify(
            system_prompt, user_prompt, [p.slug for p in profiles],
            chat_id=msg.chat_id, message_id=msg.message_id,
        )
    except (LLMError, ParseError) as exc:
        log.warning("LLM не смог обработать %s/%s: %s", msg.chat_id, msg.message_id, exc)
        return ProcessResult("error", str(exc)[:300])

    ranking = rank(answer, profiles, secondary_max_gap=settings.secondary_max_gap)
    status = decide_status(ranking)

    if not ranking.is_lead or ranking.best is None:
        # не лид: пишем в БД только чтобы было видно, что сообщение разобрано
        db.mark_seen(conn, msg.chat_id, msg.message_id, pf.text_hash(msg.text),
                     profile_hits=[s.slug for s in ranking.scores], skip_reason="not_lead")
        return ProcessResult("rejected", ranking.reason_not_lead, ranking=ranking)

    best = ranking.best
    secondary_for_card = [
        {"slug": s.slug, "tag": s.profile.tag, "score": s.score} for s in ranking.secondary
    ]
    lead = {
        "profile_id": profile_ids[best.slug],
        "chat_id": msg.chat_id,
        "chat_title": msg.chat_title,
        "message_id": msg.message_id,
        "message_link": link,
        "sender_id": msg.sender_id,
        "sender_username": msg.sender_username,
        "sender_name": msg.sender_name,
        "text": msg.text,
        "score": best.score,
        "is_lead": True,
        "lead_type": answer.lead_type,
        "intent": best.judgement.intent,
        "domain": best.judgement.domain,
        "specificity": best.judgement.specificity,
        "urgency": best.judgement.urgency,
        "extracted": answer.extracted.model_dump(exclude_none=True),
        "analysis": (answer.summary or best.judgement.reason)[:300],
        "secondary": secondary_for_card,
        "status": status,
        "prompt_version": f"{best.profile.prompt_version}",
        "llm_model": settings.llm_model,
        "notify_state": "pending" if status == NEW else "skipped",
    }
    scores = [
        {
            "profile_id": profile_ids[s.slug],
            "score": s.score,
            "intent": s.judgement.intent,
            "domain": s.judgement.domain,
            "specificity": s.judgement.specificity,
            "urgency": s.judgement.urgency,
            "is_best": s.is_best,
            "is_secondary": s.is_secondary,
            "reason": s.judgement.reason,
        }
        for s in ranking.scores
    ]

    lead_id = db.save_lead(conn, lead, scores)
    db.mark_seen(conn, msg.chat_id, msg.message_id, pf.text_hash(msg.text),
                 profile_hits=[s.slug for s in ranking.scores])

    if lead_id is None:  # гонка/повтор
        return ProcessResult("duplicate", "lead_exists")

    if status != NEW:
        return ProcessResult("rejected", f"score {best.score} < {best.profile.threshold}",
                             lead_id=lead_id, ranking=ranking)

    db.enqueue_outbox(conn, lead_id, {"lead_id": lead_id, "profile_slug": best.slug})
    return ProcessResult("notified", "new_lead", lead_id=lead_id, ranking=ranking,
                         extra={"usage": usage})


def _link(msg: IncomingMessage) -> str:
    from app.notifier.formatter import message_link
    return message_link(msg.chat_id, msg.message_id, msg.chat_username)
