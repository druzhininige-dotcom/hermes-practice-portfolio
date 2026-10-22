# -*- coding: utf-8 -*-
"""
ВЕБ-КАБИНЕТ МАСТЕРА — полная браузерная версия бота (для Кости).

Зачем: работать в браузере, а не в Telegram. Браузер не блокируют провайдеры,
не нужен аккаунт Telegram, работает с телефона и ПК, есть история проектов.

РАЗДЕЛЫ (как в боте @MebelSmetaBot):
  📐 Смета        — txt из PRO100 → Excel (9 листов)
  🖼 Визуал       — рендер → фотореалистичная картинка (6 вариантов)
  📁 Проекты      — галерея готовых работ по категориям
  ✨ Наши работы  — визуалы, которые мы сделали
  ❓ Как это работает — справка

Данные берутся из тех же папок, что у бота (12_Бот_сметы):
  projects/ — фото работ    works/ — наши визуалы
Поэтому не надо ничего дублировать: Костя видит одно и то же.

Работает и автономно (копия данных на сервере в кабинете).

Запуск: python app.py   (порт 8090)
"""
from __future__ import annotations

import html
import json
import os
import pathlib
import re
import secrets
import sys
import time
import http.cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

# ── пути ───────────────────────────────────────────────────────────────
HERE = pathlib.Path(__file__).resolve().parent
ENGINE = HERE / "engine"
if not ENGINE.exists():
    ENGINE = pathlib.Path("/opt/webkabinet/engine")
sys.path.insert(0, str(ENGINE))

# папки с данными: свои, если есть; иначе — от бота
_DEFAULT_PROJ = pathlib.Path("/opt/webkabinet/projects")
_БОТ = pathlib.Path(r"D:\файлы Гермес\12_Бот_сметы")
ГАЛЕРЕЯ = HERE / "gallery" if (HERE / "gallery").exists() else _БОТ / "projects"
РАБОТЫ = HERE / "works" if (HERE / "works").exists() else _БОТ / "works"
ПРОЕКТЫ = HERE / "projects"
ПРОЕКТЫ.mkdir(exist_ok=True)

# визуалы из конструктора (этап 4)
ВИЗУАЛЫ = HERE / "визуалы"
ВИЗУАЛЫ.mkdir(exist_ok=True)


def variant_hash(*части) -> str:
    """Короткий хеш из параметров — чтобы имена картинок не повторялись."""
    import hashlib
    с = "|".join(str(x) for x in части).encode("utf-8")
    return hashlib.md5(с).hexdigest()[:6]


# разметка: фото, ждущие категории, и результат
НЕРАЗМЕЧЕНО = HERE / "неразмечено"
НЕРАЗМЕЧЕНО.mkdir(exist_ok=True)
РАЗМЕТКА = HERE / "разметка.json"
# если своей подпапки нет — берём у бота (там 182 размеченных фото)
if not any(НЕРАЗМЕЧЕНО.iterdir()) and (_БОТ / "page").exists():
    НЕРАЗМЕЧЕНО = _БОТ / "page"

import parse_cut                                    # noqa: E402
from make_smeta import build as build_smeta          # noqa: E402
import vizual                                        # noqa: E402
import constructor                                   # noqa: E402
import k3d                                           # noqa: E402

# параметрическое ядро (сборка модулей для 3D-конструктора на главной)
МОДЕЛЬ = ENGINE / "модель"
if str(МОДЕЛЬ) not in sys.path:
    sys.path.insert(0, str(МОДЕЛЬ))
import сборка                                        # noqa: E402

# кэш сборок: состав модулей → JSON. Смета детерминированная, поэтому кэш безопасен.
СБОРКИ_КЭШ: dict[str, bytes] = {}

PORT = int(os.environ.get("KABINET_PORT", "8090"))
PASSWORD = os.environ.get("KABINET_PASSWORD", "")
PASS_FILE = HERE / "пароль.txt"
MAX_UPLOAD = 25 * 1024 * 1024
ПРЕФИКС_ПО_УМОЛЧАНИЮ = os.environ.get("KABINET_PREFIX", "")

SESSIONS: dict[str, float] = {}
SESSION_TTL = 30 * 24 * 3600
СЦЕНЫ_КЭШ: dict[str, bytes] = {}

# категории галереи — как в боте
КАТЕГОРИИ = {
    "кухни": "🍳 Кухни", "шкафы": "🚪 Шкафы", "тумбы": "🗄 Тумбы",
    "гардеробные": "🛏 Гардеробные", "столы": "🪑 Столы", "ванная": "🛁 Ванная",
    "детские": "🧸 Детские", "коммерция": "💼 Коммерческое", "прочее": "📦 Прочее",
}
ФОТО = (".jpg", ".jpeg", ".png", ".webp")

ТЕКСТ_СПРАВКИ = """
<h1>Как это работает</h1>
<div class="card">
  <h2 style="margin-top:0">📐 Смета</h2>
  <ol>
    <li>Вводишь название проекта.</li>
    <li>Прикрепляешь файл(ы) раскроя из PRO100 — <b>txt</b>.
        Можно один (тот, где есть материал) или два: «детали» + «сложная».</li>
    <li>Жмёшь «Считать смету» — получаешь Excel из 9 листов:
        ИТОГО, Корпус-ЛДСП, Фасады, Столешница, Фурнитура, Работа,
        Техника, Спецификация, Карта раскроя.</li>
  </ol>

  <h2>🖼 Визуал</h2>
  <ol>
    <li>Прикрепляешь 3D-рендер или фото помещения.</li>
    <li>Выбираешь вариант: день, вечер, как в журнале, светлые
        или тёмные фасады, просто фотореализм.</li>
    <li>Через минуту получаешь картинку, которую можно показать заказчику.</li>
  </ol>
  <p class="sub">Планировка, фасады и техника остаются на своих местах —
     меняются только материалы, свет и «жизнь» в кадре.</p>

  <h2>📁 Проекты и ✨ Наши работы</h2>
  <p>Готовые работы и визуалы, которые мы уже делали. Можно показать клиенту
     как пример.</p>

  <h2>Сколько стоит</h2>
  <p>Смета — бесплатно. Одна картинка визуала ≈ 2 ₽ с баланса.
     Если токены кончились — напиши Арту.</p>

  <h2>Вопросы</h2>
  <p>Арт или Костя.</p>
</div>
"""

