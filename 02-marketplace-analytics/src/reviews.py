"""
Отзывы WB и Ozon: выгрузка, ответ, сохранение в файлы.
Запуск:  python reviews.py wb list      — показать неотвеченные отзывы WB
         python reviews.py ozon list    — то же для Ozon
         python reviews.py wb reply ID "текст ответа"
"""
import json
import sys
import time
from pathlib import Path

from mp_core import BASE, http_request, load_keys, log

WB_FEEDBACKS = "https://feedbacks-api.wildberries.ru"
OZON_API = "https://api-seller.ozon.ru"
OUT_WB = BASE / "01_Отзывы_WB"
OUT_OZON = BASE / "02_Отзывы_Ozon"
for d in (OUT_WB, OUT_OZON):
    d.mkdir(parents=True, exist_ok=True)


def _save(items, folder, prefix):
    """Сохраняет выгрузку в датированный JSON + читаемый txt."""
    stamp = time.strftime("%Y-%m-%d_%H-%M")
    js = folder / f"{prefix}_{stamp}.json"
    js.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    txt = folder / f"{prefix}_{stamp}.txt"
    lines = []
    for it in items:
        lines.append("=" * 70)
        lines.append(f"ID: {it.get('id')}  |  оценка: {it.get('rating')}  |  {it.get('created')}")
        lines.append(f"Товар: {it.get('product')}  (артикул {it.get('sku')})")
        if it.get("pros"):
            lines.append(f"Достоинства: {it['pros']}")
        if it.get("cons"):
            lines.append(f"Недостатки: {it['cons']}")
        lines.append(f"Текст: {it.get('text')}")
        if it.get("answer"):
            lines.append(f"ОТВЕТ: {it['answer']}")
        lines.append("")
    txt.write_text("\n".join(lines), encoding="utf-8")
    return js, txt


# ----------------------------- WILDBERRIES -----------------------------

def wb_headers(token):
    return {"Authorization": token}


def wb_all_feedbacks(token, take=100, skip=0):
    """Отзывы магазина. WB ТРЕБУЕТ параметр isAnswered в URL.

    Ошибочно думали, что WB прячет отзывы без текста из isAnswered=false —
    НЕТ, он их отдаёт (проверено: выгрузка 20.09 — 25 отзывов, 18 без текста).
    Запрос БЕЗ isAnswered возвращает 400 «Плохой формат isAnswered».

    Поэтому: isAnswered=false = все неотвеченные, включая бестекстовые.
    """
    url = f"{WB_FEEDBACKS}/api/v1/feedbacks?isAnswered=false&take={take}&skip={skip}"
    return http_request(url, headers=wb_headers(token))


def _fb_to_dict(fb):
    return {
        "id": fb.get("id"),
        "rating": fb.get("productValuation"),
        "created": fb.get("createdDate"),
        "text": fb.get("text") or "",
        "pros": fb.get("pros") or "",
        "cons": fb.get("cons") or "",
        "product": (fb.get("productDetails") or {}).get("productName"),
        "sku": (fb.get("productDetails") or {}).get("nmId"),
        "answer": (fb.get("answer") or {}).get("text") if fb.get("answer") else None,
        "source": "WB",
    }


def wb_unanswered_full(token, take=100, max_pages=20):
    """ВСЕ отзывы без ответа, ВКЛЮЧАЯ отзывы без текста.

    Идём по страницам полного списка и оставляем те, у которых нет answer.
    Возвращает (items, всего_просмотрено).
    """
    out, skip, seen = [], 0, 0
    for _ in range(max_pages):
        res = wb_all_feedbacks(token, take=take, skip=skip)
        data = res.get("data", {})
        batch = data.get("feedbacks") or []
        if not batch:
            break
        for fb in batch:
            seen += 1
            if not fb.get("answer"):          # нет ответа — берём (даже без текста)
                out.append(_fb_to_dict(fb))
        skip += take
        # если получили меньше страницы — это конец
        if len(batch) < take:
            break
    return out, seen


def wb_unanswered(token, is_answered=False, take=100):
    """Список отзывов WB. is_answered=False -> только без ответа."""
    url = (f"{WB_FEEDBACKS}/api/v1/feedbacks"
           f"?isAnswered={str(is_answered).lower()}&take={take}&skip=0")
    res = http_request(url, headers=wb_headers(token))
    out = [_fb_to_dict(fb) for fb in (res.get("data", {}).get("feedbacks") or [])]
    return out, res.get("data", {}).get("countUnanswered")


