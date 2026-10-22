# -*- coding: utf-8 -*-
"""
@MebelSmetaBot — смета и визуализация мебели.

Сценарии:
  📐 Смета   — присылаешь раскрой из PRO100 (1 или 2 txt) → готовый xlsx, 9 листов
  🖼 Визуал  — присылаешь фото → выбираешь вариант → фотореалистичная картинка
  📁 Проекты — галерея готовых работ
  ❓ Справка — как это работает

Запуск:  python bot.py
Токен берётся из ~/AppData/Local/hermes/.env (MEBEL_BOT_TOKEN).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, InputMediaPhoto, Message)

HERE = Path(__file__).resolve().parent
ENGINE = HERE / "engine"
ORDERS = HERE / "orders"
PROJECTS = HERE / "projects"
WORKS = HERE / "works"          # галерея наших визуалов для раздела «Наши работы»
for _p in (ORDERS, PROJECTS):
    _p.mkdir(exist_ok=True)
sys.path.insert(0, str(ENGINE))

from parse_cut import parse_cut, save                  # noqa: E402
from make_smeta import build as build_smeta, find_price  # noqa: E402
import vizual as viz                                  # noqa: E402
import gallery as gal                                 # noqa: E402
import skills_meta as sm                              # noqa: E402

log = logging.getLogger("mebelbot")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

STARTED = time.time()
USERS: set[int] = set()


# ─────────────────────────── конфиг ───────────────────────────
def read_env(name: str) -> str:
    """Ищет переменную рядом с bot.py (HERE/.env = C:\\mebelbot\\.env или
    /opt/botsmeta/.env) и в hermes/.env. Так работает и локально, и на сервере."""
    for файл in (HERE / ".env",
                 Path(os.path.expanduser("~")) / "AppData" / "Local" / "hermes" / ".env"):
        if not файл.exists():
            continue
        for line in файл.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s.startswith(name + "="):
                v = s.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    v = os.environ.get(name, "").strip()
    if v:
        return v
    raise SystemExit(f"Нет {name} ни в {HERE / '.env'}, ни в hermes/.env")


# ─────────────────────────── клавиатуры ───────────────────────────
def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📐 Смета", callback_data="m:smeta"),
         InlineKeyboardButton(text="🖼 Визуал", callback_data="m:vizual")],
        [InlineKeyboardButton(text="📁 Проекты", callback_data="m:projects"),
         InlineKeyboardButton(text="✨ Наши работы", callback_data="m:works")],
        [InlineKeyboardButton(text="❓ Как это работает", callback_data="m:help")],
    ])


def kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="m:main")]])


def kb_smeta_ready(n: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🧮 Считать смету ({n} файл.)", callback_data="s:calc")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="m:main")],
    ])


def kb_types() -> InlineKeyboardMarkup:
    """Обязательный выбор типа изделия (архитектура «по типам»)."""
    rows, row = [], []
    for key, label in sm.TYPE_LABELS.items():
        row.append(InlineKeyboardButton(text=label, callback_data=f"t:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="m:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_feedback() -> InlineKeyboardMarkup:
    """Кнопки обратной связи после сметы: «Верно» / «Ошибка»."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Верно", callback_data="f:ok"),
         InlineKeyboardButton(text="❌ Ошибка", callback_data="f:err")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="m:main")],
    ])


