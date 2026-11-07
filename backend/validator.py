"""Проверка ответа ИИ ПОСЛЕ вызова. Ошибки возвращаются списком строк и уходят модели на повтор."""
from __future__ import annotations

from .models import Checklist, Criterion, LLMOutput

_YES = {"yes", "да", "true", "y", "иә"}
_NO = {"no", "нет", "false", "n", "жоқ"}
_NA = {"na", "n/a", "н/п", "нп"}


def normalize(c: Criterion, raw):
    """Приводит ответ к виду "yes"/"no"/"na"/число. ValueError, если недопустим."""
    s = str(raw).strip().lower()
    if s in _NA:
        if not c.na_allowed:
            raise ValueError("N/A запрещён для этого критерия")
        return "na"
    if c.type in ("yes_no", "flag"):
        if s in _YES:
            return "yes"
        if s in _NO:
            return "no"
        raise ValueError(f"ответ «{raw}» не yes/no")
    try:
        v = float(s.replace(",", "."))
    except ValueError:
        raise ValueError(f"ответ «{raw}» не число")
    if v not in [float(x) for x in c.scale]:
        raise ValueError(f"значение {raw} не из шкалы {c.scale}")
    return v


def validate(checklist: Checklist, out: LLMOutput, msgs: list[dict],
             only_ids: list[str] | None = None) -> tuple[dict, list[str]]:
    errors, norm = [], {}
    expected = [c.id for c in checklist.criteria if only_ids is None or c.id in only_ids]
    n = len(msgs)

    got = [a.criterion_id for a in out.answers]
    for cid in expected:
        if cid not in got:
            errors.append(f"{cid}: нет ответа")
    # Лишние критерии в ответе молча игнорируем: повтор вызова стоит денег, а вреда от них нет.

    for a in out.answers:
        if a.criterion_id not in expected:
            continue
        c = checklist.criterion(a.criterion_id)
        tag = c.id
        try:
            ans = normalize(c, a.answer)
        except ValueError as e:
            errors.append(f"{tag}: {e}")
            continue

        bad = [i for i in a.evidence_msg_ids if not (0 <= i < n)]
        if bad:
            errors.append(f"{tag}: реплик с номерами {bad} нет (допустимо 0..{n - 1})")
        elif c.evidence_roles:
            wrong = [i for i in a.evidence_msg_ids if msgs[i]["speaker"] not in c.evidence_roles]
            if wrong:
                errors.append(f"{tag}: реплики {wrong} не от {c.evidence_roles}")

        r = a.checked_range
        if r is not None and not (len(r) == 2 and 0 <= r[0] <= r[1] < n):
            errors.append(f"{tag}: checked_range {r} вне диапазона 0..{n - 1}")

        if ans == "na":
            if not a.na_reason:
                errors.append(f"{tag}: для N/A нужен na_reason")
        elif not (c.type == "flag" and ans == "no"):
            if not a.evidence_msg_ids and not a.checked_range:
                errors.append(f"{tag}: нужны evidence_msg_ids или checked_range")

        norm[c.id] = {"answer": ans, "evidence_msg_ids": a.evidence_msg_ids, "checked_range": a.checked_range,
                      "na_reason": a.na_reason, "explanation": a.explanation,
                      "confidence": max(0.0, min(1.0, a.confidence))}
    return norm, errors
