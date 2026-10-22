"""Синхронизация профилей из YAML в БД + привязка чатов.

Примеры:
    python scripts/seed_profiles.py
    python scripts/seed_profiles.py --chat https://t.me/chatname --profiles mebel,mp
    python scripts/seed_profiles.py --list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.profiles import load_profiles  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat", action="append", default=[],
                    help="tg_chat_id или @username (можно несколько раз)")
    ap.add_argument("--profiles", default="", help="slug профилей через запятую (по умолчанию все)")
    ap.add_argument("--title", default="", help="название чата")
    ap.add_argument("--list", action="store_true", help="показать профили и чаты")
    args = ap.parse_args()

    settings = load_settings()
    profiles = load_profiles(settings.profiles_dir)
    conn = db.connect(settings.db_path)
    ids = db.upsert_profiles(conn, profiles)
    print(f"БД: {settings.db_path}")
    print("Профили:")
    for p in profiles:
        print(f"  {ids[p.slug]:>2}  {p.slug:<6} {p.tag:<16} порог {p.threshold:g}  "
              f"топик {p.tg_topic_id or '—'}  {p.name}")

    if args.chat:
        wanted = [s.strip() for s in args.profiles.split(",") if s.strip()] or [p.slug for p in profiles]
        unknown = [s for s in wanted if s not in ids]
        if unknown:
            print(f"НЕИЗВЕСТНЫЕ профили: {unknown}", file=sys.stderr)
            return 2
        for raw in args.chat:
            chat_id = _parse_chat_id(raw)
            if chat_id is None:
                print(f"  {raw}: не могу разобрать id — укажите числовой tg_chat_id", file=sys.stderr)
                continue
            cid = db.upsert_chat(conn, chat_id, title=args.title, username=raw.lstrip("@")
                                 if raw.startswith("@") else None)
            for slug in wanted:
                db.link_chat(conn, ids[slug], cid)
            print(f"  чат {raw} ({chat_id}) → профили {', '.join(wanted)}")

    if args.list or not args.chat:
        print("\nЧаты в работе:")
        chats = db.active_chats(conn)
        for c in chats:
            print(f"  {c['tg_chat_id']:>15}  {c['title'] or '—'}  профили: {','.join(c['profiles'])}")
        if not chats:
            print("  (пусто — добавьте через --chat)")
    return 0


def _parse_chat_id(raw: str) -> int | None:
    value = raw.strip().rstrip("/").split("/")[-1]
    if value.startswith("-") or value.isdigit():
        try:
            return int(value)
        except ValueError:
            return None
    return None


if __name__ == "__main__":
    raise SystemExit(main())
