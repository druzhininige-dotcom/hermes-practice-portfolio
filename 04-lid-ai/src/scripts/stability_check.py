"""Проверка стабильности оценки: один и тот же текст N раз через реальную модель.

Пишет data/stability_<дата>.json — отчёт берёт цифры оттуда, а не из ручного ввода.

    python scripts/stability_check.py                    # 3 прогона, перекрёстные кейсы
    python scripts/stability_check.py --runs 5 --ids cross_ai_mp
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.pipeline.llm import LLMClient  # noqa: E402
from app.pipeline.prompt import build_system_prompt, build_user_prompt, prompt_fingerprint  # noqa: E402
from app.pipeline.scorer import rank  # noqa: E402
from app.profiles import load_profiles  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
DEFAULT_IDS = ["cross_ai_mp", "cross_ai_mp_real"]


async def run(runs: int, ids: list[str]) -> int:
    settings = load_settings()
    profiles = load_profiles(settings.profiles_dir)
    cases = {c["id"]: c for c in yaml.safe_load(
        (BASE / "tests" / "cases.yaml").read_text(encoding="utf-8"))}
    missing = [i for i in ids if i not in cases]
    if missing:
        print(f"нет кейсов в tests/cases.yaml: {missing}")
        return 1

    system_prompt = build_system_prompt(profiles)
    result: dict[str, list[dict]] = {}
    cost = 0.0
    conn = db.connect(settings.db_path)
    db.upsert_profiles(conn, profiles)

    async with LLMClient(settings, conn) as llm:
        for case_id in ids:
            text = cases[case_id]["text"]
            rows = []
            for n in range(1, runs + 1):
                answer, usage = await llm.classify(
                    system_prompt, build_user_prompt(text, "Стабильность"),
                    [p.slug for p in profiles],
                    chat_id=-100501, message_id=n,
                )
                cost += usage.cost_usd
                r = rank(answer, profiles, secondary_max_gap=settings.secondary_max_gap)
                rows.append({
                    "run": n,
                    "is_lead": r.is_lead,
                    "best": r.best.slug if r.best else None,
                    "score": r.best.score if r.best else 0.0,
                    "secondary": [{"slug": s.slug, "score": s.score} for s in r.secondary],
                })
                sec = ", ".join(f"{s['slug']} {s['score']}" for s in rows[-1]["secondary"]) or "—"
                print(f"{case_id:<18} прогон {n}: {rows[-1]['best']} {rows[-1]['score']} · второй: {sec}")
            result[case_id] = rows

    same = all(
        len({(r["best"], r["score"], tuple(s["slug"] for s in r["secondary"])) for r in rows}) == 1
        for rows in result.values()
    )
    out = {
        "date": date.today().isoformat(),
        "model": settings.llm_model,
        "prompt_fingerprint": prompt_fingerprint(profiles),
        "runs": runs,
        "stable": same,
        "cost_usd": round(cost, 6),
        "cases": result,
    }
    conn.close()
    path = BASE / "data" / f"stability_{date.today():%Y-%m-%d}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("-" * 60)
    print(f"стабильно: {'да' if same else 'НЕТ'} · файл: {path}")
    return 0 if same else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--ids", nargs="*", default=DEFAULT_IDS)
    args = ap.parse_args()
    return asyncio.run(run(args.runs, args.ids))


if __name__ == "__main__":
    raise SystemExit(main())
