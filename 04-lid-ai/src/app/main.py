"""Точка входа: коллектор + LLM-обработчик + отправка карточек.

Запуск:  python -m app.main
Тест без Telegram:  python scripts/demo_run.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app import db
from app.collector.listener import Collector
from app.config import load_settings
from app.notifier.sender import create_bot, send_lead_card
from app.pipeline.llm import BudgetExceeded, LLMClient
from app.pipeline.processor import IncomingMessage, async_process_message
from app.pipeline.prompt import build_system_prompt, prompt_fingerprint
from app.profiles import Profile, load_profiles

log = logging.getLogger("lid_ai")


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    log_dir = log_dir or Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    fh = RotatingFileHandler(log_dir / "lid_ai.log", maxBytes=5_000_000,
                             backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)


def profile_map(profiles: list[Profile]) -> dict[str, Profile]:
    return {p.slug: p for p in profiles}


async def outbox_loop(conn: sqlite3.Connection, settings, profiles_by_slug: dict[str, Profile],
                      bot, interval: float = 3.0) -> None:
    """Идемпотентная отправка накопленных карточек (полноценные ретраи — этап 2)."""
    while True:
        rows = conn.execute(
            """SELECT o.id AS outbox_id, o.payload_json, l.*
               FROM outbox o JOIN leads l ON l.id = o.lead_id
               WHERE o.status='pending' ORDER BY o.id LIMIT 20"""
        ).fetchall()
        for row in rows:
            lead = dict(row)
            payload = json.loads(lead.get("payload_json") or "{}")
            slug = payload.get("profile_slug") or ""
            profile = profiles_by_slug.get(slug)
            if profile is None:
                conn.execute("UPDATE outbox SET status='failed', last_error=? WHERE id=?",
                             ("профиль не найден", lead["outbox_id"]))
                continue
            secondary = json.loads(lead.get("secondary_json") or "[]")
            try:
                sent_id = await send_lead_card(
                    bot, settings, lead=lead, profile_name=profile.name,
                    profile_tag=profile.tag, topic_id=profile.tg_topic_id,
                    secondary=secondary,
                    group_id=profile.tg_group_id or None,
                )
            except Exception as exc:  # noqa: BLE001
                conn.execute(
                    """UPDATE outbox SET status='failed', attempts=attempts+1, last_error=?
                       WHERE id=?""", (str(exc)[:400], lead["outbox_id"]))
                db.set_notify_state(conn, lead["id"], "failed")
                continue

            if sent_id:
                conn.execute(
                    "UPDATE outbox SET status='sent', sent_at=datetime('now'), attempts=attempts+1 WHERE id=?",
                    (lead["outbox_id"],))
                db.set_notify_state(conn, lead["id"], "sent")
            else:  # dry-run
                conn.execute(
                    "UPDATE outbox SET status='dry_run', attempts=attempts+1 WHERE id=?",
                    (lead["outbox_id"],))
                db.set_notify_state(conn, lead["id"], "dry_run")
        await asyncio.sleep(interval)


async def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    profiles = load_profiles(settings.profiles_dir)
    conn = db.connect(settings.db_path)
    ids = db.upsert_profiles(conn, profiles)
    system_prompt = build_system_prompt(profiles)
    log.info("профили: %s", ", ".join(f"{p.slug}({ids[p.slug]})" for p in profiles))
    log.info("версия промта: %s · модель: %s · dry_run=%s",
             prompt_fingerprint(profiles), settings.llm_model, settings.dry_run)

    bot = create_bot(settings)
    if bot is None:
        log.warning("BOT_TOKEN не задан — карточки только в лог (dry-run)")

    async with LLMClient(settings, conn) as llm:
        async def handler(msg: IncomingMessage) -> None:
            try:
                result = await async_process_message(
                    msg, settings=settings, profiles=profiles, profile_ids=ids,
                    conn=conn, llm=llm, system_prompt=system_prompt)
                log.info("%s %s/%s: %s %s", result.action, msg.chat_id, msg.message_id,
                         result.reason, f"score={result.ranking.best_score:.0f}"
                         if result.ranking else "")
            except BudgetExceeded as exc:
                log.error("бюджет исчерпан: %s", exc)
            except Exception:  # noqa: BLE001
                log.exception("ошибка обработки")

        sender = asyncio.create_task(outbox_loop(conn, settings, profile_map(profiles), bot))
        collector = Collector(settings, conn, handler)
        try:
            await collector.run()
        except RuntimeError as exc:
            log.error("%s", exc)
            log.error("Карточки в группу не уходят, но всё остальное готово: "
                      "заполните .env (TG_API_ID, TG_API_HASH, TG_PHONE, BOT_TOKEN, LEADS_GROUP_ID) "
                      "и запустите scripts/login_telethon.py")
            return
        finally:
            sender.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("остановлено пользователем")
