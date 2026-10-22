"""
Hermes Marketplace — ядро: ключи + HTTP-клиент с ЗАЩИТОЙ ОТ БЛОКИРОВОК.

ВСЕ запросы к WB идут через http_request(), который:
  1. Соблюдает минимальный интервал между запросами (лимиты.py)
  2. При 429 ждёт ровно сколько просит WB
  3. При сетевом сбое делает ДОЛГУЮ паузу, а не долбит
  4. После N сбоев полностью останавливается

Только стандартная библиотека Python.
"""
import configparser
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import лимиты

BASE = Path(r"D:\файлы Гермес\04_Маркетплейсы")
KEYS_FILE = BASE / "keys.ini"
LOG_DIR = BASE / "04_Логи"
LOG_DIR.mkdir(parents=True, exist_ok=True)


class ApiError(Exception):
    """Ошибка ответа от API маркетплейса."""

    def __init__(self, status, body, url):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"HTTP {status} <- {url}\n{body[:1000]}")


class NetworkError(Exception):
    """Сеть недоступна (WB блокирует или обрыв)."""


def load_keys():
    """Читает keys.ini. Возвращает dict с ключами."""
    if not KEYS_FILE.exists():
        raise FileNotFoundError(f"Нет файла с ключами: {KEYS_FILE}")
    cfg = configparser.ConfigParser()
    cfg.read(KEYS_FILE, encoding="utf-8")
    out = {}

    def get(section, option):
        if not cfg.has_section(section):
            return None
        val = cfg.get(section, option, fallback="").strip().strip('"').strip("'")
        if not val or val.upper().startswith("ВСТАВЬТЕ"):
            return None
        return val

    out["wb_token"] = get("WB", "token")
    out["wb_token_comet"] = get("WB", "token_comet")
    out["wb_token_full"] = get("WB", "token_full")
    out["wb_token_analytics"] = get("WB", "token_analytics")
    out["ozon_client_id"] = get("OZON", "client_id")
    out["ozon_api_key"] = get("OZON", "api_key")
    return out


def _is_network_fail(exc):
    """Определяет, что это временный сетевой сбой, а не отказ логики."""
    msg = str(exc).lower()
    return any(k in msg for k in (
        "handshake", "timed out", "timeout", "eof", "connection",
        "reset", "ssl", "urlopen", "broken pipe", "remote end",
        "unreachable", "refused",
    ))


def http_request(url, method="GET", headers=None, payload=None, timeout=30,
                 respect_limits=True, retries=3):
    """
    HTTP-запрос с полной защитой от блокировок.

    respect_limits=False — только для тестов связи (не тратит лимит).
    При 429: ждёт указанное WB время и повторяет.
    При сетевом сбое: долгая пауза, при N сбоях — исключение.
    """
    if respect_limits:
        лимиты.wait_for_slot()

    data = None
    hdrs = {"Accept": "application/json", "User-Agent": "Hermes-Marketplace/1.0"}
    if headers:
        hdrs.update(headers)
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"

    net_fails = 0
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if respect_limits:
                    лимиты.mark_request(url)
                if not raw.strip():
                    return {}
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return {"_raw": raw}

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")

            if e.code == 429:
                wait = 780
                for h in ("X-RateLimit-Retry", "Retry-After", "X-RateLimit-Reset"):
                    raw = e.headers.get(h)
                    if raw and str(raw).strip().isdigit():
                        wait = int(raw)
                        break
                лимиты.mark_429(wait)
                if attempt < retries:
                    print(f"  [лимит WB] ждём {wait/60:.1f} мин...")
                    time.sleep(wait + 15)
                    continue
                raise ApiError(e.code, body, url) from None

            if e.code in (500, 502, 503, 504) and attempt < retries:
                time.sleep(30 * attempt)
                continue
            raise ApiError(e.code, body, url) from None

        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if not _is_network_fail(e):
                raise
            net_fails += 1
            лимиты.mark_network_fail(net_fails)
            if net_fails > лимиты.RETRY_ON_NET_MAX:
                raise NetworkError(
                    f"WB недоступен после {net_fails} попыток. "
                    f"Возможна блокировка IP. Прекращаю попытки."
                ) from None
            continue

    raise NetworkError(f"Не удалось выполнить запрос: {url}")


def log(msg, name="marketplace"):
    """Лог с датой в файл."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line)
    with open(LOG_DIR / f"{name}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")
