"""Решение проверяющего: заменяем ответ по критерию и пересчитываем итог через тот же scoring."""
from __future__ import annotations

from .models import Checklist
from .scoring import score
from .validator import normalize


def apply_decision(evaluation: dict, checklist: Checklist, criterion_id: str, new_answer, reason: str) -> dict:
    c = checklist.criterion(criterion_id)
    new = normalize(c, new_answer)
    answers = {x["criterion_id"]: x["answer"] for x in evaluation["criteria"]}
    original = answers[criterion_id]
    answers[criterion_id] = new
    sc = score(checklist, answers)
    return {
        "criterion_id": criterion_id, "reason": reason,
        "original_answer": original, "new_answer": new, "changed": original != new,
        "original_total": evaluation["total"], "final_total": sc["total"],
        "final_criteria": sc["criteria"], "final_critical_error": sc["critical_error"],
    }
