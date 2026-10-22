"""Форматирование карточки лида для Telegram.

Обязательные элементы: название профиля, тег профиля, балл, кто/где/что написал,
ссылка на сообщение, извлечённые параметры, второй близкий профиль.
"""

from __future__ import annotations

import html

from app.schemas import round_half_up

MAX_TEXT = 700


def fmt_score(value: object) -> str:
    """Балл для человека: 9.0 → «9», 6.5 → «6,5».

    Раньше стояло «{:.0f}» — Python округляет 6.5 к чётному и печатал «6»,
    то есть карточка теряла полбалла и искажала разницу до второго профиля.
    """
    score = round_half_up(float(value or 0), 1)
    if score == int(score):
        return str(int(score))
    return f"{score:.1f}".replace(".", ",")


def message_link(chat_id: int, message_id: int, username: str | None = None) -> str:
    if username:
        return f"https://t.me/{username.lstrip('@')}/{message_id}"
    cid = str(chat_id)
    if cid.startswith("-100"):
        return f"https://t.me/c/{cid[4:]}/{message_id}"
    return ""


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def format_card(lead: dict, profile_name: str, profile_tag: str,
                secondary: list[dict] | None = None) -> str:
    """Собирает HTML-карточку. Текст лида — в <blockquote>, экранирован."""
    secondary = secondary or []
    score = float(lead.get("score") or 0)
    status_icon = "🟢" if score >= 8 else ("🟡" if score >= 6 else "🔴")

    who = lead.get("sender_name") or "без имени"
    if lead.get("sender_username"):
        who += f" (@{lead['sender_username']})"

    lines = [
        f"{status_icon} <b>ЛИД {fmt_score(score)}/10</b> · {_esc(profile_tag)}",
        f"<b>Профиль:</b> {_esc(profile_name)}",
        "",
        f"<b>Кто:</b> {_esc(who)}",
    ]
    if lead.get("chat_title"):
        lines.append(f"<b>Где:</b> {_esc(lead['chat_title'])}")
    text = (lead.get("text") or "").strip()
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT].rstrip() + "…"
    lines += ["", f"<blockquote>{_esc(text)}</blockquote>"]

    extracted = lead.get("extracted") or {}
    facts = [(k, v) for k, v in extracted.items() if v]
    if facts:
        names = {"city": "город", "product": "товар", "volume": "объём",
                 "budget": "бюджет", "contact": "контакт"}
        lines += ["", "<b>Из запроса:</b> " + " · ".join(
            f"{names.get(k, k)}: {_esc(v)}" for k, v in facts)]

    if secondary:
        lines.append("<b>Также подходит:</b> " + ", ".join(
            f"{_esc(s.get('tag', '#' + s.get('slug', '')))} ({fmt_score(s.get('score'))})"
            for s in secondary))

    analysis = (lead.get("analysis") or "").strip()
    if analysis:
        lines += ["", f"<i>{_esc(analysis[:300])}</i>"]

    link = lead.get("message_link") or ""
    if link:
        lines.append(f'\n<a href="{_esc(link)}">Открыть сообщение</a>')
    return "\n".join(lines)


def card_length_ok(card: str) -> bool:
    """Telegram: 4096 символов на сообщение."""
    return len(card) <= 4000
