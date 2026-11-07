"""Единственное место, где вызывается модель. Провайдер меняется строкой CQA_MODEL (через LiteLLM)."""
from __future__ import annotations

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


def _cost(resp, tin: int, tout: int) -> float:
    if settings.PRICE_IN_PER_M or settings.PRICE_OUT_PER_M:
        return tin / 1e6 * settings.PRICE_IN_PER_M + tout / 1e6 * settings.PRICE_OUT_PER_M
    try:
        import litellm
        return float(litellm.completion_cost(completion_response=resp))
    except Exception:
        return 0.0


def complete(messages: list[dict], temperature: float = 0.0) -> LLMResponse:
    import litellm  # импорт здесь, чтобы тесты шли без litellm

    kwargs = dict(model=settings.MODEL, messages=messages, temperature=temperature,
                  max_tokens=settings.MAX_TOKENS)
    if settings.JSON_MODE:
        kwargs["response_format"] = {"type": "json_object"}
    resp = litellm.completion(**kwargs)
    u = resp.usage
    tin, tout = int(u.prompt_tokens), int(u.completion_tokens)
    return LLMResponse(resp.choices[0].message.content or "", tin, tout, _cost(resp, tin, tout), settings.MODEL)
