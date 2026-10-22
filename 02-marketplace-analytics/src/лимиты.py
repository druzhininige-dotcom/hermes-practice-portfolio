"""
=== ЗАЩИТА ОТ БЛОКИРОВОК WB ===

Этот модуль — "предохранитель" для всех запросов к WB API.
Задача: сделать попадание в блокировку ФИЗИЧЕСКИ НЕВОЗМОЖНЫМ.

Как WB блокирует (что мы выяснили опытным путём):
  1. Лимит на методы отзывов: 1 запрос / ~11.5 мин (заголовок X-RateLimit-Retry)
  2. При частых нарушениях — блокировка IP: SSL handshake timeout,
     то есть сервер молча роняет соединение, не отвечая даже 429.
  3. Блокировка держится часами и продлевается при новых попытках.

Правила этого модуля:
  - МИНИМАЛЬНЫЙ интервал между ЛЮБЫМИ запросами: настраивается
  - Никаких параллельных запросов (только последовательно)
  - При 429 — ждать ровно сколько сказал WB, плюс запас
  - При сетевом сбое — ждать ДОЛГО (не долбить!)
  - Ведётся журнал всех запросов в файл для аудита
  - При превышении лимита попыток — полная остановка, а не долбёжка
"""
import json
import time
from pathlib import Path

BASE = Path(r"D:\файлы Гермес\04_Маркетплейсы")
STATE = BASE / "04_Логи" / "состояние_лимитов.json"
JOURNAL = BASE / "04_Логи" / "журнал_запросов.log"

# ============ НАСТРОЙКИ БЕЗОПАСНОСТИ ============
# Лимит WB на отзывы: 1 запрос / ~11.5 мин. Берём с запасом.
MIN_INTERVAL = 780          # 13 минут между запросами (запас 1.5 мин)
RETRY_ON_429_MAX = 2        # максимум повторов при 429
RETRY_ON_NET_MAX = 3        # максимум повторов при сетевом сбое
NET_RETRY_WAIT = 900        # 15 минут между попытками при сбое сети
# ===============================================


def _load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_request": 0, "history": [], "blocked_until": 0}


def _save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    # история — последние 200 записей
    s["history"] = s.get("history", [])[-200:]
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def _journal(msg):
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {msg}\n")


def wait_for_slot():
    """
    Блокирует выполнение, пока не пройдёт минимальный интервал.
    Возвращает число секунд ожидания.
    """
    s = _load_state()
    now = time.time()

    # если была блокировка — ждём её окончания
    if s.get("blocked_until", 0) > now:
        w = s["blocked_until"] - now
        _journal(f"ОЖИДАНИЕ блокировки: {w/60:.1f} мин")
        time.sleep(w)
        now = time.time()

    since = now - s.get("last_request", 0)
    if since < MIN_INTERVAL:
        w = MIN_INTERVAL - since
        _journal(f"ОЖИДАНИЕ интервала: {w:.0f} c (прошло {since:.0f} c из {MIN_INTERVAL})")
        time.sleep(w)
        return w
    return 0


def mark_request(url):
    """Отмечает, что запрос был сделан. Вызывать СРАЗУ после запроса."""
    s = _load_state()
    s["last_request"] = time.time()
    s["history"].append({
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "url": url[:120],
    })
    _save_state(s)


def mark_429(wait_seconds):
    """WB сказал подождать. Уважаем с запасом 10%."""
    s = _load_state()
    w = int(wait_seconds * 1.1) + 10
    s["last_request"] = time.time() + w
    _journal(f"429 от WB: пауза {w} c ({w/60:.1f} мин)")
    _save_state(s)
    return w


def mark_network_fail(attempt):
    """
    Сетевой сбой. НЕ долбим: пауза растёт, при превышении — стоп.
    Возвращает: секунды ожидания или None (надо остановиться).
    """
    s = _load_state()
    if attempt > RETRY_ON_NET_MAX:
        s["blocked_until"] = time.time() + 3600  # час полного молчания
        _save_state(s)
        _journal(f"СТОП: {RETRY_ON_NET_MAX} сетевых сбоев подряд. "
                 f"Молчание 1 час, чтобы не продлевать блокировку.")
        return None
    w = NET_RETRY_WAIT * attempt
    _journal(f"Сетевой сбой #{attempt}: пауза {w//60} мин")
    time.sleep(w)
    return w


def status():
    """Текущее состояние — можно ли делать запрос."""
    s = _load_state()
    now = time.time()
    out = []
    if s.get("blocked_until", 0) > now:
        out.append(f"ЗАБЛОКИРОВАНО: ещё {(s['blocked_until']-now)/60:.1f} мин")
    left = MIN_INTERVAL - (now - s.get("last_request", 0))
    if left > 0:
        out.append(f"Интервал: ждать {left:.0f} c ({left/60:.1f} мин)")
    else:
        out.append("Можно делать запрос")
    out.append(f"Запросов в истории: {len(s.get('history', []))}")
    last = s.get("history", [])
    if last:
        out.append(f"Последний: {last[-1]['time']} -> {last[-1]['url']}")
    return "\n".join(out)


if __name__ == "__main__":
    print(status())
