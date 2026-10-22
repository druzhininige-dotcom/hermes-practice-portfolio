"""Одноразовый вход в аккаунт-сборщик (код из Telegram + 2FA).

    python scripts/login_telethon.py

Создаёт сессию по пути TG_SESSION_PATH. Аккаунт должен уже состоять в чатах,
которые будем слушать (приватные чаты иначе недоступны).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_settings  # noqa: E402


async def main() -> int:
    from telethon import TelegramClient

    s = load_settings()
    if not s.tg_api_id or not s.tg_api_hash:
        print("Заполните TG_API_ID и TG_API_HASH в .env "
              "(my.telegram.org → API development tools)", file=sys.stderr)
        return 2
    if not s.tg_phone:
        print("Заполните TG_PHONE в .env (номер аккаунта-сборщика)", file=sys.stderr)
        return 2

    s.tg_session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(s.tg_session_path), s.tg_api_id, s.tg_api_hash)
    await client.start(phone=s.tg_phone)
    me = await client.get_me()
    print(f"Вход выполнен: id={me.id} username={getattr(me, 'username', None)} "
          f"name={getattr(me, 'first_name', '')}")
    print(f"Сессия: {s.tg_session_path}")
    print("\nЧаты, в которых состоит аккаунт (первые 30):")
    async for dialog in client.iter_dialogs(limit=30):
        kind = "канал" if getattr(dialog.entity, "broadcast", False) else "чат"
        print(f"  {dialog.id:>16}  {kind:<6} {dialog.name}")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
