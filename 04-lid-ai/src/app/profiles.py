"""Профили (мультиклиентность): загрузка YAML, валидация, ключевые слова."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REQUIRED = ("slug", "name", "tag", "services", "looking_for")


class ProfileError(ValueError):
    pass


@dataclass(slots=True)
class Profile:
    slug: str
    name: str
    tag: str
    services: str
    looking_for: str
    not_lead: str = ""
    keywords: list[str] = field(default_factory=list)
    weak_keywords: list[str] = field(default_factory=list)
    stopwords: list[str] = field(default_factory=list)
    threshold: float = 6.0
    prompt_version: str = "v1"
    tg_group_id: int = 0
    tg_topic_id: int = 0
    is_active: bool = True

    @property
    def all_keywords(self) -> list[str]:
        return [*self.keywords, *self.weak_keywords]


def load_profile(path: Path) -> Profile:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ProfileError(f"{path.name}: ожидался словарь YAML")
    missing = [k for k in REQUIRED if not raw.get(k)]
    if missing:
        raise ProfileError(f"{path.name}: нет обязательных полей {missing}")
    p = Profile(
        slug=str(raw["slug"]),
        name=str(raw["name"]),
        tag=str(raw["tag"]),
        services=str(raw["services"]).strip(),
        looking_for=str(raw["looking_for"]).strip(),
        not_lead=str(raw.get("not_lead", "")).strip(),
        keywords=[str(x).lower() for x in raw.get("keywords", [])],
        weak_keywords=[str(x).lower() for x in raw.get("weak_keywords", [])],
        stopwords=[str(x).lower() for x in raw.get("stopwords", [])],
        threshold=float(raw.get("threshold", 6.0)),
        prompt_version=str(raw.get("prompt_version", "v1")),
        tg_group_id=int(raw.get("tg_group_id", 0) or 0),
        tg_topic_id=int(raw.get("tg_topic_id", 0) or 0),
        is_active=bool(raw.get("is_active", True)),
    )
    if not p.tag.startswith("#"):
        raise ProfileError(f"{path.name}: tag должен начинаться с #")
    return p


def load_profiles(directory: Path, only_active: bool = True) -> list[Profile]:
    if not directory.exists():
        raise ProfileError(f"нет папки профилей: {directory}")
    profiles = [load_profile(p) for p in sorted(directory.glob("*.yaml"))]
    if not profiles:
        raise ProfileError(f"в {directory} нет *.yaml профилей")
    slugs = [p.slug for p in profiles]
    if len(slugs) != len(set(slugs)):
        raise ProfileError(f"дубли slug профилей: {slugs}")
    if only_active:
        profiles = [p for p in profiles if p.is_active]
    if not profiles:
        raise ProfileError("нет активных профилей")
    return profiles
