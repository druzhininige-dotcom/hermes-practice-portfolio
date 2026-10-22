"""WB: отбор SKU по категориям + один запрос аналитики (после паузы) ради отзывов."""
import json, pathlib, time, configparser
from mp_core import http_request, ApiError, NetworkError

BASE = pathlib.Path(r"D:\файлы Гермес\04_Маркетплейсы")
data = json.loads((BASE / "03_Выгрузки" / "Карточки_WB_full.json").read_text(encoding="utf-8"))

TOY = ["игрушк", "снеговик", "дед мороз", "пончик", "пингвин", "оленен",
       "мишка", "панда", "печеньк", "пирожен", "кексик", "мягк"]
AROMA = ["аромат", "диффузор", "спрей"]
TABL = ["таблетк", "концентрат", "пластины", "пленки", "мыло пенка"]

def pick(kws):
    return [c for c in data if any(k in (c["title"] or "").lower() for k in kws)]

toys, aroma, tabl = pick(TOY), pick(AROMA), pick(TABL)
print("игрушки:", len(toys), "| ароматизаторы:", len(aroma), "| таблетки/концентраты:", len(tabl))
for name, group in (("ИГРУШКИ", toys), ("АРОМАТИЗАТОРЫ", aroma), ("ТАБЛЕТКИ", tabl)):
    print("\n=== %s ===" % name)
    for c in group:
        print("   %-11s %-52s фото %d" % (c["nmID"], (c["title"] or "")[:52], c["photo_count"]))

allnm = list({c["nmID"] for c in toys + aroma + tabl})
print("\nвсего артикулов для аналитики:", len(allnm))

print("\nпауза 300 с (лимит аналитики WB)...")
time.sleep(300)

cfg = configparser.ConfigParser(); cfg.read(str(BASE / "keys.ini"), encoding="utf-8")
ana = (cfg.get("WB", "token_analytics", fallback="") or "").strip().strip('"')
try:
    r = http_request("https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products",
                     method="POST", headers={"Authorization": ana},
                     payload={"selectedPeriod": {"start": "2026-09-01", "end": "2026-10-02"},
                              "nmIds": allnm[:100], "limit": 100, "page": 1},
                     respect_limits=False, retries=1)
except (ApiError, NetworkError) as e:
    raise SystemExit("ошибка аналитики: %s" % str(e)[:200])

cards = (r.get("data") or {}).get("products") or []
print("аналитика вернула карточек:", len(cards))
if cards:
    c0 = cards[0]
    print("\nключи product:", list((c0.get("product") or {}).keys()))
    st = c0.get("statistic") or {}
    sel = st.get("selected") or {}
    print("ключи statistic:", list(st.keys()))
    print("ключи selected:", list(sel.keys()))
    print("\nПОЛНЫЙ первый product:", json.dumps(c0.get("product"), ensure_ascii=False)[:700])
    (BASE / "03_Выгрузки" / "WB_воронка.json").write_text(
        json.dumps(cards, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nсохранено в WB_воронка.json")
