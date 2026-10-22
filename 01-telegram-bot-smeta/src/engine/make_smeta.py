# -*- coding: utf-8 -*-
"""
Запуск детерминированного расчёта сметы на произвольном заказе.

Раньше здесь выполнялся зашитый генератор конкретной кухни
(`13_Кухня_2уровневая\\генератор_сметы_кухня.py`) с подменой путей — из-за него
в смету протекали константы чужого проекта (декор Egger «Дикий дуб», столешница
4100×600×38 ×2, 15/11 модулей, техника Teka/Franke). Теперь расчёт целиком
детерминированный: см. engine/estimate.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from estimate import build as _build_estimate

HERE = Path(__file__).resolve().parent

# прайс поставщика — ищем по известным адресам
PRICE_CANDIDATES = [
    Path(r"D:\файлы Гермес\Downloads\Для ИИ стоимость.xlsx"),
    Path(r"D:\KimiData\kimi\Workspaces\мебель1\Для ИИ стоимость.xlsx"),
    Path(r"D:\файлы Гермес\Для ИИ стоимость.xlsx"),
    Path(r"D:\файлы Гермес\13_Кухня_2уровневая\Для ИИ стоимость.xlsx"),
    Path("/opt/botsmeta/generator/price.xlsx"),   # сервер RUVDS
    Path("/opt/botsmeta/price.xlsx"),
]


def find_price() -> Path:
    for p in PRICE_CANDIDATES:
        if p.exists():
            return p
    raise FileNotFoundError(
        "Не нашёл прайс «Для ИИ стоимость.xlsx». Положи его в одну из папок:\n  "
        + "\n  ".join(str(p) for p in PRICE_CANDIDATES)
    )


def build(order_dir: Path, detail_json: Path, out_name: str = "СМЕТА.xlsx",
          project_name: str = "", source_filenames=None) -> dict:
    """Считает смету по файлу деталей (детерминированно).

    order_dir — рабочая папка заказа (туда ляжет xlsx);
    detail_json — путь к json с деталями (от parse_cut);
    project_name — название проекта из имени файла заявки (не зашито);
    source_filenames — исходные имена файлов раскроя (для заголовков).

    Возвращает {"xlsx", "ok", "errors", "summary", "assumptions"}.
    """
    order_dir = Path(order_dir)
    order_dir.mkdir(parents=True, exist_ok=True)
    detail_json = Path(detail_json)
    if not detail_json.exists():
        raise FileNotFoundError(f"нет файла деталей: {detail_json}")

    detail = json.loads(detail_json.read_text(encoding="utf-8"))
    price = find_price()
    res = _build_estimate(detail, order_dir, out_name,
                          project_name=project_name,
                          source_filenames=source_filenames or [],
                          price_path=price)
    return res


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(HERE))
    from parse_cut import parse_cut, save

    src_dir = Path(r"C:\Users\druzh\Downloads")
    files = [src_dir / "Кухня-новая-3010 - детали.txt"]
    order = HERE.parent / "orders" / "тест_эталон"
    order.mkdir(parents=True, exist_ok=True)

    print("1) разбираю раскрой")
    d = parse_cut(files)
    for k, v in d["stats"].items():
        print(f"     {k:22} {v}")
    det = save(d, order / "детали.json")

    print("\n2) считаю смету")
    r = build(order, det, out_name="СМЕТА_тест.xlsx",
              project_name="Кухня-новая-3010",
              source_filenames=[f.name for f in files])
    print("   xlsx:", r["xlsx"])
    print("   ok:", r["ok"], "| ошибок:", r["errors"])
    for k, v in r["summary"].items():
        if isinstance(v, dict):
            print(f"     {k}: {v}")
        else:
            print(f"     {k}: {v}")
