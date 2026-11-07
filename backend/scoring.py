"""Расчёт баллов. Чистая функция: никакой БД, сети и ИИ. Ничего не знает про конкретные критерии."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from .models import Checklist, Criterion


def round_half_up(x: float, nd: int = 2) -> float:
    # round(x, 9) убирает шум float: 20.5 / 80 * 100 = 25.624999999999996 -> 25.625 -> 25.63
    return float(Decimal(str(round(x, 9))).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP))


def raw_points(c: Criterion, answer) -> Optional[float]:
    """None = критерий не применим (N/A)."""
    if answer == "na":
        return None
    if c.type == "yes_no":
        return c.weight if answer == "yes" else 0.0
    if c.type == "scale":
        return float(answer) / max(c.scale) * c.weight
    return 0.0  # flag: в итог баллами не входит


def score(checklist: Checklist, answers: dict) -> dict:
    """answers: {criterion_id: "yes"|"no"|"na"|число}. Возвращает баллы, итог, секции, флаг грубой ошибки."""
    missing = [c.id for c in checklist.criteria if c.id not in answers]
    if missing:
        raise ValueError(f"Нет ответов по критериям: {missing}")
    nd = checklist.rounding

    scored = [c for c in checklist.criteria if c.type != "flag"]
    pts = {c.id: raw_points(c, answers[c.id]) for c in scored}
    applicable = [c for c in scored if pts[c.id] is not None]
    wsum = sum(c.weight for c in applicable)

    total = None
    if wsum > 0:
        total = round_half_up(sum(pts[c.id] for c in applicable) / wsum * 100, nd)

    per_criterion = {}
    for c in checklist.criteria:
        p = pts.get(c.id)
        eff = (c.weight / wsum * 100) if (p is not None and wsum > 0) else None
        per_criterion[c.id] = {
            "points": None if p is None else round_half_up(p, nd),
            "weight": c.weight,
            "effective_weight": None if eff is None else round_half_up(eff, nd),
        }

    sections = []
    for s in checklist.sections:
        members = [c for c in applicable if c.section == s.id]
        sections.append({
            "section_id": s.id,
            "points": round_half_up(sum(pts[c.id] for c in members), nd),
            "max": round_half_up(sum(c.weight for c in members), nd),
        })

    critical = any(c.type == "flag" and answers[c.id] == "yes" for c in checklist.criteria)
    return {"criteria": per_criterion, "sections": sections, "total": total, "critical_error": critical}
