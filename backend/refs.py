"""Номера реплик в текстах ИИ - для людей.

ИИ видит реплики пронумерованными с 0 («[9] operator: ...») и так же ссылается на них в объяснениях.
Интерфейс и файл сдачи нумеруют с 1 (#10). Чтобы «в [62]» в тексте совпадало с ссылкой #63,
при показе сдвигаем номера в квадратных скобках на +1. Хранимые оценки не меняются.
"""
from __future__ import annotations

import copy
import re

_REF = re.compile(r"\[(\d+(?:\s*(?:,|-|–|—|\.\.)\s*\d+)*)\]")
_NUM = re.compile(r"\d+")
REVIEWER_PREFIX = "Решение проверяющего:"


def human_refs(text: str | None, n_msgs: int) -> str | None:
    """«[62] и [68]», «[10–12]», «[9, 11]» -> номера с 1. Числа вне диапазона реплик не трогаем."""
    if not text:
        return text

    def bump(m: re.Match) -> str:
        nums = [int(x) for x in _NUM.findall(m.group(1))]
        if any(x >= n_msgs for x in nums):   # не похоже на номер реплики - оставляем как есть
            return m.group(0)
        return "[" + _NUM.sub(lambda d: str(int(d.group(0)) + 1), m.group(1)) + "]"

    return _REF.sub(bump, text)


def humanize_evaluation(ev: dict | None, n_msgs: int) -> dict | None:
    """Копия оценки с номерами реплик с 1 в объяснениях ИИ. Текст проверяющего не трогаем."""
    if not ev:
        return ev
    out = copy.deepcopy(ev)
    for c in out.get("criteria", []):
        if not str(c.get("explanation") or "").startswith(REVIEWER_PREFIX):
            c["explanation"] = human_refs(c.get("explanation"), n_msgs)
        for k in ("na_reason", "ai_explanation"):
            c[k] = human_refs(c.get(k), n_msgs)
        so = c.get("second_opinion")
        if isinstance(so, dict):
            for k in ("first_explanation", "second_explanation"):
                if k in so:
                    so[k] = human_refs(so[k], n_msgs)
    return out
