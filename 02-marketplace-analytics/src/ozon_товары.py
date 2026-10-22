"""Ozon: названия и картинки товаров — чтобы отобрать нужные категории."""
import json, pathlib, configparser
from mp_core import http_request, ApiError, NetworkError

BASE = pathlib.Path(r"D:\файлы Гермес\04_Маркетплейсы")
OUT = BASE / "03_Выгрузки"
cfg = configparser.ConfigParser()
cfg.read(str(BASE / "keys.ini"), encoding="utf-8")
cid = (cfg.get("OZON", "client_id", fallback="") or "").strip().strip('"')
key = (cfg.get("OZON", "api_key", fallback="") or "").strip().strip('"')

ids = [x["product_id"] for x in json.loads((OUT / "Товары_Ozon.json").read_text(encoding="utf-8"))]
print("товаров на Ozon:", len(ids))

H = {"Client-Id": cid, "Api-Key": key}
items = []
for i in range(0, len(ids), 100):
    chunk = ids[i:i + 100]
    try:
        r = http_request("https://api-seller.ozon.ru/v3/product/info/list", method="POST",
                         headers=H, payload={"product_id": chunk},
                         respect_limits=False, retries=1)
    except (ApiError, NetworkError) as e:
        print("  ошибка:", str(e)[:160]); break
    part = r.get("items") or []
    items.extend(part)
    print("  получено: %d" % len(items))

slim = []
for it in items:
    imgs = it.get("images") or []
    slim.append({
        "product_id": it.get("id") or it.get("product_id"),
        "offer_id": it.get("offer_id"),
        "name": it.get("name"),
        "price": (it.get("price") or ""),
        "images": [im.get("file") if isinstance(im, dict) else im for im in imgs],
        "image_count": len(imgs),
    })
(OUT / "Товары_Ozon_полные.json").write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
print("\nсохранено:", len(slim))

KEY = {
    "таблетки для туалета": ["унитаз", "туалет"],
    "газовые плиты": ["газов", "плита"],
    "кофемашины": ["кофемаш", "кофевар", "кофе"],
    "очиститель для посудомоечной": ["посудомоеч", "посудомой"],
    "вентиляторы": ["вентилятор"],
}
for cat, kws in KEY.items():
    hit = [x for x in slim if any(k in (x["name"] or "").lower() for k in kws)]
    print("\n=== %s: %d ===" % (cat, len(hit)))
    for x in hit[:12]:
        print("   %-56s | фото %d" % ((x["name"] or "")[:56], x["image_count"]))