СТИЛЬ = """
*{box-sizing:border-box}
body{margin:0;font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
     background:#0f1115;color:#eef1f6;-webkit-font-smoothing:antialiased}
a{color:#e8b25c;text-decoration:none}
img{max-width:100%}
.wrap{max-width:1000px;margin:0 auto;padding:24px 18px 60px}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;
       border-bottom:1px solid #2a2f3a;padding-bottom:14px;margin-bottom:24px}
.logo{font-weight:800;display:flex;align-items:center;gap:9px}
.logo i{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;
        background:linear-gradient(135deg,#e8b25c,#c98a3a);color:#20160a;font-style:normal;font-weight:900}
h1{font-size:26px;letter-spacing:-.5px;margin:0 0 8px}
h2{font-size:19px;margin:26px 0 10px;letter-spacing:-.3px}
p.sub{color:#9aa4b2;margin:0 0 18px}
.card{background:#171a21;border:1px solid #2a2f3a;border-radius:16px;padding:22px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.btn{display:inline-block;padding:11px 20px;border-radius:999px;font-weight:650;font-size:15px;
     background:linear-gradient(135deg,#e8b25c,#f0c987);color:#20160a;border:none;cursor:pointer;
     text-align:center}
.btn:hover{filter:brightness(1.06)}
.btn.ghost{background:transparent;color:#eef1f6;border:1px solid #2a2f3a}
.btn.ghost:hover{border-color:#e8b25c;color:#e8b25c}
.btn.big{display:block;padding:22px;font-size:17px;border-radius:16px}
input[type=text],input[type=password],input[type=file],select{
  width:100%;padding:12px 14px;border-radius:11px;border:1px solid #2a2f3a;
  background:#0f1115;color:#eef1f6;font-size:15px;margin:6px 0 14px}
label{font-size:14px;color:#9aa4b2}
.pill{display:inline-block;padding:5px 12px;border-radius:999px;font-size:13px;
      background:#1e222b;border:1px solid #2a2f3a;color:#9aa4b2}
.ok{color:#7bd88f}.bad{color:#ef7b7b}
.gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:14px}
.gal a{display:block;border-radius:14px;overflow:hidden;border:1px solid #2a2f3a;
       background:#171a21;aspect-ratio:4/3}
.gal img{width:100%;height:100%;object-fit:cover;display:block;transition:.35s}
.gal a:hover img{transform:scale(1.05)}
.result{width:100%;border-radius:14px;border:1px solid #2a2f3a;margin:12px 0}
table{width:100%;border-collapse:collapse}
td,th{padding:10px 8px;border-bottom:1px solid #2a2f3a;text-align:left;font-size:15px}
th{color:#9aa4b2;font-weight:600;font-size:13px}
ol{padding-left:22px;color:#cfd6e0}
@media(max-width:700px){.grid{grid-template-columns:1fr}}
"""


