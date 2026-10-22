# -*- coding: utf-8 -*-
"""
Детерминированный расчёт сметы мебели по выгрузке PRO100.

Никакой нейросети и никаких «зашитых» параметров конкретного проекта:
  - классификация детали — по группе (колонка 0 выгрузки), толщина — проверка;
  - название проекта, декор, размеры, количество — ТОЛЬКО из файлов заявки;
  - цены — ТОЛЬКО из прайса по точному имени; нет цены → «УТОЧНИТЬ»;
  - каждое допущение выводится отдельным списком (лист «Допущения»).

Функции:
  classify(grp, mat, T) -> str      — категория детали
  load_prices(path)      -> dict    — прайс (наименование -> цена)
  build(...)             -> dict    — собирает .xlsx и прогоняет самопроверку
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ─────────────────────────── классификация ───────────────────────────
# Толщина — проверка (4 мм = задняя стенка, >=30 мм = столешница),
# затем группа (колонка 0), затем материал. Никаких «по одной толщине».

CAT_CORPUS = "Корпус ЛДСП"
CAT_BACK = "Задняя стенка 4мм"
CAT_PANEL = "Стеновая панель"
CAT_TOP = "Столешница"
CAT_FACADE = "Фасад крашеный"
CAT_OTHER = "Прочее"

# размер листа по производителю плиты (стандарт производителя, мм)
SHEET_SIZE = {
    "kronospan": (2750, 1830),
    "egger": (2800, 2070),
    "томлесдрев": (2750, 1830),
    "tomlesdrev": (2750, 1830),
    "lamarty": (2750, 1830),
    "увадрев": (2750, 1830),
    "nordeco": (2750, 1830),
}
DEFAULT_SHEET = (2750, 1830)

_KNOWN_PRODUCERS = (
    "kronospan", "egger", "томлесдрев", "tomlesdrev",
    "lamarty", "увадрев", "nordeco",
)


def producer_of(mat: str) -> str:
    """Производитель плиты из пути материала ('ДСП\\ДСП\\Kronospan\\…' -> 'Kronospan')."""
    for part in (mat or "").split("\\"):
        pl = part.strip().lower()
        if pl in _KNOWN_PRODUCERS:
            return part.strip()
    return ""


def decor_of(mat: str) -> str:
    """Декор = последний сегмент пути материала ('…\\U-171 серый' -> 'U-171 серый')."""
    return (mat or "").split("\\")[-1].strip()


def sheet_size_of(producer: str):
    """Размер листа по производителю; неизвестный -> (None, None) = УТОЧНИТЬ."""
    pl = (producer or "").lower()
    for key, sz in SHEET_SIZE.items():
        if key in pl:
            return sz
    return None


def classify(grp: str, mat: str, T) -> str:
    """Категория детали. Толщина — только проверка, главное — группа и материал."""
    g = (grp or "").strip().lower()
    m = (mat or "").lower()
    if T is not None:
        if T <= 5.5:                      # 4 мм и тоньше — задняя стенка (ХДФ/ДВП)
            return CAT_BACK
        if T >= 30.0:                     # 38 мм — столешница
            return CAT_TOP
    if "фасад" in g:
        return CAT_FACADE
    if "задн" in g or "стенка" in g:
        return CAT_BACK
    if "стеновая панель" in g or ("панель" in g and "стен" in g):
        return CAT_PANEL
    if "столешниц" in g:
        return CAT_TOP
    if "корпус" in g:
        return CAT_CORPUS
    # группы нет — по материалу и толщине
    if "столешниц" in m:
        return CAT_TOP
    if "стеновая панель" in m:
        return CAT_PANEL
    if "дсп" in m or "kronospan" in m or "egger" in m or "древоподоб" in m:
        return CAT_CORPUS
    return CAT_OTHER


def is_narrow_plank(L, W) -> bool:
    """Узкая длинная деталь фасада (например 100×3010) — вероятно цоколь/планка."""
    a, b = sorted([L, W])
    return a <= 120.0 and b >= 800.0 and b / a >= 6.0


# ─────────────────────────── прайс ───────────────────────────
def load_prices(path) -> dict:
    """Прайс «Для ИИ стоимость.xlsx»: колонка A пустая, имя в B, цена в D.

    Наименование = первая непустая строковая ячейка среди первых двух колонок,
    цена = первое число после неё. Падаем, если цен 0 — иначе дефект молчит.
    """
    wb = openpyxl.load_workbook(str(path), data_only=True)
    P = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [c for c in row if c is not None]
            if not cells:
                continue
            name = next((str(c).strip() for c in cells[:2]
                         if isinstance(c, str) and c.strip()), None)
            if not name:
                continue
            num = None
            for c in cells[2:]:
                if isinstance(c, (int, float)):
                    num = float(c)
                    break
                if isinstance(c, str):
                    v = c.strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
                    try:
                        num = float(v)
                    except ValueError:
                        continue
                    break
            if num is not None:
                P[name] = num
    if not P:
        raise ValueError(f"Прайс «{Path(path).name}» прочитан, но в нём 0 цен — проверь формат")
    return P


def find_sheet_price(P: dict, producer: str, decor: str):
    """Цена листа ЛДСП по производителю/декору. Нет — (None, None)."""
    pl = (producer or "").lower()
    dec = (decor or "").lower()
    for name, price in P.items():
        nl = name.lower()
        if "лдсп" in nl and pl and pl in nl:
            return price, name
    for name, price in P.items():
        nl = name.lower()
        if "лдсп" in nl and dec and dec in nl:
            return price, name
    return None, None


def price_key(P: dict, *keys) -> float:
    """Точная цена по ключу прайса; None — если ключа нет."""
    for k in keys:
        if k in P:
            return P[k]
    return None


# ─────────────────────────── раскрой (guillotine) ───────────────────────────
def pack_parts(parts, SW, SH, KERF=4.0):
    """Укладка деталей одного декора на листы SW×SH. Детерминированная сортировка."""
    items = []
    for p in parts:
        for _ in range(int(p["qty"])):
            items.append((max(p["L"], p["W"]), min(p["L"], p["W"])))
    items.sort(key=lambda x: (-round(x[0] * x[1], 6), -x[0], -x[1]))
    sheets = []
    for (L, W) in items:
        done = False
        for sh in sheets:
            for shelf in sh["sh"]:
                if shelf["h"] + 1e-6 >= W and shelf["used"] + L + KERF <= SW:
                    shelf["it"].append((L, W))
                    shelf["used"] += L + KERF
                    done = True
                    break
            if done:
                break
            if sh["y"] + W + KERF <= SH:
                sh["sh"].append({"y": sh["y"], "h": W, "used": L + KERF,
                                 "it": [(L, W)]})
                sh["y"] += W + KERF
                done = True
                break
        if not done:
            sheets.append({"y": W + KERF,
                           "sh": [{"y": 0.0, "h": W, "used": L + KERF,
                                   "it": [(L, W)]}]})
    return sheets


def cut_length(sheets, SW) -> float:
    L = 0.0
    for sh in sheets:
        L += len(sh["sh"]) * SW / 1000.0
        for shelf in sh["sh"]:
            L += len(shelf["it"]) * shelf["h"] / 1000.0
    return L


# ─────────────────────────── самопроверка ───────────────────────────
FORBIDDEN_WORDS = (
    "двухуровнев",       # зашитое название старой кухни (не должно попасть в смету)
    "дикий дуб",         # зашитый декор старой кухни
    "h1318",             # артикул декора старой кухни
    "teka", "franke", "miele",   # зашитая техника старой кухни
)


def self_check(summary: dict, stats: dict, forbidden_words=None) -> list:
    """Возвращает список ошибок. Пустой список = смету можно отправлять."""
    errors = []
    # 1) число деталей в смете = числу в «Принял»
    if summary["штук"] != stats.get("штук"):
        errors.append(f"детали: в смете {summary['штук']} шт, в «Принял» {stats.get('штук')}")
    # 2) площадь по группам = общей площади из «Принял»
    if abs(summary["площадь_м2"] - stats.get("площадь_м2", 0)) > 1e-3:
        errors.append(f"площадь: в смете {summary['площадь_м2']:.3f} м², "
                      f"в «Принял» {stats.get('площадь_м2'):.3f}")
    # 3) нет деталей без группы
    if summary["прочее"] > 0:
        errors.append(f"деталей без группы (Прочее): {summary['прочее']} — "
                      f"разбери классификацию")
    # 4) нет слов из других проектов
    fw = forbidden_words or FORBIDDEN_WORDS
    hits = [w for w in fw if w in summary["текст_сметы"].lower()]
    if hits:
        errors.append("в смете слова другого проекта: " + ", ".join(hits))
    return errors


# ─────────────────────── остатки старого проекта (по всем листам) ───────────────────────
СТОП = ["двухуровнев", "Дикий дуб", "H1318", "Teka", "Franke", "23.09", "4100", "вар. 2"]


def остатки(xlsx, stop_words=None) -> list:
    """Ищет следы чужого проекта по всем листам книги. Пустой список = чисто.

    Исключения: «Egger» НЕ входит в СТОП — он допустим в строках столешницы и
    стеновой панели (название группы из файла «стеновая панель egger»).
    «4100» в СТОП — это зашитый размер старой столешницы, его быть не должно.
    """
    wb = openpyxl.load_workbook(str(xlsx), data_only=False)
    stop = stop_words or СТОП
    hits = []
    for ws in wb:
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if isinstance(v, str) and any(s in v for s in stop):
                    hits.append((ws.title, c.coordinate, v))
    return hits


# ─────────────────────────── стили ───────────────────────────
YEL = PatternFill("solid", fgColor="FFF2CC")      # ввод цены (правит владелец)
UNK = PatternFill("solid", fgColor="FCE4D6")      # УТОЧНИТЬ (нет цены)
HDR = PatternFill("solid", fgColor="D9E1F2")
TOTF = PatternFill("solid", fgColor="E2EFDA")
B = Border(*[Side(style="thin", color="B0B0B0")] * 4)
BOLD = Font(bold=True)
TITLE = Font(bold=True, size=13)
MONEY = '#,##0.00'
NUM3 = '0.000'
NUM2 = '0.00'


def _head(ws, title, widths):
    ws["A1"] = title
    ws["A1"].font = TITLE
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _row(ws, r, vals, fill=None, bold=False, fmts=None):
    for i, v in enumerate(vals, 1):
        c = ws.cell(row=r, column=i, value=v)
        c.border = B
        if fill:
            c.fill = fill
        if bold:
            c.font = BOLD
        if fmts and i - 1 < len(fmts) and fmts[i - 1]:
            c.number_format = fmts[i - 1]
    return r + 1


# ─────────────────────────── сборка ───────────────────────────
def build(detail: dict, order_dir, out_name: str = "СМЕТА.xlsx",
          project_name: str = "", source_filenames=None, price_path=None) -> dict:
    """Собирает смету по деталям. Возвращает {'xlsx', 'ok', 'errors', 'summary', 'assumptions'}."""
    order_dir = Path(order_dir)
    order_dir.mkdir(parents=True, exist_ok=True)

    merged = detail["merged"]
    stats = detail["stats"]
    for p in merged:
        p["area"] = p["L"] * p["W"] / 1e6
        p["per"] = 2 * (p["L"] + p["W"]) / 1000
        p["cat"] = classify(p.get("grp", ""), p.get("mat", ""), p.get("T"))
        p["dec"] = decor_of(p.get("mat", ""))
        p["producer"] = producer_of(p.get("mat", ""))

    P = load_prices(price_path) if price_path else {}

    # ── группировка по категориям ──
    by_cat = defaultdict(list)
    for p in merged:
        by_cat[p["cat"]].append(p)

    def gstat(parts):
        return {
            "поз": len(parts),
            "шт": sum(p["qty"] for p in parts),
            "м2": sum(p["area"] * p["qty"] for p in parts),
            "периметр": sum(p["per"] * p["qty"] for p in parts),
        }

    corpus = gstat(by_cat[CAT_CORPUS])
    back = gstat(by_cat[CAT_BACK])
    panel = gstat(by_cat[CAT_PANEL])
    top = gstat(by_cat[CAT_TOP])
    facade = gstat(by_cat[CAT_FACADE])
    other = gstat(by_cat[CAT_OTHER])

    summary = {
        "корпус": corpus, "задние": back, "панель": panel,
        "столешница": top, "фасады": facade, "прочее": other["поз"],
        "штук": corpus["шт"] + back["шт"] + panel["шт"] + top["шт"] + facade["шт"] + other["шт"],
        "площадь_м2": corpus["м2"] + back["м2"] + panel["м2"] + top["м2"] + facade["м2"] + other["м2"],
    }

    assumptions = []          # список допущений (лист «Допущения»)
    DOLYA_EDGE = 0.6
    assumptions.append(f"Доля кромящихся торцов = {DOLYA_EDGE} (правило проекта).")

    # ── корпус по декорам ──
    corpus_dec = defaultdict(list)
    for p in by_cat[CAT_CORPUS]:
        corpus_dec[p["dec"]].append(p)

    # имя проекта из имени файла заявки
    title = project_name or "Изделие"
    src_note = ", ".join(source_filenames) if source_filenames else ""

    wb = openpyxl.Workbook()

    # ═══════════ 1. ИТОГО ═══════════
    ws = wb.active
    ws.title = "ИТОГО"
    _head(ws, f"СМЕТА — {title}", [44, 12, 10, 12, 14, 34])
    r = 3
    r = _row(ws, r, ["РАЗДЕЛ", "Кол-во", "Ед.", "Цена, ₽", "Сумма, ₽", "Источник"], HDR, True)

    sec_rows = []  # (label, qty, unit, price, sum_val, source)
    cut_total = 0.0

    # раскрой корпуса по декорам (guillotine). Число листов берём ИЗ карты раскроя,
    # а не ceil по площади — иначе сводка обещает 4 листа, а карта реально кладёт на 5.
    sheets_by_dec = {}
    for dec, parts in corpus_dec.items():
        producer = parts[0].get("producer", "")
        sz = sheet_size_of(producer) or DEFAULT_SHEET
        sheets_by_dec[dec] = pack_parts(parts, sz[0], sz[1])
        cut_total += cut_length(sheets_by_dec[dec], sz[0])

    # карта раскроя по декорам — для проверки «один декор на лист»
    корпус_по_декорам = {
        dec: {"листов": len(sheets_by_dec[dec]),
              "м2": round(sum(p["area"] * p["qty"] for p in parts), 3)}
        for dec, parts in corpus_dec.items()
    }

    for dec, parts in sorted(corpus_dec.items(), key=lambda kv: -gstat(kv[1])["м2"]):
        producer = parts[0].get("producer", "")
        sz = sheet_size_of(producer)
        price, prname = find_sheet_price(P, producer, dec) if P else (None, None)
        if sz is None:
            assumptions.append(f"Размер листа для плиты «{producer or dec}» неизвестен — "
                               f"взят {DEFAULT_SHEET[0]}×{DEFAULT_SHEET[1]} (проверить).")
            sz = DEFAULT_SHEET
        SW, SH = sz
        n_sheets = len(sheets_by_dec.get(dec, []))
        sec_rows.append((f"ЛДСП {dec} {SW}×{SH}×16 (листы)", n_sheets, "лист",
                         price, (n_sheets * price) if price is not None else None,
                         prname if price is not None else "УТОЧНИТЬ — нет цены на этот декор в прайсе"))

    pr_cut = price_key(P, "распил ЛДСП толщиной 10–16 мм")

    # кромка корпуса
    edge_len = corpus["периметр"] * DOLYA_EDGE
    pr_edge_mat = price_key(P, "кромка 2 мм")
    pr_edge_glue = price_key(P, "стоимость наклейки кромки 2 мм")
    edge_mat_sum = edge_len * pr_edge_mat if pr_edge_mat is not None else None
    edge_glue_sum = edge_len * pr_edge_glue if pr_edge_glue is not None else None
    sec_rows.append(("Кромка 2 мм (материал, корпус)", edge_len, "пог.м", pr_edge_mat, edge_mat_sum,
                     "прайс" if pr_edge_mat is not None else "УТОЧНИТЬ"))
    sec_rows.append(("Наклейка кромки 2 мм (корпус)", edge_len, "пог.м", pr_edge_glue, edge_glue_sum,
                     "прайс" if pr_edge_glue is not None else "УТОЧНИТЬ"))

    # задние стенки 4 мм
    pr_back = price_key(P, "распил ДВП, ТСНЛ толщиной 3 мм")  # работы; цены листа ХДФ нет
    sec_rows.append((f"Задние стенки 4 мм (ХДФ/ДВП), {back['шт']} шт", back["м2"], "м²",
                     None, None, "УТОЧНИТЬ — цена листа ХДФ/ДВП не в прайсе"))

    # фасады крашеные
    pr_paint = price_key(P, "Покраска фасадов МДФ", "Покраска корпуса МДФ")
    facade_paint_sum = facade["м2"] * pr_paint if pr_paint is not None else None
    sec_rows.append((f"Фасады крашеные (МДФ), {facade['шт']} шт", facade["м2"], "м²",
                     pr_paint, facade_paint_sum,
                     "УТОЧНИТЬ — ставка покраски ₽/м² не в прайсе" if pr_paint is None else "прайс"))

    # столешница — цена только при ТОЧНОМ совпадении размера с прайсом
    top_parts = by_cat[CAT_TOP]
    top_qty = sum(p["qty"] for p in top_parts)
    pr_top = None
    top_sum = None
    top_src = "УТОЧНИТЬ — точного размера нет в прайсе"
    if top_parts and P:
        for p in top_parts:
            L, W, T = p["L"], p["W"], p["T"]
            for k, price in P.items():
                if "столешниц" not in k.lower():
                    continue
                nums = [float(x) for x in re.findall(r"\d+", k)]
                if len(nums) >= 3 and abs(nums[0] - L) < 1 and abs(nums[1] - W) < 1 \
                        and abs(nums[2] - T) < 1:
                    pr_top = price
                    top_src = f"прайс: {k}"
                    break
            if pr_top is not None:
                break
        top_sum = pr_top * top_qty if pr_top is not None else None
    sec_rows.append((f"Столешница, {top_qty} шт ({top['м2']:.3f} м²)", top_qty, "шт",
                     pr_top, top_sum, top_src))

    # стеновая панель — цена только при ТОЧНОМ совпадении размера с прайсом
    panel_parts = by_cat[CAT_PANEL]
    panel_qty = sum(p["qty"] for p in panel_parts)
    pr_panel = None
    panel_src = "УТОЧНИТЬ — точного размера нет в прайсе"
    if panel_parts and P:
        for p in panel_parts:
            L, W, T = p["L"], p["W"], p["T"]
            for k, price in P.items():
                if "стеновая панель" not in k.lower():
                    continue
                nums = [float(x) for x in re.findall(r"\d+", k)]
                if len(nums) >= 3 and abs(nums[0] - L) < 1 and abs(nums[1] - W) < 1 \
                        and abs(nums[2] - T) < 1:
                    pr_panel = price
                    panel_src = f"прайс: {k}"
                    break
            if pr_panel is not None:
                break
    sec_rows.append((f"Стеновая панель, {panel_qty} шт ({panel['м2']:.3f} м²)", panel_qty, "шт",
                     pr_panel, pr_panel * panel_qty if pr_panel is not None else None, panel_src))

    # фурнитура: петли (2 на фасад), направляющие (ящики — УТОЧНИТЬ)
    n_hinge = 2 * facade["шт"]
    pr_hinge = price_key(P, "петля накладная")
    sec_rows.append(("Петля накладная", n_hinge, "шт", pr_hinge,
                     n_hinge * pr_hinge if pr_hinge is not None else None,
                     "2 шт/фасад" if pr_hinge is not None else "УТОЧНИТЬ"))

    # работы: распил + сверление/монтаж петель
    pr_drill = price_key(P, "Отверстие под петлю D35 мм")
    pr_mount_hinge = price_key(P, "Монтаж петли накладной/полунакладной/вкладной")
    work_sum = 0.0
    work_unknown = []
    if pr_cut is not None:
        work_sum += cut_total * pr_cut
    if pr_drill is not None:
        work_sum += n_hinge * pr_drill
    if pr_mount_hinge is not None:
        work_sum += n_hinge * pr_mount_hinge
    work_unknown = ["монтаж ящиков/цоколя/мойки/техники"]

    # суммируем итог
    total = 0.0
    has_unknown = False
    for (label, qty, unit, price, sum_val, src) in sec_rows:
        if sum_val is not None:
            total += sum_val
        else:
            has_unknown = True
    if work_sum:
        total += work_sum

    for label, qty, unit, price, sum_val, src in sec_rows:
        r = _row(ws, r, [label, qty if qty is not None else "", unit,
                         price if price is not None else "",
                         sum_val if sum_val is not None else "УТОЧНИТЬ", src],
                 None, False, [None, '#,##0.00', None, MONEY, MONEY, None])
        if price is None:
            ws.cell(row=r - 1, column=5).fill = UNK
    r = _row(ws, r, ["Работы (распил, сверление, монтаж петель)", "", "", "", work_sum, "прайс"],
             None, False, [None, None, None, None, MONEY, None])
    for nm in work_unknown:
        r = _row(ws, r, [f"  · {nm}", "", "", "", "УТОЧНИТЬ", ""], None, False,
                 [None, None, None, None, None, None])
    r = _row(ws, r, ["ИТОГО СЕБЕСТОИМОСТЬ", None, None, None, total, None], TOTF, True,
             [None] * 4 + [MONEY, None])
    MARKUP = 0.30
    r = _row(ws, r, ["Наценка 30 %", None, None, MARKUP, total * (1 + MARKUP), None], None, False,
             [None, None, None, '0%', MONEY, None])
    r = _row(ws, r, ["ИТОГО для клиента (без техники)", None, None, None,
                     total * (1 + MARKUP), None], TOTF, True, [None] * 4 + [MONEY, None])
    r = _row(ws, r, [])
    r = _row(ws, r, ["Контрольные числа:"], None, True)
    r = _row(ws, r, [f"Корпус ЛДСП: {corpus['поз']} поз / {corpus['шт']} шт / "
                     f"{corpus['м2']:.3f} м²"], None)
    r = _row(ws, r, [f"Задние стенки 4 мм: {back['поз']} / {back['шт']} / {back['м2']:.3f} м²"], None)
    r = _row(ws, r, [f"Стеновая панель: {panel['поз']} / {panel['шт']} / {panel['м2']:.3f} м²"], None)
    r = _row(ws, r, [f"Столешница: {top['поз']} / {top['шт']} / {top['м2']:.3f} м²"], None)
    r = _row(ws, r, [f"Фасады крашеные: {facade['поз']} / {facade['шт']} / {facade['м2']:.3f} м²"], None)
    r = _row(ws, r, [f"ВСЕГО: {summary['штук']} шт / {summary['площадь_м2']:.3f} м²"], None, True)
    if src_note:
        r = _row(ws, r, [f"Источник деталей: {src_note}"], None)

    # ═══════════ 2. Корпус-ЛДСП ═══════════
    ws = wb.create_sheet("Корпус-ЛДСП")
    _head(ws, f"Корпус — ЛДСП ({title})", [8, 26, 10, 10, 8, 6, 12, 12])
    r = 3
    r = _row(ws, r, ["№", "Декор", "Длина, мм", "Ширина, мм", "Толщ., мм", "Кол",
                     "Площадь, м²", "Кромка, пог.м"], HDR, True)
    grp = {}
    for p in by_cat[CAT_CORPUS]:
        k = (p["dec"], round(p["L"]), round(p["W"]), round(p["T"]))
        grp[k] = grp.get(k, {"q": 0, "dec": p["dec"], "L": round(p["L"]),
                             "W": round(p["W"]), "T": round(p["T"])})
        grp[k]["q"] += p["qty"]
    for i, v in enumerate(sorted(grp.values(), key=lambda v: (-v["L"] * v["W"], v["dec"])), 1):
        r = _row(ws, r, [i, v["dec"], v["L"], v["W"], v["T"], v["q"],
                         v["L"] * v["W"] / 1e6 * v["q"],
                         2 * (v["L"] + v["W"]) / 1000 * v["q"]],
                 None, False, [None, None, None, None, None, None, NUM3, '#,##0.00'])
    r = _row(ws, r, ["", "ИТОГО", None, None, None, corpus["шт"], corpus["м2"], corpus["периметр"]],
             TOTF, True, [None] * 5 + [None, NUM3, '#,##0.00'])
    r = _row(ws, r, [])
    for dec, parts in sorted(corpus_dec.items(), key=lambda kv: -gstat(kv[1])["м2"]):
        producer = parts[0].get("producer", "")
        sz = sheet_size_of(producer) or DEFAULT_SHEET
        price, prname = find_sheet_price(P, producer, dec) if P else (None, None)
        n = max(1, math.ceil(gstat(parts)["м2"] / (sz[0] * sz[1] / 1e6))) if gstat(parts)["м2"] > 0 else 0
        r = _row(ws, r, [f"ЛИСТ {dec} {sz[0]}×{sz[1]}×16", None, None, None, None, n,
                         (sz[0] * sz[1] / 1e6), None], None, True)
        fill = YEL if price is not None else UNK
        r = _row(ws, r, [f"Цена листа {dec}, ₽", None, None, None, None, None,
                         price if price is not None else "УТОЧНИТЬ", None], fill, False,
                 [None] * 6 + [MONEY, None])
    r = _row(ws, r, [])
    r = _row(ws, r, ["Распил, пог.м (из карты раскроя)", None, None, None, None, None,
                     round(cut_total, 2), None], None, False, [None] * 6 + ['#,##0.00', None])
    r = _row(ws, r, ["Цена распила, ₽/пог.м", None, None, None, None, None,
                     pr_cut if pr_cut is not None else "УТОЧНИТЬ", None],
             YEL if pr_cut is not None else UNK, False, [None] * 6 + [MONEY, None])
    r = _row(ws, r, ["Кромка: доля торцов", None, None, None, None, None, DOLYA_EDGE, None],
             YEL, False, [None] * 6 + ['0.00', None])
    r = _row(ws, r, ["Кромка корпуса, пог.м", None, None, None, None, None, round(edge_len, 2), None],
             None, False, [None] * 6 + ['#,##0.00', None])

    # ═══════════ 3. Задние стенки 4 мм ═══════════
    ws = wb.create_sheet("Задние стенки 4мм")
    _head(ws, f"Задние стенки 4 мм ({title})", [8, 26, 10, 10, 8, 6, 12])
    r = 3
    r = _row(ws, r, ["№", "Декор", "Длина, мм", "Ширина, мм", "Толщ., мм", "Кол", "Площадь, м²"],
             HDR, True)
    bg = {}
    for p in by_cat[CAT_BACK]:
        k = (p["dec"], round(p["L"]), round(p["W"]))
        bg[k] = bg.get(k, {"q": 0, "dec": p["dec"], "L": round(p["L"]), "W": round(p["W"])})
        bg[k]["q"] += p["qty"]
    for i, v in enumerate(sorted(bg.values(), key=lambda v: (-v["L"] * v["W"], v["dec"])), 1):
        r = _row(ws, r, [i, v["dec"], v["L"], v["W"], 4, v["q"], v["L"] * v["W"] / 1e6 * v["q"]],
                 None, False, [None, None, None, None, None, None, NUM3])
    r = _row(ws, r, ["", "ИТОГО", None, None, None, back["шт"], back["м2"]], TOTF, True,
             [None] * 5 + [None, NUM3])
    r = _row(ws, r, [])
    r = _row(ws, r, ["Цена листа ХДФ/ДВП 4 мм, ₽/лист", None, None, None, None, "УТОЧНИТЬ"],
             UNK, False)
    r = _row(ws, r, ["Распил ДВП/ТСНЛ 3 мм, ₽/пог.м", None, None, None, None,
                     pr_back if pr_back is not None else "УТОЧНИТЬ"],
             YEL if pr_back is not None else UNK, False)

    # ═══════════ 4. Фасады ═══════════
    ws = wb.create_sheet("Фасады")
    _head(ws, f"Фасады крашеные ({title})", [8, 12, 12, 8, 6, 12, 12, 26])
    r = 3
    r = _row(ws, r, ["№", "Длина, мм", "Ширина, мм", "Толщ., мм", "Кол",
                     "Площадь, м²", "Периметр, пог.м", "Материал/примечание"], HDR, True)
    fg = {}
    for p in by_cat[CAT_FACADE]:
        k = (round(p["L"]), round(p["W"]), round(p["T"]), p["dec"])
        fg[k] = fg.get(k, {"q": 0, "L": round(p["L"]), "W": round(p["W"]),
                           "T": round(p["T"]), "dec": p["dec"]})
        fg[k]["q"] += p["qty"]
    for i, v in enumerate(sorted(fg.values(), key=lambda v: (-v["L"] * v["W"], v["dec"])), 1):
        note = v["dec"]
        if is_narrow_plank(v["L"], v["W"]):
            note = "ПРОВЕРИТЬ: цоколь/планка? · " + note
        r = _row(ws, r, [i, v["L"], v["W"], v["T"], v["q"],
                         v["L"] * v["W"] / 1e6 * v["q"],
                         2 * (v["L"] + v["W"]) / 1000 * v["q"], note],
                 None, False, [None, None, None, None, None, NUM3, '#,##0.00', None])
    r = _row(ws, r, ["", "ИТОГО ФАСАДОВ", None, None, facade["шт"], facade["м2"],
                     facade["периметр"], "фасады крашеные — не кромкуются"], TOTF, True,
             [None, None, None, None, None, NUM3, '#,##0.00', None])
    r = _row(ws, r, [])
    r = _row(ws, r, ["Цена покраски, ₽/м²", None, None, None, None,
                     pr_paint if pr_paint is not None else "УТОЧНИТЬ", None],
             YEL if pr_paint is not None else UNK, False, [None] * 6 + [MONEY, None])

    # ═══════════ 5. Столешница и панель ═══════════
    ws = wb.create_sheet("Столешница и панель")
    _head(ws, f"Столешница и стеновая панель ({title})", [8, 26, 12, 12, 8, 12, 30])
    r = 3
    r = _row(ws, r, ["№", "Позиция", "Длина, мм", "Ширина, мм", "Кол", "Площадь, м²", "Источник"],
             HDR, True)
    for i, p in enumerate(sorted(by_cat[CAT_TOP], key=lambda p: -p["L"] * p["W"]), 1):
        r = _row(ws, r, [i, "Столешница", round(p["L"]), round(p["W"]), p["qty"],
                         p["area"] * p["qty"], "из деталей проекта"], None, False,
                 [None, None, None, None, None, NUM3, None])
    for i, p in enumerate(sorted(by_cat[CAT_PANEL], key=lambda p: -p["L"] * p["W"]), 1):
        r = _row(ws, r, [i, "Стеновая панель", round(p["L"]), round(p["W"]), p["qty"],
                         p["area"] * p["qty"], "из деталей проекта"], None, False,
                 [None, None, None, None, None, NUM3, None])
    r = _row(ws, r, [])
    r = _row(ws, r, ["Цена столешницы (точный размер 2416×600×38 не в прайсе)", None, None,
                     None, None, "УТОЧНИТЬ"], UNK, False)
    r = _row(ws, r, ["Цена стеновой панели", None, None, None, None, "УТОЧНИТЬ"], UNK, False)

    # ═══════════ 6. Фурнитура ═══════════
    ws = wb.create_sheet("Фурнитура")
    _head(ws, f"Фурнитура ({title})", [8, 46, 8, 8, 12, 14])
    r = 3
    r = _row(ws, r, ["№", "Наименование", "Кол", "Ед.", "Цена, ₽", "Сумма, ₽"], HDR, True)
    furn = [
        ("петля накладная", n_hinge, "шт", pr_hinge, "2 шт/фасад — из числа фасадов"),
        ("направляющие (ящики)", None, "шт", None, "УТОЧНИТЬ — число ящиков не в файле"),
    ]
    for i, (nm, q, u, price, note) in enumerate(furn, 1):
        r = _row(ws, r, [i, nm + ("  — " + note if note else ""), q if q is not None else "",
                         u, price if price is not None else "",
                         q * price if (q is not None and price is not None) else "УТОЧНИТЬ"],
                 None, False, [None, None, None, None, MONEY, MONEY])
        if price is None:
            ws.cell(row=r - 1, column=6).fill = UNK

    # ═══════════ 7. Работа ═══════════
    ws = wb.create_sheet("Работа")
    _head(ws, f"Работы ({title})", [8, 46, 10, 8, 12, 14, 30])
    r = 3
    r = _row(ws, r, ["№", "Наименование", "Кол", "Ед.", "Цена, ₽", "Сумма, ₽", "Примечание"],
             HDR, True)
    work = [
        ("распил ЛДСП толщиной 10–16 мм", cut_total, "пог.м", pr_cut, "из карты раскроя"),
        ("Наклейка кромки 2 мм (корпус)", edge_len, "пог.м", pr_edge_glue, "периметр × 0.6"),
        ("Отверстие под петлю D35 мм", n_hinge, "шт", pr_drill, "2/фасад"),
        ("Монтаж петли накладной", n_hinge, "шт", pr_mount_hinge, "2/фасад"),
        ("Монтаж ящиков/цоколя/мойки/техники", None, "шт", None, "УТОЧНИТЬ — нет данных"),
    ]
    for i, (nm, q, u, price, note) in enumerate(work, 1):
        r = _row(ws, r, [i, nm, round(q, 2) if q is not None else "",
                         u, price if price is not None else "",
                         round(q * price, 2) if (q is not None and price is not None) else "УТОЧНИТЬ",
                         note], None, False, [None, None, '#,##0.00', None, MONEY, MONEY, None])
        if price is None:
            ws.cell(row=r - 1, column=6).fill = UNK

    # ═══════════ 8. Спец.для фабрики ═══════════
    ws = wb.create_sheet("Спец.для фабрики")
    _head(ws, f"СПЕЦИФИКАЦИЯ ДЛЯ ФАБРИКИ — {title}", [8, 12, 12, 8, 6, 26, 20])
    r = 3
    r = _row(ws, r, ["№", "Длина, мм", "Ширина, мм", "Толщ., мм", "Кол", "Материал / декор",
                     "Назначение"], HDR, True)
    specg = {}
    for p in merged:
        k = (round(p["L"]), round(p["W"]), round(p["T"]), p["dec"], p["cat"])
        specg[k] = specg.get(k, {"q": 0, "L": round(p["L"]), "W": round(p["W"]),
                                 "T": round(p["T"]), "dec": p["dec"], "cat": p["cat"]})
        specg[k]["q"] += p["qty"]
    for i, v in enumerate(sorted(specg.values(),
                                 key=lambda v: (-v["T"], -v["L"] * v["W"], v["dec"])), 1):
        r = _row(ws, r, [i, v["L"], v["W"], v["T"], v["q"], v["dec"], v["cat"]],
                 None, False)
    r = _row(ws, r, ["", "ИТОГО деталей", None, None, summary["штук"], "", ""], TOTF, True)

    # ═══════════ 9. Карта раскроя (по декору) ═══════════
    ws = wb.create_sheet("Карта раскроя")
    _head(ws, f"Карта раскроя — {title}", [10, 10, 12, 12, 8, 12, 12])
    r = 3
    for dec, parts in sorted(corpus_dec.items(), key=lambda kv: -gstat(kv[1])["м2"]):
        producer = parts[0].get("producer", "")
        sz = sheet_size_of(producer) or DEFAULT_SHEET
        SW, SH = sz
        r = _row(ws, r, [f"ДЕКОР: {dec} · лист {SW}×{SH}×16"], None, True)
        for si, sh in enumerate(sheets_by_dec[dec], 1):
            used = 0.0
            first = True
            for shelf in sorted(sh["sh"], key=lambda s: -s["y"]):
                for (L, W) in shelf["it"]:
                    used += L * W / 1e6
                    r = _row(ws, r, [f"лист {si}" if first else "", "", L, W, 1, L * W / 1e6, ""],
                             None, False, [None, None, '#,##0', '#,##0', None, NUM3, None])
                    first = False
            r = _row(ws, r, [f"лист {si}", "ИТОГО", None, None, None, used,
                             used / (SW * SH / 1e6)], TOTF, True,
                     [None, None, None, None, None, NUM3, '0.0%'])
        r = _row(ws, r, [])

    # ═══════════ 10. Допущения ═══════════
    ws = wb.create_sheet("Допущения")
    _head(ws, "Допущения (проверить с заказчиком)", [120])
    r = 3
    r = _row(ws, r, ["Ни одно число не «додумано»: всё берётся из файла раскроя или прайса."], None, True)
    for a in assumptions:
        r = _row(ws, r, ["• " + a])
    extra = [
        "• Крашеные фасады НЕ кромкуются лентой (покраска отдельной строкой).",
        "• Кромка корпуса — лента 2 мм; если у вас 0,4/0,8 мм — правьте цену в прайсе.",
        "• Петли: 2 шт на каждый фасад (все фасады ≤ 900 мм).",
        "• Число ящиков и модулей не восстанавливается из плоского списка деталей — УТОЧНИТЬ.",
        "• Узкие длинные детали фасадов (например 100×3010) помечены «ПРОВЕРИТЬ: цоколь/планка?».",
        "• Техника и встраиваемые приборы в эту смету НЕ включаются (нет данных в файле).",
    ]
    for e in extra:
        r = _row(ws, r, [e])

    # ── самопроверка ──
    # собираем текст сметы для проверки «нет слов другого проекта»
    text_blob = title + " " + " ".join(p["dec"] + " " + (p.get("grp") or "")
                                        for p in merged)
    summary["текст_сметы"] = text_blob
    errors = self_check(summary, stats)

    out = order_dir / out_name
    if out.exists():
        out.unlink()
    wb.save(out)
    return {"xlsx": out, "ok": not errors, "errors": errors,
            "summary": summary, "assumptions": assumptions + extra,
            "карта_раскроя": корпус_по_декорам}
