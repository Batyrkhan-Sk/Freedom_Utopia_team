"""Единственное место, где вызывается модель. Провайдер меняется строкой CQA_MODEL (через LiteLLM).

Устойчивость к лимитам бесплатных тарифов:
- ограничение частоты запросов (CQA_RPM), общее для всех потоков пакетной оценки;
- 429: ждём столько, сколько просит провайдер; 503/таймаут: 5 -> 15 -> 45 сек;
- после двух неудач подряд - следующая модель из CQA_FALLBACK_MODELS.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .config import settings


@dataclass
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    model: str


# Контракт для подмены в тестах: llm(messages, temperature) -> LLMResponse
LLMFn = Callable[..., LLMResponse]

_lock = threading.Lock()
_last_call = [0.0]

TRANSIENT = ("RateLimit", "ServiceUnavailable", "Timeout", "InternalServer", "APIConnection", "BadGateway")
SWITCH_MODEL = ("PaymentRequired", "BudgetExceeded", "NotFound")   # эта модель недоступна -> следующая
_RETRY_IN = re.compile(r"(?:retry|try again)\s+in\s+([0-9.]+)\s*s", re.I)
_PER_MINUTE = re.compile(r"per minute|\((?:TPM|RPM|OTPM|ITPM)\)", re.I)
_HOPELESS = re.compile(r"per day|\((?:TPD|RPD)\)|request too large", re.I)


def _throttle() -> None:
    if settings.RPM <= 0:
        return
    interval = 60.0 / settings.RPM
    with _lock:
        wait = interval - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def _cost(resp, tin: int, tout: int, model: str = "") -> float:
    if model and model == settings.VERIFY_MODEL and (settings.VERIFY_PRICE_IN_PER_M or settings.VERIFY_PRICE_OUT_PER_M):
        return tin / 1e6 * settings.VERIFY_PRICE_IN_PER_M + tout / 1e6 * settings.VERIFY_PRICE_OUT_PER_M
    if settings.PRICE_IN_PER_M or settings.PRICE_OUT_PER_M:
        return tin / 1e6 * settings.PRICE_IN_PER_M + tout / 1e6 * settings.PRICE_OUT_PER_M
    try:
        import litellm
        return float(litellm.completion_cost(completion_response=resp))
    except Exception:
        return 0.0


def _supports_temperature(model: str) -> bool:
    # Текущие модели Claude отклоняют temperature/top_p/top_k (ошибка 400)
    return not (model.startswith("anthropic/") or "claude" in model)


def _call(model: str, messages: list[dict], temperature: float):
    import litellm  # импорт здесь, чтобы тесты шли без litellm
    litellm.suppress_debug_info = True

    local = model.startswith(("ollama/", "ollama_chat/"))
    kwargs = dict(model=model, messages=messages, max_tokens=settings.MAX_TOKENS,
                  timeout=settings.LOCAL_TIMEOUT if local else settings.LLM_TIMEOUT)
    if local:
        # У Ollama окно по умолчанию ~4K токенов: промпт (~5K) обрезался бы молча, вместе с инструкциями
        kwargs["num_ctx"] = settings.NUM_CTX
        if not settings.LOCAL_THINK:
            kwargs["think"] = False   # рассуждения на ноутбуке удлиняют ответ в 3-4 раза
    if _supports_temperature(model):
        kwargs["temperature"] = temperature
    if settings.JSON_MODE:
        kwargs["response_format"] = {"type": "json_object"}
    if settings.REASONING_EFFORT:
        kwargs["reasoning_effort"] = settings.REASONING_EFFORT
    return litellm.completion(**kwargs)


def complete(messages: list[dict], temperature: float = 0.0, models: list[str] | None = None) -> LLMResponse:
    """models: явный список моделей (например, только проверяющая модель); по умолчанию CQA_MODEL + резерв."""
    models = models or ([settings.MODEL] + [m for m in settings.FALLBACK_MODELS if m != settings.MODEL])
    mi, fails_on_model, last_error = 0, 0, None
    for attempt in range(settings.LLM_RETRIES + 1):
        model = models[mi]
        _throttle()
        try:
            resp = _call(model, messages, temperature)
            u = resp.usage
            tin, tout = int(u.prompt_tokens), int(u.completion_tokens)
            return LLMResponse(resp.choices[0].message.content or "", tin, tout, _cost(resp, tin, tout, model), model)
        except Exception as e:  # noqa: BLE001 - классифицируем ниже по имени класса LiteLLM
            last_error, name = e, type(e).__name__
            if any(x in name for x in SWITCH_MODEL):
                if mi + 1 >= len(models):
                    raise
                mi, fails_on_model = mi + 1, 0
                continue
            msg = str(e)
            if _HOPELESS.search(msg):   # дневной лимит или запрос больше лимита модели: ждать бесполезно
                if mi + 1 >= len(models):
                    raise
                mi, fails_on_model = mi + 1, 0
                continue
            if not any(x in name for x in TRANSIENT) or attempt == settings.LLM_RETRIES:
                raise
            m = _RETRY_IN.search(msg)
            delay = float(m.group(1)) + 1 if m else min(5 * 3 ** fails_on_model, 60)
            # Минутный лимит с понятной паузой - ждём ту же модель, а не уходим на более слабую резервную
            if not (m and delay <= 90 and _PER_MINUTE.search(msg)):
                fails_on_model += 1
                if fails_on_model >= 2 and mi + 1 < len(models):
                    mi, fails_on_model = mi + 1, 0
            time.sleep(min(delay, 90))
    raise last_error  # pragma: no cover