def kb_variants() -> InlineKeyboardMarkup:
    rows, row = [], []
    for key, label in viz.NAMES.items():
        row.append(InlineKeyboardButton(text=label, callback_data=f"v:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="m:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─────────────────────────── состояния ───────────────────────────
class Flow(StatesGroup):
    smeta_type = State()  # обязательный выбор типа изделия
    smeta = State()       # ждём файлы раскроя
    feedback = State()    # ждём описание ошибки («строка → как должно быть → почему»)
    vizual = State()      # ждём фото


# ─────────────────────────── тексты ───────────────────────────
HELLO = (
    "👋 <b>Смета мебели</b>\n\n"
    "Считаю сметы по раскрою и показываю, как мебель будет выглядеть в комнате.\n\n"
    "📐 <b>Смета</b> — пришли раскрой из PRO100, получишь готовый файл: материалы, "
    "фурнитура, работы, карта раскроя и спецификация для фабрики.\n\n"
    "🖼 <b>Визуал</b> — пришли фото помещения, выбери вариант, получишь фотореалистичную "
    "картинку для клиента.\n\n"
    "Выбирай 👇\n\n"
    "<i>Визуализация: Powered by Pollinations.ai</i>"
)

HELP = (
    "❓ <b>Как это работает</b>\n\n"
    "<b>📐 Смета</b>\n"
    "1. Жми «Смета»\n"
    "2. Пришли файл(ы) раскроя из PRO100 — <i>txt</i>. Можно один (тот, что с материалом) "
    "или два (детали + сложная)\n"
    "3. Жми «Считать смету» — придёт готовый xlsx\n\n"
    "<b>🖼 Визуал</b>\n"
    "1. Жми «Визуал»\n"
    "2. Пришли фото кухни или помещения\n"
    "3. Выбери вариант — море, закат, другие фасады\n"
    "4. Получи картинку, которую можно показать клиенту\n\n"
    "⚠️ Визуал работает по токенам (1 картинка ≈ 0,03). "
    "Бесплатные токены дают за квесты.\n\n"
    "Вопросы — Арт или Костя."
)


# ─────────────────────────── утилиты ───────────────────────────
def order_dir(user_id: int) -> Path:
    d = ORDERS / f"{user_id}_{time.strftime('%Y%m%d-%H%M%S')}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def версия() -> str:
    """Хеш коммита + время деплоя. Читается из версия.txt (кладётся при деплое)."""
    vfile = HERE / "версия.txt"
    if vfile.exists():
        return vfile.read_text(encoding="utf-8").strip()
    try:
        import subprocess
        h = subprocess.check_output(
            ["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"],
            text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        h = "?"
    mt = time.strftime("%d.%m.%Y %H:%M:%S",
                       time.localtime(Path(__file__).stat().st_mtime))
    return f"commit {h} · собран {mt}"


# ─────────────────────────── команды ───────────────────────────
def build_dp() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())

    @dp.message(Command("start"))
    async def cmd_start(m: Message, state: FSMContext):
        await state.clear()
        USERS.add(m.from_user.id)
        log.info("start от %s (@%s)", m.from_user.id, m.from_user.username)
        await m.answer(HELLO, reply_markup=kb_main())

    @dp.message(Command("help"))
    async def cmd_help(m: Message, state: FSMContext):
        await state.clear()
        await m.answer(HELP, reply_markup=kb_main())

    @dp.message(Command("id"))
    async def cmd_id(m: Message):
        await m.answer(f"твой id: <code>{m.from_user.id}</code>")

    @dp.message(Command("версия"))
    async def cmd_версия(m: Message):
        await m.answer(f"<b>Версия бота</b>\n<code>{версия()}</code>")

    @dp.message(Command("stats"))
    async def cmd_stats(m: Message):
        bal = viz.balance()
        await m.answer(
            f"<b>Состояние</b>\n"
            f"работаю: {int(time.time() - STARTED) // 60} мин\n"
            f"пользователей за сессию: {len(USERS)}\n"
            f"баланс токенов: {bal if bal is not None else '—'}\n"
            f"генератор сметы: {'✅' if (ENGINE / 'estimate.py').exists() else '❌'}\n"
            f"прайс: <code>{find_price().name}</code>")

    # ─────────── меню ───────────
    @dp.callback_query(F.data == "m:main")
    async def cb_main(c: CallbackQuery, state: FSMContext):
        await state.clear()
        await c.message.answer(HELLO, reply_markup=kb_main())
        await c.answer()

    @dp.callback_query(F.data == "m:help")
    async def cb_help(c: CallbackQuery, state: FSMContext):
        await state.clear()
        await c.message.answer(HELP, reply_markup=kb_main())
        await c.answer()

    @dp.callback_query(F.data == "m:projects")
    async def cb_projects(c: CallbackQuery, state: FSMContext):
        """Раздел «Проекты»: только меню категорий. Фото — по выбору, не сами."""
        await state.clear()
        st = gal.stats()
        if st["показываем"] == 0:
            await c.message.answer(
                "📁 <b>Проекты</b>\n\nВитрина пока пустая — Костя готовит фотографии "
                "готовых работ.",
                reply_markup=kb_main())
            await c.answer()
            return

        cats = gal.categories()
        rows, row = [], []
        if cats:
            for cat in gal.ordered_cats(cats):
                row.append(InlineKeyboardButton(
                    text=gal.label(cat), callback_data=f"c:{cat}"))
                if len(row) == 2:
                    rows.append(row)
                    row = []
            if row:
                rows.append(row)

        # «Смотреть все» — всегда доступно
        rows.append([InlineKeyboardButton(
            text=f"🔎 Смотреть все ({st['показываем']})", callback_data="p:0:")])
        rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="m:main")])

        if cats:
            head = (f"📁 <b>Проекты</b>\n\n<b>{st['показываем']}</b> работ "
                    f"в {st['категорий']} разделах.\n\n<b>Выбери, что смотреть:</b>")
        else:
            head = (f"📁 <b>Проекты</b>\n\nВсего работ: <b>{st['показываем']}</b>"
                    f"\n\n<i>Фото пока лежат одной кучей — жми «Смотреть все».</i>")

        await c.message.answer(head,
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await c.answer()

    # ─────────── СЕТКА: страницы внутри категории ───────────
    @dp.callback_query(F.data.startswith("p:"))
    async def cb_page(c: CallbackQuery, state: FSMContext):
        _, i, cat = c.data.split(":", 2)
        photos = gal.photos_of(cat)
        n = gal.pages_of(cat)
        if n == 0:
            await c.answer("Здесь пока пусто", show_alert=True)
            return
        i = max(0, min(int(i), n - 1))
        await c.message.delete()
        await c.answer()
        await send_grid(c.message, i, cat)

    @dp.callback_query(F.data.startswith("c:"))
    async def cb_cat(c: CallbackQuery, state: FSMContext):
        cat = c.data.split(":", 1)[1]
        n = gal.pages_of(cat)
        if n == 0:
            await c.answer("В этой категории пока пусто", show_alert=True)
            return
        cnt = len(gal.photos_of(cat))
        await c.message.answer(f"{gal.label(cat)} — <b>{cnt}</b> работ")
        await c.answer()
        await send_grid(c.message, 0, cat)

    async def send_grid(msg: Message, i: int, cat: str = ""):
        """сетка страницы №i внутри категории (cat='' = все)"""
        ph = gal.page_of(cat, i)
        n = gal.pages_of(cat)
        total = len(gal.photos_of(cat))
        if not ph:
            await msg.answer("Пусто", reply_markup=kb_main())
            return
        first = i * gal.PAGE + 1
        last = first + len(ph) - 1
        title = gal.label(cat) if cat else "Все работы"
        await msg.answer(f"{title} · <b>{first}–{last}</b> из {total} · стр. {i + 1}/{n}")

        media = []
        for k, p in enumerate(ph):
            item = {"type": "photo", "media": FSInputFile(gal.thumb(p))}
            if k == 0:
                item["caption"] = f"{first + k}. {gal.caption_of(p)[:70]}"
            media.append(item)
        try:
            await msg.answer_media_group(media)
        except Exception:
            log.exception("сетка не ушла, шлю по одному")
            for p in ph:
                try:
                    await msg.answer_photo(FSInputFile(gal.thumb(p)),
                                           caption=gal.caption_of(p)[:90])
                except Exception:
                    log.exception("фото %s не ушло", p.name)

        rows, nav = [], []
        if i > 0:
            nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"p:{i - 1}:{cat}"))
        if i < n - 1:
            nav.append(InlineKeyboardButton(text="➡️ Дальше", callback_data=f"p:{i + 1}:{cat}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton(text="🔍 Открыть по одному",
                                          callback_data=f"g:{i * gal.PAGE}:{cat}")])
        if cat:
            rows.append([InlineKeyboardButton(text="📋 Другие разделы", callback_data="m:projects")])
        else:
            rows.append([InlineKeyboardButton(text="📋 Список страниц", callback_data=f"plist:{cat}")])
        rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="m:main")])
        await msg.answer(f"страница {i + 1} из {n}",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @dp.callback_query(F.data.startswith("plist"))
    async def cb_plist(c: CallbackQuery, state: FSMContext):
        cat = c.data.split(":", 1)[1] if ":" in c.data else ""
        n = gal.pages_of(cat)
        total = len(gal.photos_of(cat))
        rows, row = [], []
        for i in range(n):
            a, b = i * gal.PAGE + 1, min((i + 1) * gal.PAGE, total)
            row.append(InlineKeyboardButton(text=f"{a}–{b}", callback_data=f"p:{i}:{cat}"))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="m:main")])
        await c.message.edit_text(
            f"📋 <b>Все страницы</b> — {total} работ, {n} страниц по {gal.PAGE}.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await c.answer()

    # ─────────── КРУПНЫЙ ПРОСМОТР: листание ───────────
    @dp.callback_query(F.data.startswith("g:"))
    async def cb_gallery(c: CallbackQuery, state: FSMContext):
        _, i, cat = (c.data.split(":", 2) + [""])[:3] if c.data.count(":") >= 2 \
            else (c.data.split(":") + [""])[:3]
        photos = gal.photos_of(cat)
        if not photos:
            await c.answer("Фотографий больше нет", show_alert=True)
            return
        i = max(0, min(int(i), len(photos) - 1))
        await c.message.delete()
        await c.answer()
        await send_gallery(c.message, i, cat)

    async def send_gallery(msg: Message, i: int, cat: str = ""):
        photos = gal.photos_of(cat)
        n = len(photos)
        p = photos[i]
        text = f"📷 <b>{gal.caption_of(p)[:80]}</b>\n\n{i + 1} из {n}"
        rows, nav = [], []
        if i > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"g:{i - 1}:{cat}"))
        if i < n - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"g:{i + 1}:{cat}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton(text="🔲 К сетке", callback_data=f"p:{i // gal.PAGE}:{cat}")])
        if cat:
            rows.append([InlineKeyboardButton(text="📋 Другие разделы", callback_data="m:projects")])
        rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="m:main")])
        try:
            await msg.answer_photo(FSInputFile(gal.thumb(p)), caption=text,
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        except Exception:
            log.exception("фото %s не ушло", p.name)
            await msg.answer(f"⚠️ «{p.name}» не отправилось.",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                 [InlineKeyboardButton(text="➡️ Дальше", callback_data=f"g:{i + 1}:{cat}")],
                                 [InlineKeyboardButton(text="⬅️ В меню", callback_data="m:main")]]))

    # ─────────── СМЕТА ───────────
    @dp.callback_query(F.data == "m:smeta")
    async def cb_smeta(c: CallbackQuery, state: FSMContext):
        d = order_dir(c.from_user.id)
        await state.set_state(Flow.smeta_type)
        await state.update_data(dir=str(d), files=[], izdelie=None)
        await c.message.answer(
            "📐 <b>Смета</b>\n\nСначала выбери <b>тип изделия</b> 👇\n"
            "Расчёт идёт по правилам именно этого типа — кухня и шкаф не смешиваются.",
            reply_markup=kb_types())
        await c.answer()

    @dp.callback_query(F.data.startswith("t:"))
    async def cb_type(c: CallbackQuery, state: FSMContext):
        key = c.data.split(":", 1)[1]
        if key not in sm.TYPES:
            await c.answer("Такого типа нет", show_alert=True)
            return
        # Читаем скилл выбранного типа (ТОЛЬКО его). Файл всегда есть (шаблон),
        # но не роняем расчёт на чтении — правила уточняются у Кости.
        try:
            sm.load_skill(HERE, key)
        except FileNotFoundError:
            log.warning("нет скилла для типа %s", key)
        await state.update_data(izdelie=key)
        await state.set_state(Flow.smeta)
        await c.message.answer(
            f"📐 <b>{sm.TYPE_LABELS[key]}</b>\n\n"
            f"Пришли файл(ы) раскроя из PRO100 — <b>txt</b>.\n\n"
            "• один файл — тот, где есть <i>материал</i> (обычно «…детали.txt»)\n"
            "• или два — «детали» + «сложная»: тогда размеры возьму точные, "
            "а материал и позиции — из обоих\n\n"
            "Можно отправить как <b>файл</b>, не как фото.",
            reply_markup=kb_cancel())
        await c.answer()

    @dp.message(F.document, Flow.smeta)
    async def on_cut_file(m: Message, state: FSMContext, bot: Bot):
        data = await state.get_data()
        d = Path(data.get("dir"))
        files = list(data.get("files", []))
        name = m.document.file_name or f"файл_{len(files) + 1}.txt"
        if not name.lower().endswith((".txt", ".tsv", ".csv")):
            await m.answer(
                "Нужен <b>txt</b> из PRO100 («Список деталей»), а не этот файл.\n\n"
                "В PRO100: <i>Файл → Экспорт → Список деталей</i> — сохрани как txt.\n"
                "В файле должны быть колонки: длина / ширина / толщина / количество / "
                "материал (с декором, например «U-171 серый»).")
            return
        dest = d / f"{len(files) + 1}_{name}"
        await bot.download(m.document, destination=dest)
        files.append(str(dest))
        await state.update_data(files=files)
        try:
            probe = parse_cut(files)
            s = probe["stats"]
            await m.answer(
                f"✅ Принял: <b>{name}</b>\n\n"
                f"Сейчас вижу: <b>{s['позиций']}</b> позиций, <b>{s['штук']}</b> деталей, "
                f"{s['площадь_м2']} м², без материала — {s['без_материала']}.\n\n"
                f"Пришли второй файл (если есть) или жми «Считать смету».",
                reply_markup=kb_smeta_ready(len(files)))
        except Exception as e:
            log.exception("не разобрал раскрой")
            await m.answer(
                f"⚠️ Файл принял, но разобрать не смог:\n<code>{str(e)[:200]}</code>\n\n"
                f"Пришли другой файл или напиши Косте.",
                reply_markup=kb_smeta_ready(len(files)))

    @dp.callback_query(F.data == "s:calc")
    async def cb_calc(c: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        files = data.get("files") or []
        d = Path(data.get("dir"))
        if not files:
            await c.answer("Сначала пришли файл раскроя", show_alert=True)
            return
        await c.answer()
        msg = await c.message.answer("🧮 Считаю смету… это несколько секунд")

        # имя проекта — из имени файла заявки (не зашито в код)
        def проект_из_имени(path: str) -> str:
            n = Path(path).name
            n = re.sub(r"\.(txt|tsv|csv)$", "", n, flags=re.I)
            n = re.sub(r"\s*[-–—]\s*детали.*$", "", n, flags=re.I)
            n = re.sub(r"^\d+_", "", n)          # убрать служебный префикс «1_», «2_»
            return n.strip() or "Изделие"

        project_name = проект_из_имени(files[0])
        src_names = [Path(f).name for f in files]

        # расчёт — детерминированный код; при сбое 3 попытки с паузой, иначе «повтори позже»
        res = None
        last_err = None
        for попытка in range(1, 4):
            try:
                det = await asyncio.to_thread(parse_cut, files)
                det_path = save(det, d / "детали.json")
                res = await asyncio.to_thread(
                    build_smeta, d, det_path, f"СМЕТА_{project_name}.xlsx",
                    project_name, src_names)
                break
            except Exception as e:
                last_err = e
                log.exception("попытка %d сметы не удалась", попытка)
                await asyncio.sleep(1.5)

        if res is None:
            await msg.edit_text(
                f"❌ Не смог посчитать после 3 попыток — повтори позже.\n"
                f"<code>{str(last_err)[:200]}</code>")
            await state.clear()
            return

        # самопроверка: файл не уходит, пока не сошлись все проверки
        if not res.get("ok"):
            errs = "\n".join("• " + e for e in res.get("errors", []))
            await msg.edit_text(
                f"⚠️ Смета собрана, но самопроверка не сошлась — файл НЕ отправляю:\n{errs}")
            await state.clear()
            return

        s = det["stats"]
        izdelie = data.get("izdelie")
        label = sm.TYPE_LABELS.get(izdelie, "") if izdelie else ""
        await state.update_data(detail_json=str(det_path), xlsx_path=str(res["xlsx"]))
        head = "✅ <b>Смета готова</b>" + (f" — {label}" if label else "") + "\n\n"
        await msg.edit_text(
            head +
            f"позиций: {s['позиций']} · деталей: {s['штук']} · {s['площадь_м2']} м²\n"
            f"без материала: {s['без_материала']}\n\n"
            f"Файл ниже — листы: ИТОГО, Корпус-ЛДСП, Задние стенки 4мм, Фасады, "
            f"Столешница и панель, Фурнитура, Работа, Спец.для фабрики, Карта раскроя, "
            f"Допущения.")
        await c.message.answer_document(
            FSInputFile(res["xlsx"]), caption=res["xlsx"].name)
        log.info("смета готова для %s: %s", c.from_user.id, res["xlsx"])
        await c.message.answer(
            "Смета верная? Нажми <b>«Верно»</b> — сохраню выгрузку и смету как эталон "
            "этого типа. Если ошибка — <b>«Ошибка»</b>, и я спрошу, что именно не так.",
            reply_markup=kb_feedback())
        await state.set_state(Flow.feedback)

    # ─────────── ОБРАТНАЯ СВЯЗЬ ПО СМЕТЕ ───────────
    @dp.callback_query(F.data == "f:ok", Flow.feedback)
    async def cb_feedback_ok(c: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        key = data.get("izdelie")
        await c.answer()
        if not key or key not in sm.TYPES:
            await c.message.answer("Не помню тип изделия — начни заново.",
                                   reply_markup=kb_main())
            await state.clear()
            return
        try:
            dest = sm.save_etalon(HERE, key, data.get("dir"),
                                  data.get("detail_json"), data.get("xlsx_path"))
            await c.message.answer(
                f"✅ Сохранил как эталон <b>{sm.TYPE_LABELS[key]}</b>.\n"
                f"Папка: <code>{dest}</code>",
                reply_markup=kb_main())
        except Exception as e:
            log.exception("не сохранил эталон")
            await c.message.answer(f"⚠️ Не сохранил: <code>{str(e)[:200]}</code>",
                                   reply_markup=kb_main())
        await state.clear()

    @dp.callback_query(F.data == "f:err", Flow.feedback)
    async def cb_feedback_err(c: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        key = data.get("izdelie")
        await c.answer()
        if not key or key not in sm.TYPES:
            await c.message.answer("Не помню тип изделия — начни заново.",
                                   reply_markup=kb_main())
            await state.clear()
            return
        await c.message.answer(
            f"❌ Понял, ошибка в смете <b>{sm.TYPE_LABELS[key]}</b>.\n\n"
            f"Напиши в формате:\n"
            f"<code>строка → как должно быть → почему</code>\n\n"
            f"Запишу в Журнал поправок <b>этого</b> типа и пересчитаю.",
            reply_markup=kb_cancel())
        await state.set_state(Flow.feedback)

    @dp.message(F.text, Flow.feedback)
    async def on_feedback_text(m: Message, state: FSMContext):
        data = await state.get_data()
        key = data.get("izdelie")
        if not key or key not in sm.TYPES:
            await m.answer("Не помню тип изделия — начни заново.", reply_markup=kb_main())
            await state.clear()
            return
        text = m.text.strip()
        try:
            p = sm.append_fix(HERE, key, text)
            await m.answer(
                f"📝 Записал в Журнал поправок <b>{sm.TYPE_LABELS[key]}</b> "
                f"(файл <code>{p.name}</code>).\n\n"
                f"Поправка: <i>{text[:300]}</i>\n\n"
                f"Пересчитаю смету по обновлённым правилам, когда будут правки кода "
                f"или новые вводные. Пока правило зафиксировано.",
                reply_markup=kb_main())
        except Exception as e:
            log.exception("не записал поправку")
            await m.answer(f"⚠️ Не записал: <code>{str(e)[:200]}</code>",
                           reply_markup=kb_main())
        await state.clear()

    # ─────────── ВИЗУАЛ ───────────
    @dp.callback_query(F.data == "m:vizual")
    async def cb_vizual(c: CallbackQuery, state: FSMContext):
        d = order_dir(c.from_user.id)
        await state.set_state(Flow.vizual)
        await state.update_data(dir=str(d))
        bal = viz.balance()
        tail = ""
        if bal is not None:
            tail = f"\n\nБаланс: <b>{bal:.2f}</b> токенов (одна картинка ≈ 0,04)"
            if bal < viz.PRICE:
                tail += "\n⚠️ Токенов мало — картинка не выйдет. " \
                        "Бесплатные дают за квесты: enter.pollinations.ai/quests"
        await c.message.answer(
            "🖼 <b>Визуал</b>\n\nПришли <b>фото</b> кухни или помещения — обычный снимок "
            "с телефона." + tail, reply_markup=kb_cancel())
        await c.answer()

    @dp.message(F.photo, Flow.vizual)
    async def on_photo(m: Message, state: FSMContext, bot: Bot):
        data = await state.get_data()
        d = Path(data.get("dir"))
        f = await bot.get_file(m.photo[-1].file_id)
        dest = d / "фото.jpg"
        await bot.download_file(f.file_path, destination=dest)
        await state.update_data(photo=str(dest))
        await m.answer("📷 Фото принял. Выбери вариант 👇", reply_markup=kb_variants())

    @dp.callback_query(F.data.startswith("v:"))
    async def cb_variant(c: CallbackQuery, state: FSMContext):
        variant = c.data.split(":", 1)[1]
        data = await state.get_data()
        photo = data.get("photo")
        d = data.get("dir")
        if not photo:
            await c.answer("Сначала пришли фото", show_alert=True)
            return
        await c.answer()
        label = viz.NAMES.get(variant, variant)
        msg = await c.message.answer(f"🎨 Делаю «{label}»… около минуты")
        try:
            out = Path(d) / f"визуал_{variant}.jpg"
            res = await asyncio.to_thread(viz.generate, Path(photo), variant, out)
            await msg.edit_text(f"✅ Готово: <b>{label}</b>")
            await c.message.answer_photo(FSInputFile(res))
            await c.message.answer("Ещё вариант?", reply_markup=kb_variants())
            log.info("визуал %s для %s", variant, c.from_user.id)
        except Exception as e:
            log.exception("ошибка визуала")
            await msg.edit_text(f"❌ Не получилось:\n<code>{str(e)[:300]}</code>")

    # ─────────── НАШИ РАБОТЫ (слайдер) ───────────
    def works_list() -> list[Path]:
        if not WORKS.exists():
            return []
        return [p for p in sorted(WORKS.glob("*"))
                if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]

    def kb_works(i: int, n: int) -> InlineKeyboardMarkup:
        """Кнопки листалки: ◀️ Далее ▶️ + в меню. Первая/последняя не крутятся."""
        nav = []
        if i > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"w:{i-1}"))
        nav.append(InlineKeyboardButton(text=f"{i+1} / {n}", callback_data="w:noop"))
        if i < n - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"w:{i+1}"))
        return InlineKeyboardMarkup(inline_keyboard=[
            nav,
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="m:main")],
        ])

    @dp.callback_query(F.data == "m:works")
    async def cb_works(c: CallbackQuery):
        await c.answer()
        pics = works_list()
        if not pics:
            await c.message.answer("Пока пусто — работы появятся позже.",
                                   reply_markup=kb_cancel())
            return
        p = pics[0]
        await c.message.answer_photo(
            FSInputFile(p),
            caption=f"✨ <b>Наши работы</b>  ·  1 / {len(pics)}\n\n"
                    "Из 3D-рендера — фотореалистичная картинка.",
            reply_markup=kb_works(0, len(pics)))

    @dp.callback_query(F.data.startswith("w:"))
    async def cb_works_nav(c: CallbackQuery):
        arg = c.data.split(":", 1)[1]
        if arg == "noop":
            await c.answer()
            return
        pics = works_list()
        if not pics:
            await c.answer("Работы пропали", show_alert=True)
            return
        i = max(0, min(int(arg), len(pics) - 1))
        await c.answer()
        media = FSInputFile(pics[i])
        cap = f"✨ <b>Наши работы</b>  ·  {i+1} / {len(pics)}"
        try:
            await c.message.edit_media(
                InputMediaPhoto(media=media, caption=cap),
                reply_markup=kb_works(i, len(pics)))
        except Exception:
            # edit_media не умеет менять тип — шлём новым сообщением
            log.exception("слайдер работ: не смог отредактировать, шлю заново")
            await c.message.answer_photo(media, caption=cap,
                                         reply_markup=kb_works(i, len(pics)))

    # ─────────── прочее ───────────
    @dp.message()
    async def fallback(m: Message, state: FSMContext):
        st = await state.get_state()
        if st == Flow.smeta_type.state:
            await m.answer("Сначала выбери <b>тип изделия</b> кнопкой ниже. "
                           "Или жми «В меню».", reply_markup=kb_types())
        elif st == Flow.smeta.state:
            await m.answer("Жду <b>файл</b> раскроя (txt). Или жми «В меню».")
        elif st == Flow.feedback.state:
            await m.answer("Нажми <b>«Верно»</b> или <b>«Ошибка»</b>, либо напиши "
                           "описание ошибки в формате «строка → как должно быть → почему».")
        elif st == Flow.vizual.state:
            await m.answer("Жду <b>фото</b> кухни или помещения. Или жми «В меню».")
        else:
            await m.answer("Выбери, что нужно 👇", reply_markup=kb_main())

    return dp


async def main():
    token = read_env("MEBEL_BOT_TOKEN")
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    me = await bot.get_me()
    log.info("запускаюсь как @%s (%s)", me.username, me.first_name)

    dp = build_dp()
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        log.warning("вебхук не удалён: %s", type(e).__name__)

    # Простой поллинг: aiogram сам повторяет запросы при обрыве.
    # Никаких доп. параметров — они вызывали самозавершение бота.
    log.info("поллинг запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("остановлен")
