"""Тесты карточки: профиль и тег, экранирование HTML, лимит длины, ссылки."""

from __future__ import annotations

from app.notifier.formatter import card_length_ok, format_card, message_link

LEAD = {
    "score": 9.0,
    "text": "Нужен бот для автоответов на отзывы Ozon, <b>срочно</b> & важно",
    "chat_title": "Селлеры Ozon",
    "sender_name": "Иван",
    "sender_username": "ivan_msk",
    "extracted": {"city": "Москва", "product": "автоответы на отзывы"},
    "analysis": "Нужен бот-автоответчик",
    "message_link": "https://t.me/c/123456/789",
}


def test_card_shows_profile_and_tag():
    card = format_card(LEAD, "ИИ-агенты и автоматизация", "#ии",
                       [{"slug": "mp", "tag": "#маркетплейсы", "score": 7.0}])
    assert "ЛИД 9/10" in card
    assert "#ии" in card
    assert "ИИ-агенты и автоматизация" in card
    assert "#маркетплейсы (7)" in card
    assert "Также подходит" in card


def test_card_escapes_html():
    card = format_card(LEAD, "Тест", "#ии")
    assert "&lt;b&gt;срочно&lt;/b&gt;" in card
    assert "<b>срочно</b>" not in card


def test_card_has_link_and_facts():
    card = format_card(LEAD, "Тест", "#ии")
    assert 'href="https://t.me/c/123456/789"' in card
    assert "город: Москва" in card
    assert "товар: автоответы на отзывы" in card


def test_card_long_text_truncated():
    lead = dict(LEAD, text="я" * 5000)
    card = format_card(lead, "Тест", "#ии")
    assert card_length_ok(card)
    assert "…" in card


def test_link_public_chat():
    assert message_link(-1001234567890, 55, "somechat") == "https://t.me/somechat/55"


def test_link_private_supergroup():
    assert message_link(-1001234567890, 55) == "https://t.me/c/1234567890/55"


def test_link_unavailable_for_plain_group():
    assert message_link(-123456, 55) == ""


def test_score_icon_thresholds():
    assert "🟢" in format_card(dict(LEAD, score=8), "T", "#т")
    assert "🟡" in format_card(dict(LEAD, score=6), "T", "#т")
    assert "🔴" in format_card(dict(LEAD, score=4), "T", "#т")


def test_fractional_score_not_rounded_away():
    # 6.5 раньше печаталось как «6» (Python округляет к чётному) — терялось полбалла
    card = format_card(dict(LEAD, score=6.5), "T", "#т")
    assert "ЛИД 6,5/10" in card
    assert "ЛИД 6/10" not in card
    card2 = format_card(dict(LEAD, score=9.0), "T", "#т",
                        [{"slug": "mp", "tag": "#маркетплейсы", "score": 6.5}])
    assert "ЛИД 9/10" in card2
    assert "#маркетплейсы (6,5)" in card2