def страница(титул: str, тело: str) -> bytes:
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(титул)}</title><style>{СТИЛЬ}</style></head>
<body><div class="wrap">{тело}</div></body></html>""".encode("utf-8")


class Кабинет(BaseHTTPRequestHandler):
    server_version = "Kabinet/2.0"

    # ── утилиты ────────────────────────────────────────────────────────
    def путь(self) -> str:
        return unquote(urlsplit(self.path).path)

    def префикс(self) -> str:
        п = (self.headers.get("X-Forwarded-Prefix") or "").rstrip("/")
        return п or ПРЕФИКС_ПО_УМОЛЧАНИЮ

    def сс(self, путь: str) -> str:
        """Ссылка с префиксом кабинета. Кириллицу кодируем — иначе nginx
        и браузер спорят о кодировке и отдают 404."""
        from urllib.parse import quote
        куски = путь.split("/")
        кодированный = "/".join(quote(кусок, safe="") if кусок else "" for кусок in куски)
        return self.префикс() + кодированный

    def отправить(self, код: int, тело: bytes, тип="text/html; charset=utf-8", доп=None):
        self.send_response(код)
        self.send_header("Content-Type", тип)
        self.send_header("Content-Length", str(len(тело)))
        for k, v in (доп or {}).items():
            # HTTP-заголовки знают только latin-1 — кириллицу кодируем
            # по RFC 5987 (filename*=UTF-8''...), иначе соединение рвётся.
            try:
                v.encode("latin-1")
                self.send_header(k, v)
            except UnicodeEncodeError:
                from urllib.parse import quote
                значение = re.sub(r'filename="([^"]*)"',
                                  lambda m: "filename*=UTF-8''" + quote(m.group(1)),
                                  v)
                self.send_header(k, значение.encode("latin-1", "replace").decode("latin-1"))
        self.end_headers()
        self.wfile.write(тело)

    def сессия(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return None
        c = http.cookies.SimpleCookie()
        try:
            c.load(raw)
        except Exception:
            return None
        t = c.get("sid")
        if not t:
            return None
        v = SESSIONS.get(t.value)
        return t.value if (v and time.time() - v < SESSION_TTL) else None

    def форма(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX_UPLOAD:
            raise ValueError("файл слишком большой (лимит 25 МБ)")
        тело = self.rfile.read(n)
        поля, файлы = {}, {}
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            from urllib.parse import unquote_plus
            for пара in тело.decode("utf-8", "replace").split("&"):
                if "=" in пара:
                    k, v = пара.split("=", 1)
                    поля[unquote_plus(k)] = unquote_plus(v)
            return {"поля": поля, "файлы": файлы}
        m = re.search(r'boundary=([^;]+)', ctype)
        if not m:
            return {"поля": поля, "файлы": файлы}
        sep = b"--" + m.group(1).strip('"').encode()
        for часть in тело.split(sep):
            if not часть.strip() or b"\r\n\r\n" not in часть:
                continue
            голова, данные = часть.split(b"\r\n\r\n", 1)
            данные = данные.rstrip(b"\r\n--")
            г = голова.decode("utf-8", "replace")
            имя = re.search(r'name="([^"]+)"', г)
            if not имя:
                continue
            фн = re.search(r'filename="([^"]*)"', г)
            if фн and фн.group(1):
                файлы[имя.group(1)] = (фн.group(1), данные)
            else:
                поля[имя.group(1)] = данные.decode("utf-8", "replace").strip()
        return {"поля": поля, "файлы": файлы}

    def папка_проекта(self, имя: str) -> pathlib.Path:
        чистое = "".join(ch for ch in имя if ch.isalnum() or ch in " -_()[]").strip()[:60]
        чистое = чистое or time.strftime("проект_%d%m_%H%M")
        d = ПРОЕКТЫ / чистое
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── маршруты ───────────────────────────────────────────────────────
    def do_GET(self):
        п = self.путь()
        if п == "/health":
            return self.отправить(200, b"ok", "text/plain")
        if п == "/выход":
            т = self.сессия()
            if т:
                SESSIONS.pop(т, None)
            return self.отправить(303, b"", доп={"Location": self.сс("/")})
        # ── статика без входа: файлы three.js нужны браузеру ДО сессии ──
        # ES-модули запрашиваются отдельным запросом и cookie может не уйти —
        # поэтому /vendor/ отдаём открыто (это публичная библиотека, не данные).
        if п.startswith("/vendor/"):
            return self.отдать_vendor(п[len("/vendor/"):])
        # ── публичный API 3D-конструктора главной страницы (без входа) ──
        # Отдаёт только геометрию и смету собранной кухни: ни проектов, ни
        # паролей, ни файлов. Кэш по составу — состав детерминированный.
        if "/api/конструктор3д" in п:
            return self.конструктор3д_json()
        if not self.сессия():
            return self.отправить(200, self.вход())

        if п == "/":
            return self.отправить(200, self.главная())
        if п == "/конструктор":
            return self.отправить(200, self.страница_конструктора())
        if п == "/конструктор3д":
            return self.отправить(200, self.страница_конструктора_3д())
        if п == "/сцена3д":
            return self.сцена_3д_json()
        if п.startswith("/визуал/"):
            return self.отдать_визуал(п[len("/визуал/"):])
        if п == "/проекты":
            return self.отправить(200, self.страница_галереи())
        if п == "/работы":
            return self.отправить(200, self.страница_работ())
        if п == "/справка":
            return self.отправить(200, self.страница_справки())
        if п.startswith("/gallery/") or п.startswith("/gallery/"):
            return self.отдать_галерею(п[len("/gallery/"):] if п.startswith("/gallery/") else п[len("/gallery/"):])
        if п.startswith("/work/") or п.startswith("/work/"):
            return self.отдать_работу(п[len("/work/"):] if п.startswith("/work/") else п[len("/work/"):])
        if п.startswith("/file/") or п.startswith("/file/"):
            return self.отдать_файл(п[len("/file/"):] if п.startswith("/file/") else п[len("/file/"):])
        if п.startswith("/thumb/"):
            return self.отдать_миниатюру(п[len("/thumb/"):])
        if п.startswith("/разметка") or п.startswith("/markup"):
            return self.отправить(200, self.страница_разметки())
        if п.startswith("/неразмеченное/") or п.startswith("/unmarked/"):
            хв = п[len("/неразмеченное/"):] if п.startswith("/неразмеченное/") else п[len("/unmarked/"):]
            return self.отдать_неразмеченное(хв)
        return self.отправить(404, страница("404", "<h1>Не найдено</h1>"
                                            f'<p><a href="{self.сс("/")}">← В кабинет</a></p>'))

    def do_POST(self):
        п = self.путь()
        # сборка с главной: состав модулей приходит телом JSON (URL не раздувается)
        if "/api/конструктор3д" in п:
            return self.конструктор3д_json(post=True)
        if п == "/вход":
            try:
                д = self.форма()
            except Exception as e:
                return self.отправить(400, страница("Ошибка", f"<h1>Ошибка</h1><p>{e}</p>"))
            if д["поля"].get("пароль", "") == get_password():
                tok = secrets.token_urlsafe(24)
                SESSIONS[tok] = time.time()
                return self.отправить(303, b"", доп={
                    "Location": self.сс("/"),
                    "Set-Cookie": f"sid={tok}; Path=/; HttpOnly; Max-Age={SESSION_TTL}; SameSite=Lax"})
            return self.отправить(200, self.вход("Пароль не подошёл"))
        if not self.сессия():
            return self.отправить(403, страница("Нет доступа", "<h1>Нужно войти</h1>"))

        # сохранение разметки приходит JSON-ом, а не формой
        if п.startswith("/markup/save") or п.startswith("/разметка/save"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                данные = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                итог = self.сохранить_разметку(данные)
                return self.отправить(200, json.dumps(итог, ensure_ascii=False).encode("utf-8"),
                                      "application/json; charset=utf-8")
            except Exception as e:
                return self.отправить(200,
                    json.dumps({"ok": False, "ошибка": str(e)[:200]}, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8")

        # конструктор → визуал по чертежу (без загрузки файла)
        if п == "/визуал":
            return self.визуал_из_конструктора()

        try:
            д = self.форма()
        except Exception as e:
            return self.отправить(400, страница("Ошибка", f"<h1>Ошибка</h1><p>{e}</p>"))
        вид = д["поля"].get("действие", "")
        if вид == "смета":
            return self.сделать_смету(д)
        if вид == "визуал":
            return self.сделать_визуал(д)
        return self.отправить(400, страница("Ошибка", "<h1>Неизвестное действие</h1>"))

    # ── шапка ──────────────────────────────────────────────────────────
    def шапка(self) -> str:
        return f"""<header>
  <a class="logo" href="{self.сс('/')}"><i>К</i> Кабинет мастера</a>
  <nav style="display:flex;gap:16px;flex-wrap:wrap;font-size:15px">
    <a href="{self.сс('/')}">Главная</a>
    <a href="{self.сс('/конструктор')}">Конструктор</a>
    <a href="{self.сс('/конструктор3д')}">3D-конструктор</a>
    <a href="{self.сс('/проекты')}">Проекты</a>
    <a href="{self.сс('/работы')}">Наши работы</a>
    <a href="{self.сс('/разметка')}">Разметка</a>
    <a href="{self.сс('/справка')}">Справка</a>
    <a href="{self.сс('/выход')}" style="color:#9aa4b2">Выход</a>
  </nav>
