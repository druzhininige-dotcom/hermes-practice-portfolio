"""Калибровка на тестовом наборе (tests/cases.yaml) через реальный LLM.

    python scripts/calibrate.py              # таблица ожидание/факт + код возврата
    python scripts/calibrate.py --cards      # плюс карточки лидов
    python scripts/calibrate.py --csv out.csv

Код возврата 0 — все ожидания совпали, 1 — есть расхождения.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.notifier.formatter import format_card  # noqa: E402
from app.pipeline.llm import LLMClient  # noqa: E402
from app.pipeline.processor import IncomingMessage, async_process_message  # noqa: E402
from app.pipeline.prompt import build_system_prompt, prompt_fingerprint  # noqa: E402
from app.profiles import load_profiles  # noqa: E402

BASE = Path(__file__).resolve().parent.parent


async def run(csv_path: str = "", show_cards: bool = False) -> int:
    settings = load_settings()
    settings.db_path = BASE / "data" / "calibrate.db"
    if settings.db_path.exists():
        settings.db_path.unlink()
    profiles = load_profiles(settings.profiles_dir)
    conn = db.connect(settings.db_path)
    ids = db.upsert_profiles(conn, profiles)
    cases = yaml.safe_load((BASE / "tests" / "cases.yaml").read_text(encoding="utf-8"))

    rows: list[dict] = []
    misses: list[str] = []
    async with LLMClient(settings, conn) as llm:
        system_prompt = build_system_prompt(profiles)
        print(f"модель: {settings.llm_model} · промт: {prompt_fingerprint(profiles)} · "
              f"профили: {', '.join(p.slug for p in profiles)}")
        print(f"{'кейс':<16} {'ожид.':<14} {'факт':<26} {'балл':>4} {'2-й':<6} итог")
        print("-" * 96)
        for i, case in enumerate(cases, start=1):
            res = await async_process_message(
                IncomingMessage(chat_id=-100500, message_id=i, text=case["text"],
                                chat_title="Калибровка"),
                settings=settings, profiles=profiles, profile_ids=ids, conn=conn, llm=llm,
                system_prompt=system_prompt)
            expect_lead = bool(case.get("expect_lead", True))
            expect_profile = case.get("profile", "")
            expect_secondary = case.get("expect_secondary")
            expect_profiles = case.get("expect_profiles") or []

            ranking = res.ranking
            got_profile = ranking.best.slug if ranking and ranking.best and ranking.is_lead else "—"
            got_score = ranking.best.score if ranking and ranking.best else 0.0
            got_second = ",".join(s.slug for s in ranking.secondary) if ranking and ranking.secondary else "—"
            got_lead = res.action == "notified"

            ok = got_lead == expect_lead
            detail = ""
            if expect_lead and got_lead and expect_profile and got_profile != expect_profile:
                ok = False
                detail = f"профиль: ждали {expect_profile}"
            if expect_secondary and expect_secondary not in got_second:
                ok = False
                detail = f"второй: ждали {expect_secondary}"
            if expect_profiles:
                got_set = {got_profile, *[s.slug for s in (ranking.secondary if ranking else [])]}
                missing = [p for p in expect_profiles if p not in got_set]
                if missing:
                    ok = False
                    detail = f"в карточке нет профилей {missing}"
            if not ok:
                misses.append(f"{case['id']}: {detail or res.reason}")

            print(f"{case['id']:<16} {'лид' if expect_lead else 'не лид':<14} "
                  f"{got_profile + ('/' + got_second if got_second != '—' else ''):<26} "
                  f"{got_score:>4.0f} {got_second:<6} {'OK' if ok else 'ПРОВАЛ'}")
            if ok and detail:
                print(f"{'':<16} примечание: {detail}")
            rows.append({
                "id": case["id"], "ожидание": "лид" if expect_lead else "не лид",
                "ожидаемый_профиль": expect_profile, "ожидаемый_второй": expect_secondary or "",
                "факт_профиль": got_profile, "факт_балл": f"{got_score:.0f}",
                "факт_второй": got_second, "действие": res.action, "итог": "OK" if ok else "ПРОВАЛ",
            })
            if show_cards and got_lead and ranking and ranking.best:
                secondary = [{"slug": s.slug, "tag": s.profile.tag, "score": s.score}
                             for s in ranking.secondary]
                print("\n--- карточка ---")
                print(format_card({"score": got_score, "text": case["text"],
                                   "chat_title": "Калибровка", "sender_name": "Тест",
                                   "analysis": ranking.best.judgement.reason,
                                   "message_link": ""},
                                  ranking.best.profile.name, ranking.best.profile.tag, secondary))
                print("---\n")

    total = len(rows)
    passed = sum(1 for r in rows if r["итог"] == "OK")
    print("-" * 96)
    print(f"итог: {passed}/{total} совпало")
    if misses:
        print("расхождения:")
        for m in misses:
            print("  •", m)
    print(f"расход за прогон: ${db.cost_today(conn):.4f}")

    if csv_path:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
            w.writeheader()
            w.writerows(rows)
        print(f"CSV: {csv_path}")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="")
    ap.add_argument("--cards", action="store_true")
    args = ap.parse_args()
    return asyncio.run(run(args.csv, args.cards))


if __name__ == "__main__":
    raise SystemExit(main())
