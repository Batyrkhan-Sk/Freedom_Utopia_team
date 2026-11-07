"""Решение проверяющего: заменяем ответ по критерию и пересчитываем итог через тот же scoring."""
from __future__ import annotations

import copy
from datetime import datetime, timezone

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


def apply_to_evaluation(evaluation: dict, checklist: Checklist, decision: dict, appeal_id: int) -> dict:
    """Записывает решение в саму оценку: новый ответ, пересчитанные баллы, история апелляций.
    Первый исходный ответ ИИ сохраняется в original_answer."""
    ev = copy.deepcopy(evaluation)
    cid = decision["criterion_id"]
    for x in ev["criteria"]:
        if x["criterion_id"] == cid:
            x.setdefault("original_answer", x["answer"])
            x.setdefault("ai_explanation", x.get("explanation", ""))
            x["answer"] = decision["new_answer"]
            if decision["new_answer"] != x["original_answer"]:   # объяснение ИИ противоречило бы новому ответу
                x["explanation"] = f"Решение проверяющего: {decision['reason']}"
    sc = score(checklist, {x["criterion_id"]: x["answer"] for x in ev["criteria"]})
    for x in ev["criteria"]:
        p = sc["criteria"][x["criterion_id"]]
        x["points"], x["weight"], x["effective_weight"] = p["points"], p["weight"], p["effective_weight"]
    ev["sections"], ev["total"], ev["critical_error"] = sc["sections"], sc["total"], sc["critical_error"]
    ev.setdefault("appeal_history", []).append({
        "appeal_id": appeal_id, "criterion_id": cid, "old": decision["original_answer"],
        "new": decision["new_answer"], "reason": decision["reason"],
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return ev
