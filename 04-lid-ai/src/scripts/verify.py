"""Единая проверка проекта: линт-минимум, тесты, схема, отчёт.

    python scripts/verify.py            # полный прогон (включая живые тесты на LLM)
    python scripts/verify.py --fast     # без живых тестов (0 ₽)

Код возврата 0 — можно сдавать.
"""

from __future__ import annotations

import argparse
import compileall
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PY = sys.executable


def step(name: str, cmd: list[str]) -> bool:
    print(f"\n=== {name} ===")
    r = subprocess.run(cmd, cwd=BASE)
    print(f"→ {name}: {'OK' if r.returncode == 0 else f'ПРОВАЛ (код {r.returncode})'}")
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="без живых тестов на LLM")
    args = ap.parse_args()

    print("компиляция всех модулей…")
    ok_compile = compileall.compile_dir(str(BASE / "app"), quiet=1) and \
        compileall.compile_dir(str(BASE / "scripts"), quiet=1) and \
        compileall.compile_dir(str(BASE / "tests"), quiet=1)
    print(f"→ синтаксис: {'OK' if ok_compile else 'ПРОВАЛ'}")

    tests = [PY, "-m", "pytest", "-q"]
    if args.fast:
        tests += ["-m", "not live"]

    results = {
        "синтаксис": ok_compile,
        "тесты": step("pytest", tests),
        "схема/карточка/отчёт": step("verify_stage1", [PY, "scripts/verify_stage1.py"]),
    }
    print("\n" + "=" * 60)
    for name, ok in results.items():
        print(f"{'OK  ' if ok else 'ПРОВАЛ'} {name}")
    bad = [n for n, ok in results.items() if not ok]
    print("=" * 60)
    print("ИТОГ: всё прошло" if not bad else f"ПРОВАЛЫ: {', '.join(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
