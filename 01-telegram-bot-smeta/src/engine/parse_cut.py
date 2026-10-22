# -*- coding: utf-8 -*-
"""
Разбор раскроя из PRO100 → список деталей для сметы.

Костя выгружает из PRO100 два вида файлов:

  ТИП A — «детали»: есть МАТЕРИАЛ, размеры округлены
      f[1], f[3], f[5] = три габарита · f[6] = кол-во · f[7] = материал
      пример:  \t788\t   \t594\t   \t18\t1\tДСП\\ДСП\\Kronospan\\16 mm\\Унидорки\\K_ 101

  ТИП B — «сложная»: размеры ТОЧНЫЕ, есть номера позиций
      f[2]=№ · f[3]=группа · f[4]=кол-во · f[5],f[6],f[7] = X,Y,Z
      пример:  \t\t1\t.\t1\terror_18,0\t788,0\t594,0\tA

Правило (Костя): панель = три габарита; L,W — два больших, T — наименьший.
Сопоставление двух файлов — по ТРОЙКЕ габаритов (не по паре!).

Если пришёл один файл — работаем по нему: тип A даёт и размеры, и материал.
"""
from __future__ import annotations

import json
from pathlib import Path

from estimate import classify


def num(s):
    """'error_18,0' → 18.0 | '  594' → 594.0 | '' → None"""
    if s is None:
        return None
    s = str(s).strip().replace("error_", "").replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def dims3(a, b, c):
    """Три габарита → (L, W, T): два больших и наименьший."""
    v = sorted([a, b, c], reverse=True)
    return v[0], v[1], v[2]


def skey(L, W, T):
    """Ключ сопоставления — округлённая тройка (толщина с допуском ±1)."""
    return (round(L), round(W), round(T / 2) * 2)


def read_rows(path: Path):
    txt = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    return [ln.split("\t") for ln in txt.splitlines() if ln.strip()]


def parse_type_a(rows):
    """Файл с материалом."""
    out = []
    for f in rows:
        if len(f) < 8:
            continue
        A, B, C = num(f[1]), num(f[3]), num(f[5])
        qty = num(f[6])
        mat = f[7].strip()
        if None in (A, B, C, qty) or not mat or num(mat) is not None:
            continue
        L, W, T = dims3(A, B, C)
        out.append({"group": f[0].strip(), "qty": int(qty),
                    "L": L, "W": W, "T": T, "mat": mat})
    return out


def parse_type_b(rows):
    """Файл с точными размерами и номерами позиций."""
    out = []
    for f in rows:
        if len(f) < 8:
            continue
        n, qty = num(f[2]), num(f[4])
        X, Y, Z = num(f[5]), num(f[6]), num(f[7])
        if None in (n, qty, X, Y, Z):
            continue
        L, W, T = dims3(X, Y, Z)
        out.append({"n": int(n), "group": f[3].strip(), "qty": int(qty),
                    "L": L, "W": W, "T": T})
    return out


def parse_bare(rows):
    """
    Детали БЕЗ материала: PRO100 отдал только размеры и количество.
    Пример: \t150\t   \t16\t   \t200\t4     (6 полей, 7-го — материала — нет)

    Возвращает позиции с пустым материалом, чтобы разбор не падал.
    """
    out = []
    for f in rows:
        if len(f) < 6:
            continue
        A, B, C = num(f[1]), num(f[3]), num(f[5])
        qty = num(f[6]) if len(f) > 6 else None
        if None in (A, B, C, qty) or qty <= 0:
            continue
        L, W, T = dims3(A, B, C)
        out.append({"group": (f[0] or "").strip(), "qty": int(qty),
                    "L": L, "W": W, "T": T, "mat": ""})
    return out


