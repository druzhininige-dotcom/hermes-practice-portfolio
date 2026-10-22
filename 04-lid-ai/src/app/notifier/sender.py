"""Отправка карточек в группу «ЛИДЫ»: профиль → свой топик (если задан) + тег."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from app.config import Settings
from app.notifier.formatter import format_card

log = logging.getLogger(__name__)


def create_bot(settings: Settings) -> Bot | None:
    if not settings.bot_token:
        return None
    return Bot(settings.bot_token,
               default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def send_lead_card(bot: Bot | None, settings: Settings, *, lead: dict,
                         profile_name: str, profile_tag: str, topic_id: int = 0,
                         secondary: list[dict] | None = None,
                         group_id: int | None = None) -> int | None:
    """Возвращает message_id отправленной карточки или None (dry-run / нет настроек)."""
    card = format_card(lead, profile_name, profile_tag, secondary)
    target_chat = group_id or settings.leads_group_id
    target_topic = topic_id or settings.leads_default_topic_id

    if settings.dry_run or bot is None or not target_chat:
        log.info("DRY-RUN карточка (%s):\n%s", profile_tag, card)
        return None

    kwargs: dict = {"chat_id": target_chat, "text": card,
                    "disable_web_page_preview": False}
    if target_topic:
        kwargs["message_thread_id"] = target_topic
    try:
        sent = await bot.send_message(**kwargs)
        return sent.message_id
    except TelegramAPIError as exc:
        log.error("не смог отправить карточку в %s (топик %s): %s",
                  target_chat, target_topic, exc)
        raise
