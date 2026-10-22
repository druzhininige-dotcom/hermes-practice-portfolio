"""Telethon-листенер: слушает чаты из БД, отдаёт сообщения в обработчик.

Ключевые свойства:
  * read-only: ничего не отправляет и не вступает в чаты;
  * FloodWait соблюдается (Telethon делает это сам, мы только логируем);
  * список чатов перечитывается из БД раз в 60 с — новый чат подхватывается без рестарта;
  * идемпотентность: повторный реконнект не создаёт дублей (см. seen_messages/UNIQUE).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Awaitable, Callable

from app import db
from app.pipeline.processor import IncomingMessage

log = logging.getLogger(__name__)

Handler = Callable[[IncomingMessage], Awaitable[None]]


class Collector:
    def __init__(self, settings, conn: sqlite3.Connection, handler: Handler,
                 reload_interval: int = 60):
        self.s = settings
        self.conn = conn
        self.handler = handler
        self.reload_interval = reload_interval
        self._chats: list[dict] = []
        self._queue: asyncio.Queue[IncomingMessage] = asyncio.Queue(maxsize=2000)
        self._client = None
        self._stop = asyncio.Event()

    # ── список чатов ────────────────────────────────────────────────────
    def reload_chats(self) -> list[dict]:
        self._chats = db.active_chats(self.conn)
        log.info("чатов в работе: %d (%s)", len(self._chats),
                 ", ".join(c["title"] or str(c["tg_chat_id"]) for c in self._chats) or "—")
        return self._chats

    @property
    def chat_ids(self) -> list[int]:
        return [c["tg_chat_id"] for c in self._chats]

    # ── приём ───────────────────────────────────────────────────────────
    async def _on_new_message(self, event) -> None:
        try:
            msg = event.message
            if not msg or not (msg.message or "").strip():
                return
            chat = event.chat
            sender = await event.get_sender()
            incoming = IncomingMessage(
                chat_id=int(event.chat_id),
                message_id=int(msg.id),
                text=msg.message,
                chat_title=getattr(chat, "title", "") or "",
                chat_username=getattr(chat, "username", None),
                chat_type=type(chat).__name__.lower(),
                sender_id=getattr(sender, "id", None),
                sender_username=getattr(sender, "username", None),
                sender_name=_display_name(sender),
                is_bot=bool(getattr(sender, "bot", False)),
                is_forward=bool(getattr(msg, "fwd_from", None)),
            )
            await self._queue.put(incoming)
        except Exception:  # noqa: BLE001 — приём не должен падать из-за одного сообщения
            log.exception("ошибка приёма сообщения")

    async def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                incoming = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                await self.handler(incoming)
            except Exception:  # noqa: BLE001
                log.exception("ошибка обработки сообщения %s/%s",
                              incoming.chat_id, incoming.message_id)
            finally:
                self._queue.task_done()

    async def _reload_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.reload_interval)
            try:
                before = set(self.chat_ids)
                self.reload_chats()
                after = set(self.chat_ids)
                if after - before:
                    log.info("подхвачены новые чаты: %s", after - before)
                    await self._resubscribe()
            except Exception:  # noqa: BLE001
                log.exception("не смог перечитать список чатов")

    async def _resubscribe(self) -> None:
        if self._client is None:
            return
        from telethon import events
        self._client.remove_event_handler(self._on_new_message)
        self._client.add_event_handler(
            self._on_new_message, events.NewMessage(chats=self.chat_ids or None))

    # ── запуск ──────────────────────────────────────────────────────────
    async def run(self) -> None:
        from telethon import TelegramClient, events

        if not self.s.tg_api_id or not self.s.tg_api_hash:
            raise RuntimeError("нет TG_API_ID/TG_API_HASH — коллектор не запустить")
        self.reload_chats()
        if not self._chats:
            log.warning("нет чатов для мониторинга: добавьте через scripts/add_chat.py")

        self._client = TelegramClient(str(self.s.tg_session_path),
                                      self.s.tg_api_id, self.s.tg_api_hash)
        await self._client.start(phone=self.s.tg_phone or None)
        me = await self._client.get_me()
        log.info("вошли как %s (id %s)", getattr(me, "username", None) or "—", me.id)

        self._client.add_event_handler(
            self._on_new_message, events.NewMessage(chats=self.chat_ids or None))

        workers = [asyncio.create_task(self._worker()) for _ in range(4)]
        reloader = asyncio.create_task(self._reload_loop())
        log.info("коллектор запущен, ожидаю сообщения…")
        try:
            await self._client.run_until_disconnected()
        finally:
            self._stop.set()
            for t in [*workers, reloader]:
                t.cancel()
            await self._client.disconnect()

    def stop(self) -> None:
        self._stop.set()


def _display_name(entity) -> str:
    if entity is None:
        return ""
    name = " ".join(x for x in [getattr(entity, "first_name", None),
                                getattr(entity, "last_name", None)] if x)
    return name or getattr(entity, "title", "") or getattr(entity, "username", "") or ""
