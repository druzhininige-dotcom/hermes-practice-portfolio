"""Тесты предфильтра: не теряем запросы, отсекаем болтовню."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.pipeline.prefilter import normalize, prefilter, text_hash
from app.profiles import load_profiles

PROFILES = load_profiles(Path(__file__).resolve().parent.parent / "profiles")


@pytest.mark.parametrize("text", [
    "Нужна смета на кухню 3,6 метра, размеры есть, кто посчитает?",
    "Ищу кто нарисует 3D проект гардеробной в нишу 2,4 на 0,6",
    "Ищем менеджера на WB, два магазина, полный цикл",
    "Кто может сделать аудит кабинета Ozon?",
    "Кто занимается выкупом с 1688, нужна тестовая партия 50 кг",
    "Нужен поставщик в Китае на солнечные панели под ключ",
    "Нужен телеграм-бот под ключ: приём заявок и Битрикс24",
    "Хочу автоматизировать ответы на отзывы в WB через n8n",
    "Подскажите, кто возит сборные грузы из Китая",
    "Нужна помощь с оформлением карточек товара",
])
def test_request_passes(text):
    assert prefilter(text, PROFILES).passed, text


@pytest.mark.parametrize("text", [
    "доброе утро",
    "всем привет!",
    "спасибо большое",
    "ахаха, ну ты даёшь",
    "ок",
])
def test_noise_filtered(text):
    assert not prefilter(text, PROFILES).passed


def test_short_message_filtered():
    assert prefilter("нужен шкаф", PROFILES).reason == "short"


def test_keyword_hit_reported():
    res = prefilter("Нужна смета на кухню 3,6 метра, размеры есть", PROFILES)
    assert res.passed and "mebel" in res.hits


def test_trigger_without_keyword_passes():
    # нет ни одного ключевого слова, но есть запрос исполнителя
    res = prefilter("Подскажите, кто может помочь с расчётом стоимости проекта?", PROFILES)
    assert res.passed and res.reason == "trigger"


def test_noise_with_longer_text_not_filtered():
    text = "Всем привет! Нужен телеграм-бот под ключ для приёма заявок, кто делает?"
    assert prefilter(text, PROFILES).passed


def test_normalize_and_hash_stable():
    assert normalize("Нужна  СМЕТА, на кухню!") == "нужна смета на кухню"
    assert text_hash("Нужна смета") == text_hash("нужна   смета")


def test_hash_differs_for_different_text():
    assert text_hash("нужна смета") != text_hash("нужен проект")
