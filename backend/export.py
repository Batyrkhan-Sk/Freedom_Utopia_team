"""Файл сдачи results.json (формат JSON 1.0 из CQA_Student_Guide_Appendix_Results.pdf) и его проверка.

Отличия от внутреннего формата оценки:
- код критерия из поля `code` чек-листа (contact, discovery, ...), а не наш id;
- "yes"/"no"/"na" -> true/false/null, шкала -> число до учёта веса;
- evidence_turns нумеруются с 1 по исходному массиву транскрипта (у нас с 0);
- неудачный диалог не пропускается: status="error", criteria=[], score=null.
"""
from __future__ import annotations

from .models import Checklist
from .refs import REVIEWER_PREFIX, human_refs

SCHEMA_VERSION = "1.0"
LOCAL_PREFIXES = ("ollama/", "ollama_chat/", "lm_studio/", "local/")

# Допустимые значения из таблицы «Правила заполнения» (стр. 3)
OFFICIAL_VALUES: dict[str, set] = {
    "contact": {True, False},
    "discovery": {True, False, None},
    "identification": {True, False, None},
    "consultation": {0, 5, 10, 15, 20, None},
    "empathy": {True, False},
    "dialogue": {0, 4, 7, 10},
    "closure": {True, False, None},
    "critical": {True, False},
}


def _allowed(v, allowed: set) -> bool:
    """В Python True == 1 и False == 0, поэтому тип проверяем отдельно: true не должно пройти как шкала 1."""
    if v is None:
        return None in allowed
    if isinstance(v, bool):
        return v in {a for a in allowed if isinstance(a, bool)}
    if isinstance(v, (int, float)):
        return v in {a for a in allowed if a is not None and not isinstance(a, bool)}
    return False


def model_info(models: list[str], mode: str = "") -> dict:
    names = sorted({m for m in models if m})
    if not mode:
        local = [m.startswith(LOCAL_PREFIXES) for m in names]
        mode = "local" if local and all(local) else "hybrid" if any(local) else "external_api"
    return {"mode": mode, "name": ", ".join(names) or "unknown"}


def _value(answer):
    if answer == "yes":
        return True
    if answer == "no":
        return False
    if answer == "na":
        return None
    return int(answer) if float(answer).is_integer() else float(answer)


def _turns(x: dict) -> list[int]:
    """Реплики-доказательства с 1. Для «Нет» (действия не было) модель указывает проверенный участок
    checked_range=[от, до] вместо реплик - тогда доказательство = этот участок."""
    ids = x.get("evidence_msg_ids") or []
    rng = x.get("checked_range")
    if not ids and rng and len(rng) == 2 and rng[0] <= rng[1]:
        ids = range(rng[0], rng[1] + 1)
    return sorted({i + 1 for i in ids})


def _rationale(x: dict, n_msgs: int | None) -> str:
    """Объяснение ИИ с номерами реплик с 1 - как в evidence_turns (текст проверяющего не трогаем)."""
    text = x.get("explanation") or ""
    if n_msgs and not text.startswith(REVIEWER_PREFIX):
        text = human_refs(text, n_msgs)
    return text


def to_result(ev: dict, checklist: Checklist, n_msgs: int | None = None) -> dict:
    """Одна успешная оценка (результат evaluator.evaluate) -> запись results[]."""
    by_id = {c["criterion_id"]: c for c in ev["criteria"]}
    criteria = []
    for c in checklist.criteria:
        x = by_id[c.id]
        criteria.append({
            "criterion_id": c.code or c.id,
            "value": _value(x["answer"]),
            "rationale": _rationale(x, n_msgs),
            "evidence_turns": _turns(x),
        })
    return {
        "conversation_id": ev["conversation_id"], "status": "ok", "criteria": criteria,
        "score": {"final_score": ev["total"], "critical_flag": bool(ev["critical_error"])},
    }


