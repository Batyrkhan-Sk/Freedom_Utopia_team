"""Проверка чек-листа перед сохранением и сравнение версий (что переспрашивать у ИИ при переоценке)."""
from __future__ import annotations

from .models import Checklist

# Поля критерия, которые попадают в промпт: их изменение требует нового ответа ИИ
PROMPT_FIELDS = ("name", "type", "na_allowed", "scale", "scale_descriptions", "instruction", "evidence_roles")
# Поля, которые влияют только на расчёт или выгрузку: ИИ не нужен, только пересчёт
SCORE_FIELDS = ("weight", "code", "section", "resample")


def check_checklist(cl: Checklist) -> list[str]:
    """Пустой список = чек-лист можно сохранять."""
    err: list[str] = []
    ids = [c.id.strip() for c in cl.criteria]
    if any(not i for i in ids):
        err.append("У критерия пустой id")
    dup = sorted({i for i in ids if i and ids.count(i) > 1})
    if dup:
        err.append(f"Повторяющиеся id критериев: {dup}")
    codes = [c.code for c in cl.criteria if c.code is not None]
    if any(not str(x).strip() for x in codes):
        err.append("Пустой code у критерия")
    dup_codes = sorted({x for x in codes if x and codes.count(x) > 1})
    if dup_codes:
        err.append(f"Повторяющиеся code: {dup_codes}")

    scored = [c for c in cl.criteria if c.type != "flag"]
    wsum = sum(c.weight for c in scored)
    if abs(wsum - 100) > 0.01:
        err.append(f"Сумма весов критериев должна быть 100, сейчас {round(wsum, 2)}")
    section_ids = {s.id for s in cl.sections}
    for c in cl.criteria:
        if c.weight < 0:
            err.append(f"{c.id}: вес не может быть отрицательным")
        if c.type == "flag" and c.weight != 0:
            err.append(f"{c.id}: у флага (грубые ошибки) вес должен быть 0")
        if c.type == "scale":
            vals = {float(x) for x in (c.scale or [])}
            if len(vals) < 2:
                err.append(f"{c.id}: у шкалы нужно минимум 2 разных значения")
        elif c.scale:
            err.append(f"{c.id}: шкала задаётся только для типа scale")
        if c.section and section_ids and c.section not in section_ids:
            err.append(f"{c.id}: секции «{c.section}» нет в чек-листе")
    return err


def diff_checklists(old: Checklist, new: Checklist) -> dict:
    """Что изменилось между версиями. changed/added -> спросить ИИ, weight_only -> только пересчитать."""
    old_by = {c.id: c for c in old.criteria}
    new_by = {c.id: c for c in new.criteria}
    added = [i for i in new_by if i not in old_by]
    removed = [i for i in old_by if i not in new_by]
    rules_changed = (old.rules or "").strip() != (new.rules or "").strip()
    changed, weight_only = [], []
    for i, c in new_by.items():
        if i not in old_by:
            continue
        o = old_by[i]
        if rules_changed or any(getattr(o, f) != getattr(c, f) for f in PROMPT_FIELDS):
            changed.append(i)
        elif any(getattr(o, f) != getattr(c, f) for f in SCORE_FIELDS):
            weight_only.append(i)
    return {"added": added, "removed": removed, "changed": changed,
            "weight_only": weight_only, "rules_changed": rules_changed}
