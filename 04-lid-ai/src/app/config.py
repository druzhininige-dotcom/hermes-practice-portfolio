"""Конфигурация приложения. Все значения — из окружения / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILES_DIR = BASE_DIR / "profiles"


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


@dataclass(slots=True)
class Settings:
    # Telegram-аккаунт сборщика
    tg_api_id: int = 0
    tg_api_hash: str = ""
    tg_phone: str = ""
    tg_session_path: Path = BASE_DIR / "data" / "lid_ai.session"

    # Бот и группа ЛИДЫ
    bot_token: str = ""
    leads_group_id: int = 0
    leads_default_topic_id: int = 0

    # LLM
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_daily_budget_usd: float = 2.0
    llm_concurrency: int = 4
    # Цены за 1M токенов. Сверить с прайсом провайдера и задать в .env —
    # тогда cost guard считает реальные деньги, а не приблизительные.
    llm_price_in_per_1m: float = 0.27
    llm_price_out_per_1m: float = 1.10
    llm_timeout_s: float = 60.0

    # База и режим
    db_path: Path = BASE_DIR / "data" / "lid_ai.db"
    dry_run: bool = True
    log_level: str = "INFO"

    # Калибровка мультипрофиля
    secondary_max_gap: float = 3.0   # второй профиль показываем, если отстаёт не больше чем на столько
    min_score_to_notify: float | None = None  # None = порог профиля

    profiles_dir: Path = field(default=PROFILES_DIR)


def load_settings(env_file: Path | None = None) -> Settings:
    env_path = env_file or (BASE_DIR / ".env")
    if env_path.exists():
        load_dotenv(env_path, override=False)

    s = Settings()
    s.tg_api_id = _int("TG_API_ID", 0)
    s.tg_api_hash = os.getenv("TG_API_HASH", "")
    s.tg_phone = os.getenv("TG_PHONE", "")
    if os.getenv("TG_SESSION_PATH"):
        s.tg_session_path = Path(os.getenv("TG_SESSION_PATH"))
    s.bot_token = os.getenv("BOT_TOKEN", "")
    s.leads_group_id = _int("LEADS_GROUP_ID", 0)
    s.leads_default_topic_id = _int("LEADS_DEFAULT_TOPIC_ID", 0)

    s.llm_provider = os.getenv("LLM_PROVIDER", s.llm_provider)
    s.llm_model = os.getenv("LLM_MODEL", s.llm_model)
    s.llm_base_url = os.getenv("LLM_BASE_URL", s.llm_base_url)
    s.llm_api_key = os.getenv("LLM_API_KEY", "") or _key_from_hermes_env("DEEPSEEK_API_KEY")
    s.llm_daily_budget_usd = _float("LLM_DAILY_BUDGET_USD", s.llm_daily_budget_usd)
    s.llm_concurrency = _int("LLM_CONCURRENCY", s.llm_concurrency)
    s.llm_price_in_per_1m = _float("LLM_PRICE_IN_PER_1M", s.llm_price_in_per_1m)
    s.llm_price_out_per_1m = _float("LLM_PRICE_OUT_PER_1M", s.llm_price_out_per_1m)
    s.llm_timeout_s = _float("LLM_TIMEOUT_S", s.llm_timeout_s)

    if os.getenv("DB_PATH"):
        s.db_path = Path(os.getenv("DB_PATH"))
    s.dry_run = _bool("DRY_RUN", s.dry_run)
    s.log_level = os.getenv("LOG_LEVEL", s.log_level)
    s.secondary_max_gap = _float("SECONDARY_MAX_GAP", s.secondary_max_gap)
    if os.getenv("MIN_SCORE_TO_NOTIFY"):
        s.min_score_to_notify = _float("MIN_SCORE_TO_NOTIFY", 0.0)
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    return s


def _key_from_hermes_env(name: str) -> str:
    """Позволяет не дублировать ключ, если он уже есть в окружении Hermes."""
    if os.getenv(name):
        return os.environ[name]
    hermes_env = Path.home() / "AppData" / "Local" / "hermes" / ".env"
    if not hermes_env.exists():
        return ""
    try:
        for line in hermes_env.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""