</header>"""

    # ── страницы ───────────────────────────────────────────────────────
    def вход(self, ошибка: str = "") -> bytes:
        ош = f'<p class="bad">{html.escape(ошибка)}</p>' if ошибка else ""
        return страница("Вход", f"""
<div class="card" style="max-width:420px;margin:60px auto">
  <h1>Кабинет мастера</h1>
  <p class="sub">Расчёт смет и визуализация мебели.</p>
  {ош}
  <form method="post" action="{self.сс('/вход')}">
    <label>Пароль</label>
    <input type="password" name="пароль" autofocus required>
    <button class="btn" type="submit" style="width:100%">Войти</button>
  </form>
</div>""")

    def главная(self) -> bytes:
        try:
            бал = vizual.balance()
            бал_стр = f"~{бал:.1f}" if бал is not None else "?"
        except Exception:
            бал_стр = "?"
        return страница("Кабинет мастера", self.шапка() + f"""
<h1>Что делаем?</h1>
<p class="sub">Всё, что раньше было в Telegram — теперь в браузере.</p>

<div class="grid">
  <div class="card">
    <h2 style="margin-top:0">📐 Смета из раскроя</h2>
    <p class="sub">Файл <b>txt</b> из PRO100 (можно два).</p>
    <form method="post" enctype="multipart/form-data">
      <input type="hidden" name="действие" value="смета">
      <label>Название проекта</label>
      <input type="text" name="проект" placeholder="Кухня Ивановых" required>
      <label>Файл(ы) раскроя</label>
      <input type="file" name="раскрой" accept=".txt,.tsv,.csv" multiple required>
      <button class="btn" type="submit" style="width:100%">🧮 Считать смету</button>
    </form>
  </div>

  <div class="card">
    <h2 style="margin-top:0">🖼 Визуал</h2>
    <p class="sub">Рендер или фото → фотореалистичная картинка.</p>
    <form method="post" enctype="multipart/form-data">
      <input type="hidden" name="действие" value="визуал">
      <label>Название проекта</label>
      <input type="text" name="проект" placeholder="Кухня Ивановых" required>
      <label>Фото или 3D-рендер</label>
      <input type="file" name="фото" accept="image/*" required>
      <label>Вариант</label>
      <select name="вариант">
        <option value="день">День</option>
        <option value="вечер">Вечерний свет</option>
        <option value="журнал">Как в журнале</option>
        <option value="без_изменений">Фотореализм (без изменений)</option>
        <option value="светлые_фасады">Светлые фасады</option>
        <option value="тёмные_фасады">Тёмные фасады</option>
      </select>
      <button class="btn" type="submit" style="width:100%">🎨 Сделать картинку</button>
      <p class="sub" style="margin:10px 0 0;font-size:13px">
        одна картинка ≈ минута · баланс: {бал_стр}</p>
    </form>
  </div>
</div>

<div class="grid" style="margin-top:4px">
  <a class="btn ghost big" href="{self.сс('/проекты')}">📁 Проекты — готовые работы</a>
  <a class="btn ghost big" href="{self.сс('/работы')}">✨ Наши работы — визуалы</a>
