"""Оркестрация: факты -> промпт -> ИИ -> проверка (с повтором) -> голосование -> scoring."""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter

from .config import settings
from .facts import build_messages as number_messages
from .facts import compute_facts
from .llm import LLMFn, complete
from .models import Checklist, LLMOutput
from .prompt import build_messages as build_prompt
from .scoring import score
from .validator import validate


class EvaluationError(Exception):
    pass


class Usage:
    def __init__(self):
        self.tin = self.tout = self.calls = 0
        self.cost = 0.0
        self.model = ""

    def add(self, r):
        self.tin += r.tokens_in
        self.tout += r.tokens_out
        self.cost += r.cost_usd
        self.calls += 1
        self.model = r.model


def parse_output(text: str) -> LLMOutput:
    text = re.sub(r"```(?:json)?", "", text).strip()
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < 0:
        raise ValueError("в ответе нет JSON")
    return LLMOutput.model_validate(json.loads(text[a:b + 1]))


def _ask(llm: LLMFn, checklist, conv, msgs, facts, only_ids, usage: Usage, temperature=0.0) -> dict:
    """Один «логический» вызов: при невалидном ответе показываем модели ошибки и просим исправить."""
    messages = build_prompt(checklist, conv, msgs, facts, only_ids)
    errors: list[str] = []
    for _ in range(settings.MAX_RETRIES + 1):
        resp = llm(messages, temperature=temperature)
        usage.add(resp)
        try:
            norm, errors = validate(checklist, parse_output(resp.text), msgs, only_ids)
        except ValueError as e:  # JSONDecodeError и pydantic ValidationError - наследники ValueError
            norm, errors = {}, [f"Невалидный JSON или схема: {e}"]
        if not errors:
            return norm
        messages = messages + [
            {"role": "assistant", "content": resp.text},
            {"role": "user", "content": "Исправь ошибки и верни полный JSON заново:\n- " + "\n- ".join(errors)},
        ]
    raise EvaluationError("; ".join(errors))


def _merge_votes(c, votes: list[dict]) -> dict:
    answers = [v["answer"] for v in votes]
    if c.type == "scale" and all(a != "na" for a in answers):
        final = statistics.median_low(answers)        # реальное значение шкалы, не среднее
    else:
        final = Counter(answers).most_common(1)[0][0]
    agree = answers.count(final) / len(answers)
    rep = next(v for v in votes if v["answer"] == final)
    return {**rep, "confidence": round(agree * rep["confidence"], 2)}


REUSED_FIELDS = ("answer", "evidence_msg_ids", "checked_range", "na_reason", "explanation", "confidence")


def evaluate(conv: dict, checklist: Checklist, llm: LLMFn = complete, reuse: dict | None = None) -> dict:
    """reuse: {criterion_id: запись критерия из прошлой оценки} - эти критерии не переспрашиваем у ИИ
    (переоценка после правки чек-листа: ИИ спрашиваем только об изменённых и новых критериях)."""
    msgs = number_messages(conv["transcript"])
    facts = compute_facts(msgs, conv.get("channel", ""))
    usage = Usage()
    reuse = {cid: v for cid, v in (reuse or {}).items() if any(c.id == cid for c in checklist.criteria)}
    ask_ids = [c.id for c in checklist.criteria if c.id not in reuse]

    norm: dict = {}
    if ask_ids:
        norm = _ask(llm, checklist, conv, msgs, facts, None if not reuse else ask_ids, usage)
    for cid, prev in reuse.items():
        norm[cid] = {k: prev.get(k) for k in REUSED_FIELDS}

    # Повторный опрос только для критериев с resample > 1 (шкальные, где модель чаще ошибается)
    for c in checklist.criteria:
        n = settings.resample_for(c)   # CQA_RESAMPLE=1 выключает повторный опрос
        if c.id in ask_ids and n > 1 and norm[c.id]["answer"] != "na":
            votes = [norm[c.id]]
            for _ in range(n - 1):
                votes.append(_ask(llm, checklist, conv, msgs, facts, [c.id], usage,
                                  temperature=settings.VOTE_TEMPERATURE)[c.id])
            norm[c.id] = _merge_votes(c, votes)

    sc = score(checklist, {cid: v["answer"] for cid, v in norm.items()})
    criteria = []
    for c in checklist.criteria:
        v, p = norm[c.id], sc["criteria"][c.id]
        criteria.append({
            "criterion_id": c.id, "name": c.name, "answer": v["answer"],
            "points": p["points"], "weight": p["weight"], "effective_weight": p["effective_weight"],
            "evidence_msg_ids": v["evidence_msg_ids"], "checked_range": v["checked_range"],
            "na_reason": v["na_reason"], "explanation": v["explanation"],
            "confidence": v["confidence"], "needs_review": v["confidence"] < settings.LOW_CONFIDENCE,
            "reused": c.id in reuse,
        })
    return {
        "conversation_id": conv["conversation_id"],
        "checklist_id": checklist.checklist_id, "checklist_version": checklist.version,
        "model": usage.model, "llm_calls": usage.calls,
        "tokens": {"in": usage.tin, "out": usage.tout}, "cost_usd": round(usage.cost, 6),
        "facts": facts, "criteria": criteria, "sections": sc["sections"],
        "total": sc["total"], "critical_error": sc["critical_error"],
        "needs_review": any(x["needs_review"] for x in criteria),
        "asked_criteria": ask_ids,
    }
