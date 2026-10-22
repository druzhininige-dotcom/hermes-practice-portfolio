"""Проверка Telegram-настроек: кто бот, какие чаты и топики он видит.

    python scripts/telegram_setup_check.py                  # токен из .env (BOT_TOKEN)
    python scripts/telegram_setup_check.py --token 123:ABC
    python scripts/telegram_setup_check.py --api-check 12345678:abcdef...

Что делает:
  1) getMe — проверяет токен и печатает бота;
  2) getUpdates — показывает чаты, где бот получал сообщения, их id и id топиков;
  3) подсказывает, какие строки вписать в .env.

Токен НЕ печатается и никуда не отправляется, кроме api.telegram.org.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def api(token: str, method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"ok": False, "description": f"HTTP {exc.code}: {body[:200]}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "description": f"сеть недоступна: {exc.reason}"}


def token_from_env() -> str:
    env = BASE / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("BOT_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default="", help="токен бота (по умолчанию из .env)")
    ap.add_argument("--api-check", default="", help="api_id:api_hash для проверки формата")
    args = ap.parse_args()

    if args.api_check:
        parts = args.api_check.split(":", 1)
        ok = len(parts) == 2 and parts[0].isdigit() and len(parts[1]) == 32
        print(("OK   " if ok else "ПРОВАЛ ") + "api_id:api_hash — формат "
              "(число:32 hex-символа)" + ("" if ok else f", получили {len(args.api_check)} символов"))
        return 0 if ok else 1

    token = args.token or token_from_env()
    if not token:
        print("Нет токена: заполните BOT_TOKEN в .env или передайте --token", file=sys.stderr)
        return 2

    me = api(token, "getMe")
    if not me.get("ok"):
        print(f"Токен не принят Telegram: {me.get('description')}", file=sys.stderr)
        return 1
    bot = me["result"]
    print(f"Бот: @{bot.get('username')} · id {bot['id']} · имя «{bot.get('first_name')}»")
    print(f"Ссылка: https://t.me/{bot.get('username')}\n")

    upd = api(token, "getUpdates", timeout=0, limit=100, allowed_updates='["message","channel_post"]')
    if not upd.get("ok"):
        print(f"getUpdates не сработал: {upd.get('description')}", file=sys.stderr)
        return 1

    chats: dict[int, dict] = {}
    for item in upd.get("result", []):
        msg = item.get("message") or item.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if not chat.get("id"):
            continue
        rec = chats.setdefault(chat["id"], {
            "title": chat.get("title") or chat.get("username") or "—",
            "type": chat.get("type"), "topics": set()})
        if msg.get("is_topic_message") and msg.get("message_thread_id"):
            rec["topics"].add(int(msg["message_thread_id"]))

    if not chats:
        print("Чатов пока нет. Добавьте бота в группу ЛИДЫ, напишите там любое сообщение "
              "(и по сообщению в каждую тему, если нужны топики) и запустите снова.")
        return 0

    print("Чаты, где бот получал сообщения:")
    for chat_id, rec in chats.items():
        topics = ", ".join(str(t) for t in sorted(rec["topics"])) or "—"
        print(f"  {chat_id:>16}  {rec['type']:<12} «{rec['title']}»  топики: {topics}")

    main_chat = max(chats.items(), key=lambda kv: len(kv[1]["topics"]))[0]
    print("\nВпишите в .env:")
    print(f"LEADS_GROUP_ID={main_chat}")
    for slug in ("mebel", "mp", "log", "ai"):
        print(f"# {slug}: tg_topic_id — id темы из строки выше (если разводите по топикам)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