</div>""")

    def _фото(self, папка: pathlib.Path) -> list[pathlib.Path]:
        if not папка.exists():
            return []
        return sorted(p for p in папка.rglob("*") if p.suffix.lower() in ФОТО
                      and "cache" not in p.parts and "thumb" not in str(p).lower())

    def страница_галереи(self) -> bytes:
        фото = self._фото(ГАЛЕРЕЯ)
        # группирую по категориям, если подпапки есть
        разделы = []
        подпапки = [p for p in ГАЛЕРЕЯ.iterdir() if p.is_dir()] if ГАЛЕРЕЯ.exists() else []
        if подпапки:
            for под in sorted(подпапки):
                своё = self._фото(под)
                if своё:
                    label = КАТЕГОРИИ.get(под.name.lower(), под.name)
                    разделы.append((label, своё[:60]))
            корень = sorted(p for p in ГАЛЕРЕЯ.glob("*") if p.suffix.lower() in ФОТО)
            if корень:
                разделы.append(("Другое", корень[:60]))
        else:
            разделы.append(("Все работы", фото[:120]))

        блоки = []
        for label, список in разделы:
            картинки = "".join(
                f'<a href="{self.сс("/gallery/" + str(п.relative_to(ГАЛЕРЕЯ)).replace(chr(92), "/"))}" '
                f'target="_blank"><img loading="lazy" src="{self.сс("/thumb/gallery/" + str(п.relative_to(ГАЛЕРЕЯ)).replace(chr(92), "/"))}" alt=""></a>'
                for п in список)
            блоки.append(f'<h2>{html.escape(label)} <span class="pill">{len(список)}</span></h2>'
                         f'<div class="gal">{картинки}</div>')

        тело = ("".join(блоки) if блоки else
                "<div class='card'><p class='sub'>Витрина пока пустая — Костя готовит фото.</p></div>")
        return страница("Проекты", self.шапка() +
                        f"<h1>📁 Проекты</h1>"
                        f"<p class='sub'>Готовые работы. Нажми на фото, чтобы открыть大きе.</p>{тело}")

    def страница_работ(self) -> bytes:
        фото = self._фото(РАБОТЫ)
        картинки = "".join(
            f'<a href="{self.сс("/work/" + п.name)}" target="_blank">'
            f'<img loading="lazy" src="{self.сс("/thumb/work/" + п.name)}" alt=""></a>'
            for п in фото)
        тело = (f'<div class="gal">{картинки}</div>' if фото else
                "<div class='card'><p class='sub'>Пока пусто.</p></div>")
        return страница("Наши работы", self.шапка() +
                        f"<h1>✨ Наши работы</h1>"
                        f"<p class='sub'>{len(фото)} визуалов: из 3D-рендера — фотореалистичная "
                        f"картинка.</p>{тело}")

    def страница_справки(self) -> bytes:
        return страница("Как это работает", self.шапка() + ТЕКСТ_СПРАВКИ)

    def страница_конструктора(self) -> bytes:
        """Конструктор: выбор типового изделия, размеры, цвет фасада (этап 3)."""
        return страница("Конструктор мебели",
                        self.шапка() + constructor.страница_конструктора(self.префикс()))

    # ── конструктор → визуал (этап 4) ──────────────────────────────────
    def визуал_из_конструктора(self):
        """Принимает параметры конструктора, рисует чертёж в PNG и гонит его в Pollinations."""
        try:
            д = self.форма()
        except Exception as e:
            return self.отправить(400, страница("Ошибка", f"<h1>Ошибка</h1><p>{e}</p>"))
        п = д["поля"]
        кат = constructor.читать_каталог()
        шаблон = next((t for t in кат.get("шаблоны", []) if t["id"] == п.get("шаблон", "")), None)
        тип = (шаблон or {}).get("тип", "Изделие")

        def числ(ключ, по_умолч, низ, верх):
            try:
                з = int(float(str(п.get(ключ, "")).replace(",", ".")))
            except Exception:
                return по_умолч
            return max(низ, min(верх, з))

        w = числ("ширина", 1500, 200, 6000)
        h = числ("высота", 800, 200, 3000)
        d = числ("глубина", 600, 150, 1200)
        цв = п.get("цвет", "white")
        вариант = п.get("вариант", "из_чертежа_день")
        if вариант not in vizual.ИЗ_ЧЕРТЕЖА:
            вариант = "из_чертежа_день"

        цвета = {k: (a, b) for k, _, a, b in constructor.ПАЛИТРА}
        цв_а, цв_б = цвета.get(цв, ("#f4f4f2", "#e3e3df"))

        настройки = {"шаблон": п.get("шаблон", ""), "ширина": w, "высота": h,
                     "глубина": d, "цвет": цв}

        try:
            имя = f"констр_{int(time.time())}_{variant_hash(тип, w, h, цв)}.jpg"
            чертёж = ВИЗУАЛЫ / f"чертёж_{имя}.png"
            constructor.отрисовать_чертёж(тип, w, h, d, цв_а, цв_б, чертёж)
            vizual.generate(чертёж, вариант, ВИЗУАЛЫ / имя)
        except Exception as e:
            return self.отправить(200, страница(
                "Конструктор мебели",
                self.шапка() + constructor.страница_конструктора(
                    self.префикс(), ошибка=str(e), настройки=настройки)))

        return self.отправить(200, страница(
            "Конструктор мебели",
            self.шапка() + constructor.страница_конструктора(
                self.префикс(), результат=имя, настройки=настройки)))

    def отдать_визуал(self, хвост: str):
        """Отдаёт готовую картинку конструктора."""
        ф = self._безопасно(ВИЗУАЛЫ, хвост)
        if not ф:
            return self.отправить(404, b"no", "text/plain")
        типы = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
        return self.отправить(200, ф.read_bytes(),
                              типы.get(ф.suffix.lower(), "application/octet-stream"))

    # ── 3D-конструктор (этап 6) ────────────────────────────────────────
    def страница_конструктора_3д(self) -> bytes:
        """3D-просмотр: те же параметры, но объёмно (Three.js)."""
        п = self.поля_из_url()
        try:
            ш = float(п.get("ширина") or 1600)
            в = float(п.get("высота") or 2200)
            г = float(п.get("глубина") or 600)
        except Exception:
            ш, в, г = 1600.0, 2200.0, 600.0
        кат = k3d._каталог_шаблонов()
        шаблон = next((t for t in кат if t["id"] == п.get("шаблон")), None) or (кат[0] if кат else {})
        ошибка = ""
        данные = None
        try:
            данные = k3d.сцена(
                шаблон, ш, в, г,
                п.get("корпус", "лдсп16_белый"),
                п.get("фасад", "мдф18_эмаль_белая"),
                bool(п.get("столешница")), п.get("столешница_материал", "лдсп38_дуб"))
        except Exception as e:
            ошибка = f"{type(e).__name__}: {e}"
        html_стр = k3d.страница_3д(self.префикс(), сцена_данные=данные, ошибка=ошибка,
                                   параметры={**п, "шаблон": (шаблон or {}).get("id", "")})
        return страница("3D-конструктор", self.шапка() + html_стр)

    def сцена_3д_json(self) -> bytes:
        """GET /сцена3д → JSON сцены + сметы. Пересчёт без перезагрузки,
        кэш на сервере по хешу параметров."""
        import hashlib
        п = self.поля_из_url()
        хеш = hashlib.md5(json.dumps(п, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if хеш in СЦЕНЫ_КЭШ:
            return self.отправить(200, СЦЕНЫ_КЭШ[хеш], "application/json; charset=utf-8")
        try:
            ш = float(п.get("ширина") or 1600)
            в = float(п.get("высота") or 2200)
            г = float(п.get("глубина") or 600)
        except Exception:
            ш, в, г = 1600.0, 2200.0, 600.0
        кат = k3d._каталог_шаблонов()
        шаблон = next((t for t in кат if t["id"] == п.get("шаблон")), None) or {}
        try:
            данные = k3d.сцена3д_полная(
                шаблон, ш, в, г,
                п.get("корпус", "лдсп16_белый"),
                п.get("фасад", "мдф18_эмаль_белая"),
                bool(п.get("столешница")), п.get("столешница_материал", "лдсп38_дуб"))
            тело = json.dumps({"ok": True, **данные}, ensure_ascii=False).encode("utf-8")
        except Exception as e:
            тело = json.dumps({"ok": False, "ошибка": f"{type(e).__name__}: {e}"},
                              ensure_ascii=False).encode("utf-8")
        СЦЕНЫ_КЭШ[хеш] = тело
        if len(СЦЕНЫ_КЭШ) > 200:
            СЦЕНЫ_КЭШ.clear()
        return self.отправить(200, тело, "application/json; charset=utf-8")

    def конструктор3д_json(self, post: bool = False) -> bytes:
        """GET /api/конструктор3д — ПУБЛИЧНЫЙ (без входа) API главной страницы.

        ?каталог=1            → модули, пределы, материалы, шаблоны (без цен)
        ?сборка={json}        → сцена + смета собранной кухни
        Цены — только из прайса и config-файла. Ошибки — списком, не «примерно».
        """
        import hashlib
        п = self.поля_из_url()
        try:
            if post:
                n = int(self.headers.get("Content-Length", 0) or 0)
                if n > 40000:
                    raise ValueError("сборка слишком большая")
                состав = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                if not isinstance(состав, dict):
                    raise ValueError("сборка должна быть объектом JSON")
                хеш = hashlib.md5(json.dumps(состав, sort_keys=True,
                                             ensure_ascii=False).encode("utf-8")).hexdigest()
                if хеш not in СБОРКИ_КЭШ:
                    СБОРКИ_КЭШ[хеш] = json.dumps(сборка.собрать(состав),
                                                 ensure_ascii=False).encode("utf-8")
                    if len(СБОРКИ_КЭШ) > 300:
                        СБОРКИ_КЭШ.clear()
                return self.отправить(200, СБОРКИ_КЭШ[хеш], "application/json; charset=utf-8",
                                      доп={"Cache-Control": "no-store",
                                           "Access-Control-Allow-Origin": "*"})
            if п.get("каталог") in ("1", "true", "да"):
                данные = сборка.каталог()
            else:
                сырьё = п.get("сборка") or "{}"
                if len(сырьё) > 20000:
                    raise ValueError("сборка слишком большая")
                состав = json.loads(сырьё) if сырьё.strip() else {}
                if not isinstance(состав, dict):
                    raise ValueError("сборка должна быть объектом JSON")
                хеш = hashlib.md5(json.dumps(состав, sort_keys=True,
                                             ensure_ascii=False).encode("utf-8")).hexdigest()
                if хеш not in СБОРКИ_КЭШ:
                    СБОРКИ_КЭШ[хеш] = json.dumps(сборка.собрать(состав),
                                                 ensure_ascii=False).encode("utf-8")
                    if len(СБОРКИ_КЭШ) > 300:
                        СБОРКИ_КЭШ.clear()
                return self.отправить(200, СБОРКИ_КЭШ[хеш], "application/json; charset=utf-8",
                                      доп={"Cache-Control": "public, max-age=300",
                                           "Access-Control-Allow-Origin": "*"})
        except Exception as e:
            данные = {"ok": False, "ошибки": [f"{type(e).__name__}: {str(e)[:200]}"]}
        тело = json.dumps(данные, ensure_ascii=False).encode("utf-8")
        return self.отправить(200, тело, "application/json; charset=utf-8",
                              доп={"Cache-Control": "public, max-age=300",
                                   "Access-Control-Allow-Origin": "*"})

    def отдать_vendor(self, хвост: str):
        """Файлы three.js с сервера (не CDN)."""
        ф = self._безопасно(HERE / "vendor", хвост)
        if not ф:
            return self.отправить(404, b"no", "text/plain")
        return self.отправить(200, ф.read_bytes(), "text/javascript; charset=utf-8")

    def поля_из_url(self) -> dict:
        """GET-параметры страницы как словарь."""
        from urllib.parse import parse_qs
        q = parse_qs(urlsplit(self.path).query)
        return {k: v[0] for k, v in q.items()}

    # ── отдача картинок (с миниатюрами) ────────────────────────────────
    def _безопасно(self, корень: pathlib.Path, хвост: str) -> pathlib.Path | None:
        хвост = хвост.replace("%2F", "/").replace("%2f", "/")
        if ".." in хвост:
            return None
        ф = корень / хвост
        try:
            ф.resolve().relative_to(корень.resolve())
        except Exception:
            return None
        return ф if ф.is_file() else None

    def отдать_галерею(self, хвост: str):
        ф = self._безопасно(ГАЛЕРЕЯ, хвост)
        if not ф:
            return self.отправить(404, b"no", "text/plain")
        типы = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp"}
        return self.отправить(200, ф.read_bytes(), типы.get(ф.suffix.lower(), "application/octet-stream"))

    def отдать_работу(self, хвост: str):
        ф = self._безопасно(РАБОТЫ, хвост)
        if not ф:
            return self.отправить(404, b"no", "text/plain")
        типы = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
        return self.отправить(200, ф.read_bytes(), типы.get(ф.suffix.lower(), "application/octet-stream"))

    def отдать_миниатюру(self, хвост: str):
        """Сжатая копия для галереи — быстрее грузится."""
        if хвост.startswith("gallery/") or хвост.startswith("галерея/"):
            ф = self._безопасно(ГАЛЕРЕЯ, хвост[8:] if хвост.startswith("gallery/") else хвост[len("галерея/"):])
        elif хвост.startswith("work/") or хвост.startswith("работа/"):
            ф = self._безопасно(РАБОТЫ, хвост[5:] if хвост.startswith("work/") else хвост[len("работа/"):])
        else:
            return self.отправить(404, b"no", "text/plain")
        if not ф:
            return self.отправить(404, b"no", "text/plain")
        # кэш миниатюр
        кэш = HERE / "cache_thumbs"
        кэш.mkdir(exist_ok=True)
        имя = str(abs(hash(str(ф)))) + ".jpg"
        мини = кэш / имя
        if not мини.exists() or мини.stat().st_mtime < ф.stat().st_mtime:
            try:
                from PIL import Image
                im = Image.open(ф)
                if im.mode in ("RGBA", "P", "LA"):
                    im = im.convert("RGB")
                im.thumbnail((640, 640), Image.LANCZOS)
                im.save(мини, "JPEG", quality=82, optimize=True)
            except Exception:
                return self.отправить(200, ф.read_bytes(), "image/jpeg")
        return self.отправить(200, мини.read_bytes(), "image/jpeg")


    # ── РАЗМЕТКА (обучение) ────────────────────────────────────────────
    def _разметка(self) -> dict:
        """Текущая разметка: {имя файла: категория}."""
        if РАЗМЕТКА.exists():
            try:
                return json.loads(РАЗМЕТКА.read_text(encoding="utf-8"))
            except Exception:
                pass
        # у бота своя разметка — берём её
        бот_р = pathlib.Path(r"D:\файлы Гермес\12_Бот_сметы\разметка.json")
        if бот_р.exists():
            try:
                return json.loads(бот_р.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _сохранить_разметку(self, д: dict):
        РАЗМЕТКА.write_text(json.dumps(д, ensure_ascii=False, indent=1),
                            encoding="utf-8")

    def _неразмеченные(self) -> list[pathlib.Path]:
        """Фото, которым ещё не назначена категория."""
        if not НЕРАЗМЕЧЕНО.exists():
            return []
        сделано = self._разметка()
        фото = []
        for p in sorted(НЕРАЗМЕЧЕНО.rglob("*")):
            if p.is_file() and p.suffix.lower() in ФОТО:
                if p.name not in сделано or not сделано.get(p.name):
                    фото.append(p)
        return фото

    def страница_разметки(self) -> bytes:
        сделано = self._разметка()
        фото = self._неразмеченные()
        всего = len([p for p in НЕРАЗМЕЧЕНО.rglob("*")
                     if p.is_file() and p.suffix.lower() in ФОТО]) if НЕРАЗМЕЧЕНО.exists() else 0
        размечено = len([v for v in сделано.values() if v])

        # статистика по категориям
        from collections import Counter
        счёт = Counter(v for v in сделано.values() if v)
        строчки = "".join(
            f'<tr><td>{КАТЕГОРИИ.get(k, k)}</td><td><b>{n}</b></td></tr>'
            for k, n in счёт.most_common())

        # сетка неразмеченных
        клетки = "".join(
            f'<div class="клетка" data-файл="{html.escape(п.name)}">'
            f'<img loading="lazy" src="{self.сс("/неразмеченное/" + п.name)}" alt="">'
            f'<div class="кн">'
            + "".join(f'<button type="button" data-кат="{k}">{v[:12]}</button>'
                      for k, v in КАТЕГОРИИ.items())
            + '</div></div>'
            for п in фото[:60])

        пусто = ('<div class="card"><p class="ok">✅ Все фото размечены! '
                 'Когда Костя пришлёт новые — они появятся здесь.</p></div>')
        сетка = (f'<div class="клетки">{клетки}</div>'
                 f'<p class="sub">Показаны первые 60 из {len(фото)}.</p>'
                 if фото else пусто)

        return страница("Разметка проектов", self.шапка() + f"""
