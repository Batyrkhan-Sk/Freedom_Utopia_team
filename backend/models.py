"""Схемы данных. Чек-лист и ответ ИИ описаны здесь, остальной код работает от них."""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel


# ---------- Чек-лист (то, что редактируется в интерфейсе) ----------
class Criterion(BaseModel):
    id: str
    name: str
    type: Literal["yes_no", "scale", "flag"]
    weight: float = 0
    na_allowed: bool = False
    scale: Optional[list[float]] = None          # для type="scale", например [0, 4, 7, 10]
    scale_descriptions: Optional[dict[str, str]] = None   # {"20": "что означает ступень", ...}
    instruction: str = ""                        # инструкция для ИИ: когда да / нет / N/A
    section: Optional[str] = None                # id секции, если секции заданы
    evidence_roles: Optional[list[str]] = None   # кто может быть доказательством
    resample: int = 1                            # >1 -> повторный опрос и голосование


class Section(BaseModel):
    id: str
    name: str


class Checklist(BaseModel):
    checklist_id: str
    version: int = 1
    name: str = ""
    rounding: int = 2
    rules: str = ""                              # общие правила толкования -> идут в промпт
    sections: list[Section] = []
    criteria: list[Criterion]

    def criterion(self, cid: str) -> Criterion:
        for c in self.criteria:
            if c.id == cid:
                return c
        raise KeyError(cid)


# ---------- Ответ ИИ ----------
# Порядок полей важен: сначала объяснение и доказательства, потом ответ.
class LLMAnswer(BaseModel):
    criterion_id: str
    explanation: str = ""
    evidence_msg_ids: list[int] = []
    checked_range: Optional[list[int]] = None    # проверенный участок, если действие пропущено
    na_reason: Optional[str] = None
    answer: Union[int, float, str]
    confidence: float = 0.5


class LLMOutput(BaseModel):
    answers: list[LLMAnswer]
