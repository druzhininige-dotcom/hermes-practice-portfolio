"""LLM-клиент: OpenAI-совместимый chat/completions (DeepSeek, OpenRouter, OpenAI).

Считает токены и деньги, соблюдает дневной бюджет, делает 2 ретрая на 429/5xx.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass

import httpx

from app import db
from app.config import Settings
from app.schemas import LLMAnswer, ParseError, parse_answer

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


class LLMClient:
    def __init__(self, settings: Settings, conn: sqlite3.Connection | None = None,
                 client: httpx.AsyncClient | None = None):
        self.s = settings
        self.conn = conn
        self._client = client
        self._own_client = client is None
        self.semaphore = asyncio.Semaphore(max(1, settings.llm_concurrency))

    async def __aenter__(self) -> "LLMClient":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.s.llm_timeout_s)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._own_client and self._client is not None:
            await self._client.aclose()

    # ── бюджет ──────────────────────────────────────────────────────────
    def budget_left(self) -> float:
        if self.conn is None:
            return self.s.llm_daily_budget_usd
        return self.s.llm_daily_budget_usd - db.cost_today(self.conn)

    # ── основной вызов ──────────────────────────────────────────────────
    async def classify(self, system_prompt: str, user_prompt: str, expected_slugs: list[str],
                       *, chat_id: int | None = None, message_id: int | None = None,
                       max_retries: int = 2) -> tuple[LLMAnswer, Usage]:
        if not self.s.llm_api_key:
            raise LLMError("нет LLM_API_KEY (задайте в .env)")
        if self.budget_left() <= 0:
            raise BudgetExceeded(f"дневной бюджет ${self.s.llm_daily_budget_usd} исчерпан")

        body = {
            "model": self.s.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 900,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.s.llm_api_key}",
                   "Content-Type": "application/json"}

        last_err: Exception | None = None
        for attempt in range(max_retries + 1):
            t0 = time.perf_counter()
            try:
                async with self.semaphore:
                    assert self._client is not None
                    resp = await self._client.post(
                        f"{self.s.llm_base_url.rstrip('/')}/chat/completions",
                        json=body, headers=headers)
                latency = int((time.perf_counter() - t0) * 1000)

                if resp.status_code in (429, 500, 502, 503, 504):
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                if resp.status_code != 200:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")

                data = resp.json()
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                usage_raw = data.get("usage") or {}
                usage = Usage(
                    prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                    completion_tokens=int(usage_raw.get("completion_tokens") or 0),
                    latency_ms=latency,
                )
                usage.cost_usd = self._cost(usage)
                answer = parse_answer(content, expected_slugs)
                self._log_call(usage, ok=True, chat_id=chat_id, message_id=message_id)
                return answer, usage

            except (httpx.HTTPError, LLMError, ParseError, KeyError, ValueError) as exc:
                last_err = exc
                latency = int((time.perf_counter() - t0) * 1000)
                self._log_call(Usage(latency_ms=latency), ok=False, error=str(exc)[:400],
                               chat_id=chat_id, message_id=message_id)
                if attempt < max_retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                break
        raise LLMError(f"не удалось получить валидный ответ: {last_err}")

    def _cost(self, usage: Usage) -> float:
        return (
            usage.prompt_tokens / 1_000_000 * self.s.llm_price_in_per_1m
            + usage.completion_tokens / 1_000_000 * self.s.llm_price_out_per_1m
        )

    def _log_call(self, usage: Usage, *, ok: bool, error: str | None = None,
                  chat_id: int | None = None, message_id: int | None = None) -> None:
        if self.conn is None:
            return
        try:
            db.log_llm_call(
                self.conn, model=self.s.llm_model,
                prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
                cost_usd=usage.cost_usd, latency_ms=usage.latency_ms, ok=ok, error=error,
                chat_id=chat_id, message_id=message_id,
            )
        except sqlite3.Error as exc:  # учёт не должен ронять обработку
            log.warning("не смог записать llm_calls: %s", exc)