def merge(a_parts, b_parts):
    """Сопоставляем по тройке габаритов: материал из A, точные размеры из B."""
    used = [False] * len(a_parts)
    merged, exact, fuzzy = [], 0, 0
    for pb in b_parts:
        k = skey(pb["L"], pb["W"], pb["T"])
        hit = None
        for i, pa in enumerate(a_parts):
            if not used[i] and skey(pa["L"], pa["W"], pa["T"]) == k:
                hit = i
                exact += 1
                break
        if hit is None:
            for i, pa in enumerate(a_parts):
                if used[i]:
                    continue
                if (abs(pa["L"] - pb["L"]) <= 1.2 and abs(pa["W"] - pb["W"]) <= 1.2
                        and abs(pa["T"] - pb["T"]) <= 1.2):
                    hit = i
                    fuzzy += 1
                    break
        mat = ""
        if hit is not None:
            used[hit] = True
            mat = a_parts[hit]["mat"]
        merged.append({"n": pb.get("n", len(merged) + 1), "grp": pb["group"],
                       "qty": pb["qty"], "L": pb["L"], "W": pb["W"], "T": pb["T"],
                       "mat": mat})
    left = [a_parts[i] for i in range(len(a_parts)) if not used[i]]
    return merged, left, exact, fuzzy


def parse_cut(paths) -> dict:
    """
    Главная функция. Принимает 1 или 2 файла раскроя.
    Возвращает {"merged": [...], "leftover": [...], "stats": {...}}

    Файл без материала (столбец материала не выгружен) не роняет разбор:
    детали читаются, материал помечается пустым — смета укажет «уточнить».
    """
    files_a, files_b = [], []
    bad = []
    for p in paths:
        p = Path(p)
        rows = read_rows(p)
        a = parse_type_a(rows)
        b = parse_type_b(rows)
        if len(a) >= len(b) and a:
            files_a.append((p.name, a))
        elif b:
            files_b.append((p.name, b))
        else:
            # ни материал, ни номера — пробуем как «детали без материала»
            bare = parse_bare(rows)
            if bare:
                files_a.append((p.name, bare))
            else:
                bad.append(p.name)

    if not files_a and not files_b:
        raise ValueError(
            "Файл не похож на раскрой PRO100: " + ", ".join(bad) + ".\n"
            "Пришли txt «Список деталей» из PRO100 (Файл → Экспорт → Список деталей), "
            "с колонками: длина / ширина / толщина / количество / материал (декор)."
        )

    a_parts = files_a[0][1] if files_a else []
    b_parts = files_b[0][1] if files_b else []

    if a_parts and b_parts:
        merged, left, exact, fuzzy = merge(a_parts, b_parts)
    elif a_parts:
        merged = [{"n": i + 1, "grp": x.get("group", ""), "qty": x["qty"], "L": x["L"],
                   "W": x["W"], "T": x["T"], "mat": x.get("mat", "")}
                  for i, x in enumerate(a_parts)]
        left, exact, fuzzy = [], 0, 0
    else:
        merged = [{"n": x["n"], "grp": x["group"], "qty": x["qty"], "L": x["L"],
                   "W": x["W"], "T": x["T"], "mat": ""} for x in b_parts]
        left, exact, fuzzy = [], 0, 0

    for p in merged:
        p["area"] = p["L"] * p["W"] / 1e6
        p["per"] = 2 * (p["L"] + p["W"]) / 1000
        p["cat"] = classify(p.get("grp", ""), p.get("mat", ""), p.get("T"))

    stats = {
        "файлов": len(paths),
        "позиций": len(merged),
        "штук": sum(p["qty"] for p in merged),
        "площадь_м2": round(sum(p["area"] * p["qty"] for p in merged), 3),
        "совпало_точно": exact,
        "совпало_с_допуском": fuzzy,
        "без_материала": sum(1 for p in merged if not p["mat"]),
        "лишних_в_A": len(left),
        "файлов_не_понял": bad,
    }
    return {"merged": merged, "leftover": left, "stats": stats}


def save(detail: dict, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(detail, ensure_ascii=False, indent=1), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    import sys
    ps = sys.argv[1:]
    if not ps:
        print("укажи файл(ы) раскроя")
        raise SystemExit(2)
    d = parse_cut(ps)
    for k, v in d["stats"].items():
        print(f"  {k:22} {v}")
    print()
    for p in d["merged"][:8]:
        nm = (p["mat"] or p["grp"]).split("\\")[-1]
        print(f"  №{p['n']:3d} {p['L']:7.1f}×{p['W']:6.1f}×{p['T']:2.0f} ×{p['qty']:<3d} "
              f"[{p['cat']:12s}] {nm[:45]}")