def wb_reply(token, feedback_id, text):
    """Отвечает на отзыв WB. Лимит площадки: 1000 символов."""
    if len(text) > 1000:
        raise ValueError(f"WB: ответ длиннее 1000 символов ({len(text)})")
    return http_request(
        f"{WB_FEEDBACKS}/api/v1/feedbacks/answer",
        method="POST",
        headers=wb_headers(token),
        payload={"id": feedback_id, "text": text},
    )


def wb_check(token):
    """Проверка, что токен живой."""
    return http_request(f"{WB_FEEDBACKS}/api/v1/feedbacks/count-unanswered",
                        headers=wb_headers(token))


# -------------------------------- OZON --------------------------------

def ozon_headers(client_id, api_key):
    return {"Client-Id": client_id, "Api-Key": api_key}


def ozon_unanswered(client_id, api_key, limit=100, only_unanswered=False):
    """
    Отзывы Ozon. Ozon принимает limit строго 20..100 и отдаёт пачками с last_id.

    only_unanswered=True -> фильтруем локально (метод v1/review/count недоступен
    на базовом тарифе, поэтому считаем сами).
    """
    if not 20 <= limit <= 100:
        limit = 100
    url = f"{OZON_API}/v1/review/list"
    out, last_id, guard = [], "", 0
    while guard < 50:  # защита от бесконечного цикла
        guard += 1
        payload = {"limit": limit, "sort_dir": "DESC"}
        if last_id:
            payload["last_id"] = last_id
        res = http_request(url, method="POST",
                           headers=ozon_headers(client_id, api_key),
                           payload=payload)
        batch = res.get("reviews") or []
        if not batch:
            break
        for rv in batch:
            answered = bool((rv.get("comment") or "").strip()) or \
                       (rv.get("status") or "").lower() in ("processed", "answered")
            if only_unanswered and answered:
                continue
            out.append({
                "id": rv.get("id"),
                "rating": rv.get("rating"),
                "created": rv.get("published_at"),
                "text": rv.get("text") or "",
                "pros": rv.get("pros") or "",
                "cons": rv.get("cons") or "",
                "product": (rv.get("item") or {}).get("name"),
                "sku": (rv.get("item") or {}).get("sku"),
                "answer": rv.get("comment"),
                "status": rv.get("status"),
                "answered": answered,
                "source": "OZON",
            })
        last_id = res.get("last_id") or ""
        if not last_id:
            break
    return out, len(out)


def ozon_reply(client_id, api_key, review_id, text):
    """Отвечает на отзыв Ozon."""
    return http_request(
        f"{OZON_API}/v1/review/comment/create",
        method="POST",
        headers=ozon_headers(client_id, api_key),
        payload={"review_id": str(review_id), "text": text, "mark_review_as_processed": True},
    )


def ozon_check(client_id, api_key):
    """Проверка ключей Ozon — запрашиваем 1 отзыв."""
    return http_request(f"{OZON_API}/v1/review/list", method="POST",
                        headers=ozon_headers(client_id, api_key),
                        payload={"limit": 1, "sort_dir": "DESC"})


# ------------------------------- CLI -------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    keys = load_keys()
    mp = sys.argv[1].lower()
    action = sys.argv[2].lower() if len(sys.argv) > 2 else "list"

    if mp == "wb":
        if not keys["wb_token"]:
            log("нет токена WB в keys.ini", "reviews")
            return 1
        if action == "list":
            items, count = wb_unanswered(keys["wb_token"])
            if items:
                js, txt = _save(items, OUT_WB, "WB_отзывы")
                log(f"WB: {len(items)} без ответа (всего {count}). Файл: {txt.name}", "reviews")
            else:
                log("WB: неотвеченных отзывов нет", "reviews")
            for it in items[:10]:
                print(f"  [{it['id']}] {it['rating']}★ {it['product']}: {it['text'][:70]}")
        elif action == "reply":
            log(str(wb_reply(keys["wb_token"], sys.argv[3], sys.argv[4])), "reviews")

    elif mp == "ozon":
        if not (keys["ozon_client_id"] and keys["ozon_api_key"]):
            log("нет ключей Ozon в keys.ini", "reviews")
            return 1
        if action == "list":
            items, _ = ozon_unanswered(keys["ozon_client_id"], keys["ozon_api_key"])
            if items:
                js, txt = _save(items, OUT_OZON, "Ozon_отзывы")
                log(f"Ozon: {len(items)} без ответа. Файл: {txt.name}", "reviews")
            else:
                log("Ozon: неотвеченных отзывов нет", "reviews")
            for it in items[:10]:
                print(f"  [{it['id']}] {it['rating']}★ {it['product']}: {it['text'][:70]}")
        elif action == "reply":
            log(str(ozon_reply(keys["ozon_client_id"], keys["ozon_api_key"],
                               sys.argv[3], sys.argv[4])), "reviews")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
