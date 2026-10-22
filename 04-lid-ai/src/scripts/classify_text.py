"""Один текст через всю цепочку: префильтр → LLM → скорер → карточка.

    python scripts/classify_text.py "нужна смета на кухню 3 метра, кто посчитает"
    python scripts/classify_text.py --file message.txt --chat "Чат мебельщиков" --db :memory:
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.notifier.formatter import format_card  # noqa: E402
from app.pipeline import prefilter as pf  # noqa: E402
from app.pipeline.llm import LLMClient  # noqa: E402
from app.pipeline.prompt import build_system_prompt  # noqa: E402
from app.pipeline.processor import IncomingMessage, async_process_message  # noqa: E402
from app.profiles import load_profiles  # noqa: E402
from app.schemas import ParseError  # noqa: E402


async def run(text: str, chat_title: str, db_path: str) -> int:
    settings = load_settings()
    settings.db_path = Path(db_path) if db_path != ":memory:" else Path(":memory:")
    profiles = load_profiles(settings.profiles_dir)

    pre = pf.prefilter(text, profiles)
    print(f"префильтр: passed={pre.passed} reason={pre.reason} "
          f"hits={pre.hits or '—'} trigger={pre.has_trigger}")
    if not pre.passed:
        print("дальше не идём: сообщение отсеяно префильтром")
        return 0

    if db_path == ":memory:":
        conn = db.connect(":memory:")
    else:
        conn = db.connect(settings.db_path)
    ids = db.upsert_profiles(conn, profiles)

    async with LLMClient(settings, conn) as llm:
        try:
            result = await async_process_message(
                IncomingMessage(chat_id=-1001, message_id=1, text=text, chat_title=chat_title),
                settings=settings, profiles=profiles, profile_ids=ids, conn=conn, llm=llm,
                system_prompt=build_system_prompt(profiles))
        except ParseError as exc:
            print(f"ОШИБКА РАЗБОРА: {exc}")
            return 2

    if result.ranking is None:
        print(f"результат: {result.action} ({result.reason})")
        return 0

    print("\nоценки по профилям:")
    print(f"  {'профиль':<8} {'балл':>5}  {'intent/domain/spec/urg':<28} отметка")
    for s in result.ranking.scores:
        mark = "ЛУЧШИЙ" if s.is_best else ("второй" if s.is_secondary else "")
        j = s.judgement
        print(f"  {s.slug:<8} {s.score:>5.0f}  {j.intent}/{j.domain}/{j.specificity}/{j.urgency:<10} {mark}")

    print(f"\nрешение: {result.action} ({result.reason})")
    if result.ranking.best:
        best = result.ranking.best
        secondary = [{"slug": s.slug, "tag": s.profile.tag, "score": s.score}
                     for s in result.ranking.secondary]
        card = format_card(
            {"score": best.score, "text": text, "chat_title": chat_title,
             "sender_name": "Проверка", "extracted": {}, "analysis": best.judgement.reason,
             "message_link": ""},
            best.profile.name, best.profile.tag, secondary)
        print("\n--- карточка ---")
        print(card)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="", help="текст сообщения")
    ap.add_argument("--file", default="", help="файл с текстом")
    ap.add_argument("--chat", default="", help="название чата (для промта)")
    ap.add_argument("--db", default=":memory:", help="путь к БД (:memory: по умолчанию)")
    args = ap.parse_args()

    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text.strip():
        print("нужен текст сообщения или --file", file=sys.stderr)
        return 2
    return asyncio.run(run(text, args.chat, args.db))


if __name__ == "__main__":
    raise SystemExit(main())
