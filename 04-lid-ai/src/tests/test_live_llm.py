"""Живой прогон тестового набора через реальный LLM (маркер live).

    pytest -m live -v
    python scripts/calibrate.py --cards     # то же, но с таблицей и карточками

Проверяются требования задания: по 2 положительных и 2 отрицательных на профиль,
2 перекрёстных случая (второй профиль должен попасть в карточку).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import yaml

from app import db
from app.config import load_settings
from app.pipeline.llm import LLMClient
from app.pipeline.processor import IncomingMessage, async_process_message
from app.pipeline.prompt import build_system_prompt
from app.profiles import load_profiles

BASE = Path(__file__).resolve().parent.parent
CASES = yaml.safe_load((BASE / "tests" / "cases.yaml").read_text(encoding="utf-8"))
SETTINGS = load_settings()
HAS_KEY = bool(SETTINGS.llm_api_key)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not HAS_KEY, reason="нет LLM_API_KEY — живой прогон пропущен"),
]


def _run_case(case: dict) -> dict:
    async def inner() -> dict:
        profiles = load_profiles(SETTINGS.profiles_dir)
        SETTINGS.db_path = BASE / "data" / "test_live.db"
        if SETTINGS.db_path.exists():
            SETTINGS.db_path.unlink()
        conn = db.connect(SETTINGS.db_path)
        ids = db.upsert_profiles(conn, profiles)
        async with LLMClient(SETTINGS, conn) as llm:
            res = await async_process_message(
                IncomingMessage(chat_id=-100777, message_id=abs(hash(case["id"])) % 10_000,
                                text=case["text"], chat_title="Тест"),
                settings=SETTINGS, profiles=profiles, profile_ids=ids, conn=conn, llm=llm,
                system_prompt=build_system_prompt(profiles))
        conn.close()
        return {
            "action": res.action,
            "best": res.ranking.best.slug if res.ranking and res.ranking.best else "—",
            "score": res.ranking.best_score if res.ranking else 0.0,
            "second": [s.slug for s in res.ranking.secondary] if res.ranking else [],
            "reason": res.reason,
        }
    return asyncio.run(inner())


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case(case):
    got = _run_case(case)
    expect_lead = bool(case.get("expect_lead", True))
    assert (got["action"] == "notified") == expect_lead, (
        f"{case['id']}: ждали lead={expect_lead}, получили {got}")
    if expect_lead and case.get("profile"):
        assert got["best"] == case["profile"], f"{case['id']}: профиль {got['best']}"
    if case.get("expect_secondary"):
        assert case["expect_secondary"] in got["second"], (
            f"{case['id']}: второй профиль {got['second']}, ждали {case['expect_secondary']}")
    if case.get("expect_profiles"):
        got_set = {got["best"], *got["second"]}
        missing = [p for p in case["expect_profiles"] if p not in got_set]
        assert not missing, f"{case['id']}: в карточке нет профилей {missing} ({got_set})"