<h1>🏷 Разметка проектов</h1>
<p class="sub">раскладывай фото по категориям — так система учится понимать,
что за мебель на снимке. Потом это попадёт в галерею «Проекты».</p>

<div class="card">
  <p><b>Размечено:</b> {размечено} из {всего} ·
     <b>осталось:</b> <span id="осталось">{len(фото)}</span></p>
  <table style="max-width:420px"><tr><th>Категория</th><th>Фото</th></tr>{строчки}</table>
</div>

<div class="card">
  <h2 style="margin-top:0">Что разложить</h2>
  <p class="sub">Нажми категорию под фото — оно сразу уедет в нужную папку.</p>
  {сетка}
  <button class="btn" id="сохранить" style="margin-top:16px">💾 Сохранить разметку</button>
  <span id="статус" class="sub" style="margin-left:12px"></span>
</div>

<style>
.клетки{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}}
.клетка{{border:1px solid #2a2f3a;border-radius:14px;overflow:hidden;background:#13161c}}
.клетка img{{width:100%;height:170px;object-fit:cover;display:block}}
.кн{{display:flex;flex-wrap:wrap;gap:4px;padding:8px}}
.кн button{{font-size:11px;padding:4px 7px;border-radius:7px;border:1px solid #2a2f3a;
            background:#1e222b;color:#cfd6e0;cursor:pointer}}
.кн button:hover{{border-color:#e8b25c;color:#e8b25c}}
.клетка.готово{{opacity:.28;pointer-events:none}}
</style>

<script>
const ВЫБОР = {{}};
document.querySelectorAll('.кн button').forEach(b => {{
  b.onclick = () => {{
    const клетка = b.closest('.клетка');
    const файл = клетка.dataset.файл;
    ВЫБОР[файл] = b.dataset.кат;
    клетка.classList.add('готово');
    document.getElementById('осталось').textContent =
      document.querySelectorAll('.клетка:not(.готово)').length;
  }};
}});

document.getElementById('сохранить').onclick = async () => {{
  const статус = document.getElementById('статус');
  const n = Object.keys(ВЫБОР).length;
  if (!n) {{ статус.textContent = 'Ничего не выбрано'; return; }}
  статус.textContent = 'Сохраняю…';
  try {{
    const r = await fetch('{self.сс("/markup/save")}', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(ВЫБОР)
    }});
    const j = await r.json();
    статус.textContent = j.ok
      ? ('✅ Сохранено: ' + j.записано + ' фото, разложено: ' + j.разложено)
      : ('❌ ' + (j.ошибка || 'ошибка'));
  }} catch (e) {{ статус.textContent = '❌ ' + e; }}
}};
</script>""")

    def отдать_неразмеченное(self, хвост: str):
        ф = self._безопасно(НЕРАЗМЕЧЕНО, хвост)
        if not ф:
            return self.отправить(404, b"no", "text/plain")
        типы = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp"}
        return self.отправить(200, ф.read_bytes(),
                              типы.get(ф.suffix.lower(), "application/octet-stream"))

    def сохранить_разметку(self, данные: dict):
        """Принимает {{файл: категория}}, пишет в разметку и раскладывает фото."""
        import shutil
        разметка = self._разметка()
        разложено = 0
        for имя, кат in данные.items():
            if кат not in КАТЕГОРИИ:
                continue
            разметка[имя] = кат
            # копирую фото в projects/<категория>/ — чтобы попало в галерею
            исток = None
            for p in НЕРАЗМЕЧЕНО.rglob("*"):
                if p.is_file() and p.name == имя:
                    исток = p
                    break
            if исток:
                цель_папка = ГАЛЕРЕЯ / кат
                try:
                    цель_папка.mkdir(parents=True, exist_ok=True)
                    цель = цель_папка / имя
                    if not цель.exists():
                        shutil.copy2(исток, цель)
                    разложено += 1
                except Exception as e:
                    print(f"не разложил {{имя}}: {{e}}", file=sys.stderr)
        self._сохранить_разметку(разметка)
        return {"ok": True, "записано": len(данные), "разложено": разложено}

    # ── действия ───────────────────────────────────────────────────────
    def сделать_смету(self, д: dict):
        поля, файлы = д["поля"], д["файлы"]
        имя = поля.get("проект", "").strip() or "проект"
        d = self.папка_проекта(имя)
        пути = []
        for ключ, (фн, данные) in файлы.items():
            if not данные:
                continue
            цель = d / f"{len(пути)+1}_{pathlib.Path(фн).name}"
            цель.write_bytes(данные)
            пути.append(цель)
        if not пути:
            return self.отправить(400, страница("Ошибка",
                                  "<h1>Не пришёл файл раскроя</h1>"
                                  f'<p><a href="{self.сс("/")}">← Назад</a></p>'))
        try:
            дет = parse_cut.parse_cut([str(п) for п in пути])
            дет_путь = parse_cut.save(дет, d / "детали.json")
            штамп = time.strftime("%d.%m_%H%M")
            res = build_smeta(d, дет_путь, f"СМЕТА_{штамп}.xlsx")
            s, xlsx = дет["stats"], res["xlsx"]
        except Exception as e:
            return self.отправить(200, страница("Ошибка сметы", self.шапка() + f"""
<h1 class="bad">Не получилось посчитать</h1>
<div class="card"><pre style="white-space:pre-wrap;color:#ef7b7b">{html.escape(str(e)[:800])}</pre>
<p class="sub">Проверь, что файл — это txt из PRO100.</p></div>"""))
        ссылка = self.сс("/file/" + d.name + "/" + xlsx.name)
        размер = xlsx.stat().st_size // 1024
        return self.отправить(200, страница("Смета готова", self.шапка() + f"""
<h1 class="ok">✅ Смета готова</h1>
<div class="card">
  <p><b>Позиций:</b> {s['позиций']} · <b>деталей:</b> {s['штук']} ·
     <b>площадь:</b> {s['площадь_м2']} м² · <b>без материала:</b> {s['без_материала']}</p>
  <p class="sub">9 листов: ИТОГО, Корпус-ЛДСП, Фасады, Столешница, Фурнитура,
     Работа, Техника, Спецификация, Карта раскроя.</p>
  <a class="btn big" href="{ссылка}" download>⬇ Скачать {html.escape(xlsx.name)} ({размер} КБ)</a>
  <p style="margin-top:18px"><a href="{self.сс('/проекты')}">Все проекты →</a></p>
</div>"""))

    def сделать_визуал(self, д: dict):
        поля, файлы = д["поля"], д["файлы"]
        имя = поля.get("проект", "").strip() or "проект"
        вариант = поля.get("вариант", "день")
        d = self.папка_проекта(имя)
        фото = None
        for ключ, (фн, данные) in файлы.items():
            if данные:
                фото = d / f"фото_{pathlib.Path(фн).name}"
                фото.write_bytes(данные)
                break
        if not фото:
            return self.отправить(400, страница("Ошибка", "<h1>Не пришло фото</h1>"))
        if вариант not in vizual.VARIANTS:
            вариант = "день"
        выход = d / f"визуал_{вариант}.jpg"
        try:
            vizual.generate(фото, вариант, выход)
        except Exception as e:
            return self.отправить(200, страница("Ошибка визуала", self.шапка() + f"""
<h1 class="bad">Не получилось сделать картинку</h1>
<div class="card"><pre style="white-space:pre-wrap;color:#ef7b7b">{html.escape(str(e)[:600])}</pre></div>"""))
        метка = vizual.NAMES.get(вариант, вариант)
        ссылка = self.сс("/file/" + d.name + "/" + выход.name)
        исходник = self.сс("/file/" + d.name + "/" + фото.name)
        return self.отправить(200, страница("Визуал готов", self.шапка() + f"""
<h1 class="ok">✅ «{html.escape(метка)}» готова</h1>
<div class="card">
  <img class="result" src="{ссылка}" alt="результат">
  <a class="btn" href="{ссылка}" download>⬇ Скачать</a>
  <a class="btn ghost" href="{self.сс('/')}">Ещё вариант</a>
  <p class="sub" style="margin-top:14px">Сравни с исходником:
     <a href="{исходник}">{html.escape(фото.name)}</a></p>
</div>"""))

    def отдать_файл(self, хвост: str):
        части = хвост.split("/", 1)
        if len(части) != 2:
            return self.отправить(404, b"not found", "text/plain")
        имя_проекта, имя_файла = части
        if ".." in имя_проекта or ".." in имя_файла or "/" in имя_файла:
            return self.отправить(403, b"forbidden", "text/plain")
        ф = ПРОЕКТЫ / имя_проекта / имя_файла
        if not ф.is_file():
            return self.отправить(404, b"not found", "text/plain")
        типы = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".txt": "text/plain; charset=utf-8", ".json": "application/json"}
        тип = типы.get(ф.suffix.lower(), "application/octet-stream")
        доп = {"Content-Disposition": f'attachment; filename="{ф.name}"'} \
            if тип.startswith("application/") else {}
        return self.отправить(200, ф.read_bytes(), тип, доп)

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%d.%m %H:%M:%S"), fmt % args))


def get_password() -> str:
    if PASSWORD:
        return PASSWORD
    if PASS_FILE.exists():
        п = PASS_FILE.read_text(encoding="utf-8").strip()
        if п:
            return п
    новый = secrets.token_urlsafe(9)
    PASS_FILE.write_text(новый + "\n", encoding="utf-8")
    return новый


def main():
    print(f"Кабинет мастера на порту {PORT}")
    print(f"Пароль: {get_password()}")
    print(f"Данные: галерея={ГАЛЕРЕЯ} · работы={РАБОТЫ}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Кабинет).serve_forever()


if __name__ == "__main__":
    main()