def error_result(conversation_id: str, error: str) -> dict:
    return {"conversation_id": conversation_id, "status": "error", "criteria": [], "score": None,
            "error": error[:500]}


def build_document(results: list[dict], models: list[str], team_name: str, mode: str = "") -> dict:
    return {"schema_version": SCHEMA_VERSION, "team_name": team_name,
            "model": model_info(models, mode), "results": results}


def validate_document(doc: dict, expected_ids: list[str] | None = None,
                      transcript_len: dict[str, int] | None = None, strict_codes: bool = True) -> list[str]:
    """Все правила приложения. Пустой список = файл можно сдавать.
    strict_codes=False - чек-лист изменён (заменён критерий): не требуем ровно 8 официальных кодов."""
    err: list[str] = []
    if doc.get("schema_version") != SCHEMA_VERSION:
        err.append(f"schema_version должен быть {SCHEMA_VERSION!r}")
    if not doc.get("team_name"):
        err.append("пустой team_name")
    m = doc.get("model") or {}
    if m.get("mode") not in ("local", "external_api", "hybrid") or not m.get("name"):
        err.append("model: нужен mode (local/external_api/hybrid) и name")
    results = doc.get("results")
    if not isinstance(results, list):
        return err + ["results должен быть массивом"]

    ids = [r.get("conversation_id") for r in results]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        err.append(f"повторяющиеся conversation_id: {sorted(dup)}")
    if expected_ids is not None:
        missing, extra = set(expected_ids) - set(ids), set(ids) - set(expected_ids)
        if missing:
            err.append(f"нет записей для {len(missing)} диалогов: {sorted(missing)[:10]}")
        if extra:
            err.append(f"лишние conversation_id (нет в API): {sorted(extra)[:10]}")

    for r in results:
        cid = r.get("conversation_id")
        if r.get("status") == "error":
            if r.get("criteria") != [] or r.get("score") is not None or not r.get("error"):
                err.append(f"{cid}: для status=error нужны criteria=[], score=null и текст error")
            continue
        if r.get("status") != "ok":
            err.append(f"{cid}: status должен быть ok или error")
            continue
        crit = r.get("criteria") or []
        codes = [c.get("criterion_id") for c in crit]
        if len(codes) != len(set(codes)):
            err.append(f"{cid}: критерий повторяется")
        if strict_codes and set(codes) != set(OFFICIAL_VALUES):
            err.append(f"{cid}: нужны ровно 8 критериев {sorted(OFFICIAL_VALUES)}, есть {codes}")
        for c in crit:
            code, v = c.get("criterion_id"), c.get("value")
            allowed = OFFICIAL_VALUES.get(code)
            if allowed is not None and not _allowed(v, allowed):
                err.append(f"{cid}/{code}: недопустимое value {v!r}")
            if not isinstance(c.get("rationale"), str):
                err.append(f"{cid}/{code}: rationale должен быть строкой")
            turns = c.get("evidence_turns")
            if not isinstance(turns, list) or not all(isinstance(t, int) and not isinstance(t, bool) for t in turns):
                err.append(f"{cid}/{code}: evidence_turns должен быть списком целых")
            elif turns and (min(turns) < 1 or (transcript_len and cid in transcript_len
                                               and max(turns) > transcript_len[cid])):
                err.append(f"{cid}/{code}: evidence_turns {turns} вне 1..длина транскрипта")
        sc = r.get("score") or {}
        fs = sc.get("final_score")
        if not isinstance(fs, (int, float)) or isinstance(fs, bool) or not 0 <= fs <= 100 or round(fs, 2) != fs:
            err.append(f"{cid}: final_score должен быть числом 0..100 с 2 знаками")
        crit_v = next((c.get("value") for c in crit if c.get("criterion_id") == "critical"), None)
        if strict_codes and sc.get("critical_flag") is not crit_v:
            err.append(f"{cid}: critical_flag должен совпадать с value критерия critical")
    return err
