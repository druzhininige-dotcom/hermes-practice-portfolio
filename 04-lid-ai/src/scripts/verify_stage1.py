"""Проверка этапа 1 перед сдачей. Код возврата 0 — можно сдавать.

Пересчитывает ключевые числа НЕЗАВИСИМО (своя реализация формулы на Decimal),
сверяет с кодом проекта, проверяет схему БД, карточку и отчёт .docx.
Числа в отчёте сверяются с файлами прогонов, а не с текстом отчёта.

    python scripts/verify_stage1.py
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import subprocess
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = Path(__file__).resolve().parent.parent
DESKTOP_REPORT = Path.home() / "OneDrive" / "Desktop" / "Lid_AI_этап_1_отчёт.docx"
DB = BASE / "data" / "lid_ai.db"
CASES = BASE / "tests" / "cases.yaml"
PROFILES_DIR = BASE / "profiles"

problems: list[str] = []
checks: list[tuple[str, str, bool]] = []

# Запрещённые формулировки: ревью признало их завышенными
FORBIDDEN = [
    "собран и проверен",
    "оценка воспроизводима",
    "массовое чтение чатов противоречит правилам Telegram",
    "оставляем 6 или на старте 5",
]


def check(name: str, detail: str, ok: bool) -> None:
    checks.append((name, detail, ok))
    if not ok:
        problems.append(f"{name}: {detail}")


# ── 1. независимый пересчёт формулы ────────────────────────────────────────
VAL = {"high": 1, "medium": Decimal("0.5"), "low": 0,
       "full": 1, "partial": Decimal("0.5"), "none": 0}


def independent_score(intent: str, domain: str, spec: str, urg: str) -> Decimal:
    total = (Decimal("0.40") * VAL[intent] + Decimal("0.30") * VAL[domain]
             + Decimal("0.20") * VAL[spec] + Decimal("0.10") * VAL[urg])
    return (Decimal(10) * total).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def test_formula() -> None:
    from app.schemas import ProfileJudgement, judgement_score

    cases = [("high", "full", "low", "low"), ("high", "partial", "low", "low"),
             ("high", "full", "medium", "medium"), ("high", "full", "high", "medium"),
             ("high", "full", "high", "high"), ("medium", "partial", "low", "low"),
             ("low", "none", "low", "low")]
    mismatch = []
    for c in cases:
        mine = independent_score(*c)
        code = Decimal(str(judgement_score(ProfileJudgement(
            intent=c[0], domain=c[1], specificity=c[2], urgency=c[3]))))
        if mine != code:
            mismatch.append(f"{c}: независимо {mine}, в коде {code}")
    check("формула: независимый пересчёт", "; ".join(mismatch) or f"{len(cases)} случаев совпали",
          not mismatch)


# ── 2. схема БД и профили ──────────────────────────────────────────────────
def test_schema() -> None:
    from app.profiles import load_profiles

    if not DB.exists():
        check("БД", f"нет файла {DB} — сначала scripts/seed_profiles.py", False)
        return
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    need = {"profiles", "chats", "profile_chats", "leads", "lead_profile_scores",
            "outbox", "llm_calls", "seen_messages"}
    check("таблицы БД", f"есть {len(need & tables)} из {len(need)}", need <= tables)

    lead_cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
    check("leads.profile_id", "колонка есть" if "profile_id" in lead_cols else "НЕТ",
          "profile_id" in lead_cols)

    n_profiles = conn.execute("SELECT COUNT(*) n FROM profiles").fetchone()["n"]
    check("профили в БД", f"{n_profiles} (ожидалось 4)", n_profiles == 4)

    rows = conn.execute(
        """SELECT p.slug, p.tag, p.threshold, p.tg_topic_id
           FROM profiles p ORDER BY p.slug""").fetchall()
    tags = {r["slug"]: r["tag"] for r in rows}
    expect = {"mebel": "#мебель", "mp": "#маркетплейсы", "log": "#логистика", "ai": "#ии"}
    check("теги профилей", str(tags), tags == expect)

    # порог берём из YAML-файлов (источник истины) и сверяем с БД
    yaml_thr = {p.slug: p.threshold for p in load_profiles(PROFILES_DIR)}
    db_thr = {r["slug"]: r["threshold"] for r in rows}
    check("порог: YAML совпадает с БД", f"YAML {yaml_thr}, БД {db_thr}", yaml_thr == db_thr)
    check("порог 5 у всех (решение по ревью)", str(sorted(set(yaml_thr.values()))),
          set(yaml_thr.values()) == {5.0})

    scores_cols = {r["name"] for r in conn.execute("PRAGMA table_info(lead_profile_scores)")}
    check("lead_profile_scores", "is_best/is_secondary есть",
          {"is_best", "is_secondary", "profile_id"} <= scores_cols)
    conn.close()


# ── 3. карточка ────────────────────────────────────────────────────────────
def test_card() -> None:
    from app.notifier.formatter import format_card

    card = format_card(
        {"score": 9, "text": "Нужен бот для отзывов Ozon <b>срочно</b>",
         "chat_title": "Селлеры", "sender_name": "Иван", "sender_username": "ivan",
         "extracted": {"city": "Москва"}, "analysis": "бот",
         "message_link": "https://t.me/c/1/2"},
        "ИИ-агенты и автоматизация", "#ии",
        [{"slug": "mp", "tag": "#маркетплейсы", "score": 6.5}])
    check("карточка: профиль и тег", "есть", "#ии" in card and "ИИ-агенты" in card)
    check("карточка: второй профиль без потери полбалла", "ожидалось «(6,5)»",
          "#маркетплейсы (6,5)" in card)
    check("карточка: экранирование", "нет сырого <b>", "<b>срочно</b>" not in card)
    check("карточка: длина", f"{len(card)} символов", len(card) <= 4000)

    # 6,5 не должно превращаться в «6» (round-half-even)
    card65 = format_card({"score": 6.5, "text": "т", "chat_title": "ч"}, "Тест", "#тест")
    check("карточка: балл 6,5 печатается как 6,5", "ЛИД 6,5/10" in card65,
          "ЛИД 6,5/10" in card65)


# ── 4. прогоны: калибровка и стабильность ─────────────────────────────────
def latest(pattern: str) -> Path | None:
    files = sorted((BASE / "data").glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def test_runs() -> dict:
    from app.pipeline.prompt import prompt_fingerprint
    from app.profiles import load_profiles

    facts: dict = {}
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    ids = [c["id"] for c in cases]
    cross = [c for c in cases if str(c["id"]).startswith("cross_")]
    check("набор кейсов: 19, из них 3 перекрёстных",
          f"{len(cases)} кейсов, перекрёстных {len(cross)}",
          len(cases) == 19 and len(cross) == 3)

    cal = latest("calibration_*.csv")
    if cal is None:
        check("прогон калибровки", "нет data/calibration_*.csv", False)
        return facts
    rows = list(csv.DictReader(cal.open(encoding="utf-8-sig"), delimiter=";"))
    check("калибровка: прогон по всем кейсам", f"{len(rows)} строк в {cal.name} против {len(ids)} кейсов",
          sorted(r["id"] for r in rows) == sorted(ids))
    passed = sum(1 for r in rows if r["итог"] == "OK")
    check("калибровка: все ожидания совпали", f"{passed}/{len(rows)}", passed == len(rows))
    facts["совпало"], facts["всего"] = passed, len(rows)

    conn = sqlite3.connect(BASE / "data" / "calibrate.db")
    cost_cal, calls = conn.execute("SELECT ROUND(SUM(cost_usd), 6), COUNT(*) FROM llm_calls").fetchone()
    conn.close()
    facts["расход_калибровки"] = cost_cal or 0.0
    check("калибровка: вызовов модели меньше, чем кейсов (работает предфильтр)",
          f"{calls} вызовов на {len(rows)} кейсов", calls <= len(rows))
    facts["вызовов"] = calls

    st = latest("stability_*.json")
    if st is None:
        check("прогон стабильности", "нет data/stability_*.json", False)
        return facts
    d = json.loads(st.read_text(encoding="utf-8"))
    fp_now = prompt_fingerprint(load_profiles(PROFILES_DIR))
    check("прогоны относятся к текущему промту", f"{d.get('prompt_fingerprint')} против {fp_now}",
          d.get("prompt_fingerprint") == fp_now)
    check("стабильность: все прогоны дают одно и то же", str(d.get("stable")), bool(d.get("stable")))
    facts["стабильность"] = d
    facts["расход_стабильности"] = d.get("cost_usd", 0.0)
    return facts


# ── 5. автотесты ───────────────────────────────────────────────────────────
def test_pytest() -> int:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=BASE,
                          capture_output=True, text=True, encoding="utf-8", errors="ignore")
    out = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"(\d+) passed", out)
    n = int(m.group(1)) if m else 0
    check("pytest зелёный", f"код {proc.returncode}, {n} passed", proc.returncode == 0 and n > 0)
    if "failed" in out:
        check("pytest без падений", out.strip().splitlines()[-1], False)
    return n


# ── 6. отчёт .docx ─────────────────────────────────────────────────────────
def test_report(n_tests: int, facts: dict) -> None:
    if not DESKTOP_REPORT.exists():
        check("отчёт .docx", f"нет файла {DESKTOP_REPORT}", False)
        return
    from docx import Document
    try:
        doc = Document(DESKTOP_REPORT)
    except Exception as exc:  # noqa: BLE001
        check("отчёт .docx открывается", f"{type(exc).__name__}: {exc}", False)
        return
    text = "\n".join(p.text for p in doc.paragraphs)
    for t in doc.tables:
        for row in t.rows:
            text += "\n" + " | ".join(c.text for c in row.cells)
    check("отчёт .docx открывается", f"{len(doc.paragraphs)} абзацев, {len(doc.tables)} таблиц", True)

    need = [
        f"{facts.get('совпало')} из {facts.get('всего')}",
        str(n_tests),
        "орог 5",
        "152-ФЗ",
        "Как измерять качество",
        "api_id",
        "D:\\файлы Гермес\\24_Lid_AI",
        "cross_ai_mp_real",
        "6,5",
    ]
    for needle in need:
        check(f"отчёт содержит «{needle}»", "есть" if needle in text else "НЕТ", needle in text)

    for bad in FORBIDDEN:
        check(f"отчёт без формулировки «{bad}»", "нет" if bad not in text else "ЕСТЬ", bad not in text)

    md_junk = [p for p in doc.paragraphs if re.search(r"^\s*(#{1,3}\s|\*\*|\|)", p.text)]
    check("в отчёте нет markdown-мусора", f"{len(md_junk)} строк", not md_junk)
    normal_font = doc.styles["Normal"].font.name
    check("шрифт отчёта", f"Normal={normal_font}", normal_font == "Calibri")
    headings = [p.style.name for p in doc.paragraphs if p.style.name.startswith("Heading")]
    check("настоящие заголовки Word", f"{len(headings)} заголовков", len(headings) >= 8)
    emoji = [p.text for p in doc.paragraphs if re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", p.text)]
    check("в отчёте нет эмодзи", f"{len(emoji)} строк", not emoji)


def main() -> int:
    test_formula()
    test_schema()
    test_card()
    facts = test_runs()
    n_tests = test_pytest()
    test_report(n_tests, facts)

    width = max(len(c[0]) for c in checks)
    for name, detail, ok in checks:
        print(f"{'OK  ' if ok else 'ПРОВАЛ'} {name:<{width}}  {detail}")
    print("-" * 78)
    if problems:
        print(f"ДЕФЕКТЫ ({len(problems)}):")
        for p in problems:
            print("  •", p)
        return 1
    print(f"все проверки пройдены: {len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
