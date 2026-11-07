"""Переоценка после правки чек-листа: ИИ спрашиваем только о новых и изменённых критериях,
остальные ответы берём из прошлой оценки и просто пересчитываем итог."""
from __future__ import annotations

from .checklists import diff_checklists
from .evaluator import evaluate
from .llm import LLMFn, complete
from .models import Checklist


def reevaluate_conversation(conv: dict, prev_eval: dict | None, old_cl: Checklist | None, new_cl: Checklist,
                            llm: LLMFn = complete) -> tuple[dict, dict]:
    """Возвращает (новая оценка, строка сравнения для интерфейса)."""
    reuse: dict = {}
    if prev_eval and old_cl:
        d = diff_checklists(old_cl, new_cl)
        if not d["rules_changed"]:
            ask = set(d["added"]) | set(d["changed"])
            prev = {c["criterion_id"]: c for c in prev_eval["criteria"]}
            reuse = {c.id: prev[c.id] for c in new_cl.criteria if c.id not in ask and c.id in prev}
    ev = evaluate(conv, new_cl, llm=llm, reuse=reuse)
    if ev["llm_calls"] == 0 and prev_eval:   # ИИ не спрашивали: модель ответов - прежняя
        ev["model"] = prev_eval.get("model", "")
    if prev_eval:
        # Решения по апелляциям не теряются: у переиспользованных критериев остаются исходный ответ ИИ
        # и его объяснение, история апелляций переносится в новую оценку
        prev_by = {c["criterion_id"]: c for c in prev_eval["criteria"]}
        for c in ev["criteria"]:
            p = prev_by.get(c["criterion_id"])
            if c.get("reused") and p:
                for k in ("original_answer", "ai_explanation"):
                    if k in p:
                        c[k] = p[k]
        if prev_eval.get("appeal_history"):
            ev["appeal_history"] = list(prev_eval["appeal_history"])

    old_answers = {c["criterion_id"]: c["answer"] for c in (prev_eval or {}).get("criteria", [])}
    changed_answers = [{"criterion_id": c["criterion_id"], "old": old_answers.get(c["criterion_id"]), "new": c["answer"]}
                       for c in ev["criteria"] if old_answers.get(c["criterion_id"]) != c["answer"]]
    row = {
        "conversation_id": conv["conversation_id"],
        "old_total": (prev_eval or {}).get("total"), "new_total": ev["total"],
        "old_critical": (prev_eval or {}).get("critical_error"), "new_critical": ev["critical_error"],
        "asked_criteria": ev["asked_criteria"], "changed_answers": changed_answers,
        "evaluation_id": None,   # заполняется после сохранения
    }
    return ev, row
