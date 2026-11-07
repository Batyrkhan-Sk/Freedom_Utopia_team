"""Промпт собирается из JSON чек-листа. Ни одного критерия в коде: замена критерия в UI работает сразу."""
from __future__ import annotations

from .facts import fmt_t
from .models import Checklist, Criterion

TYPE_LABEL = {"yes_no": "да/нет", "scale": "шкала", "flag": "флаг (yes = ошибка есть)"}

SYSTEM_HEAD = (
    "Ты - эксперт по контролю качества службы поддержки. Заполни чек-лист по диалогу.\n"
    "Оцениваешь ТОЛЬКО оператора (speaker=operator). Реплики bot и system - контекст.\n"
    "Диалог может быть на русском, казахском или английском.\n"
    "Ссылаться на доказательства можно только по номерам реплик [N] из диалога. Не выдумывай номера.\n"
    "Блок ФАКТЫ посчитан системой и достоверен, не пересчитывай его.\n"
    "Баллы и итог не считай: верни только ответы по критериям.\n"
)

OUTPUT_SPEC = (
    "ФОРМАТ ОТВЕТА - только JSON, без markdown и пояснений вокруг:\n"
    '{"answers": [{"criterion_id": "...", "explanation": "...", "evidence_msg_ids": [0], '
    '"checked_range": null, "na_reason": null, "answer": "...", "confidence": 0.0}]}\n'
    "Порядок полей в каждом элементе именно такой: сначала explanation и доказательства, потом answer.\n"
    "explanation - 1-2 предложения. confidence - от 0 до 1.\n"
    "Если действие пропущено (ответ no или низкая ступень шкалы) - заполни checked_range: [первая, последняя] "
    "проверенные реплики. Если ответ na - заполни na_reason. Если ответ yes - дай evidence_msg_ids.\n"
    "Нужен ровно один элемент на каждый критерий из списка."
)


def allowed_values(c: Criterion) -> list[str]:
    if c.type in ("yes_no", "flag"):
        base = ["yes", "no"]
    else:
        base = [str(int(v)) if float(v).is_integer() else str(v) for v in c.scale]
    return base + (["na"] if c.na_allowed else [])


def criterion_block(c: Criterion) -> str:
    return (
        f"### {c.id} - {c.name}\n"
        f"Тип: {TYPE_LABEL[c.type]}. Допустимые answer: {', '.join(allowed_values(c))}.\n"
        f"N/A: {'разрешён' if c.na_allowed else 'не разрешён'}.\n"
        f"Инструкция: {c.instruction}\n"
    )


def facts_to_text(f: dict) -> str:
    ev = ", ".join(f"[{e['idx']}] {e['type']}" for e in f["events"]) or "нет"
    return (
        f"- Реплик в диалоге: {f['n_messages']}\n"
        f"- Первая реплика оператора: {f['first_operator_idx']}\n"
        f"- Время первой реакции оператора, сек: {f['first_reaction_sec']} "
        f"(от: {f['reaction_ref']}); порог превышен: {f['reaction_exceeded_threshold']}\n"
        f"- Личность подтверждена системным сообщением: {'да' if f['identity_confirmed_by_system'] else 'нет'}\n"
        f"- Системные события: {ev}"
    )


def transcript_to_text(msgs: list[dict]) -> str:
    return "\n".join(f"[{m['idx']}] {fmt_t(m['t'])} {m['speaker']}: {m['text']}" for m in msgs)


def build_messages(checklist: Checklist, conv: dict, msgs: list[dict], facts: dict,
                   only_ids: list[str] | None = None) -> list[dict]:
    crits = [c for c in checklist.criteria if only_ids is None or c.id in only_ids]
    system = (
        SYSTEM_HEAD
        + "\nПРАВИЛА ТОЛКОВАНИЯ:\n" + checklist.rules
        + "\n\nКРИТЕРИИ:\n" + "\n".join(criterion_block(c) for c in crits)
        + "\n" + OUTPUT_SPEC
    )
    user = (
        f"Канал: {conv.get('channel')}, язык: {conv.get('language')}\n\n"
        f"ФАКТЫ:\n{facts_to_text(facts)}\n\n"
        f"ДИАЛОГ:\n{transcript_to_text(msgs)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
