"""Тесты профилей: 4 профиля, обязательные поля, ключевые слова, теги."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.profiles import ProfileError, load_profile, load_profiles

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"


def test_four_profiles_loaded():
    profiles = load_profiles(PROFILES_DIR)
    assert {p.slug for p in profiles} == {"mebel", "mp", "log", "ai"}


@pytest.mark.parametrize("slug,tag", [
    ("mebel", "#мебель"), ("mp", "#маркетплейсы"), ("log", "#логистика"), ("ai", "#ии")])
def test_profile_tags(slug, tag):
    profiles = {p.slug: p for p in load_profiles(PROFILES_DIR)}
    assert profiles[slug].tag == tag


@pytest.mark.parametrize("slug", ["mebel", "mp", "log", "ai"])
def test_profile_complete(slug):
    p = {x.slug: x for x in load_profiles(PROFILES_DIR)}[slug]
    assert len(p.keywords) >= 15, "ключевых слов должно быть достаточно"
    assert p.services and p.looking_for and p.not_lead
    assert 0 < p.threshold <= 10
    assert p.prompt_version


def test_duplicate_slug_rejected(tmp_path):
    src = (PROFILES_DIR / "mebel.yaml").read_text(encoding="utf-8")
    (tmp_path / "a.yaml").write_text(src, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(src.replace("slug: mebel", "slug: mebel", 1), encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profiles(tmp_path)


def test_bad_tag_rejected(tmp_path):
    src = (PROFILES_DIR / "mebel.yaml").read_text(encoding="utf-8")
    path = tmp_path / "x.yaml"
    path.write_text(src.replace('tag: "#мебель"', 'tag: "мебель"'), encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile(path)
