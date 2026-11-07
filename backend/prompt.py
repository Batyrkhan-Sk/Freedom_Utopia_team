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
    "Блок ФАКТЫ посчитан системой по меткам времени и системным сообщениям. Это подсказки: "
    "время и события в нём точные, но решение по критерию принимай по самому диалогу.\n"
    "Отсутствие события в ФАКТАХ не означает нарушения: например, если подтверждения личности нет, "
    "сначала реши, требовалась ли идентификация вообще.\n"
    "Баллы и итог не считай: верни только ответы по критериям.\n"
)

CALL_NOTE = (
    "Это телефонный звонок: текст получен распознаванием речи, в нём есть ошибки и повторы. "
    "Не штрафуй оператора за ошибки распознавания и за скрытые данные ([Имя скрыто] и т.п.). "
    "В звонках идентификацию подтверждает сам оператор в речи (например, «идентификация пройдена»), "
    "системного сообщения об этом может не быть.\n"
)

END_LABEL = {
    "connection_lost": "соединение прервано",
    "client_left": "клиент покинул чат",
    "client_ended_call": "клиент завершил звонок",
    "chat_closed_by_operator": "оператор закрыл чат",
}

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
    steps = ""
    if c.type == "scale" and c.scale_descriptions:
        ordered = sorted(c.scale_descriptions.items(), key=lambda kv: -float(kv[0]))
        steps = "Ступени (выбери одну, подходящую первой сверху вниз):\n" + "".join(
            f"  {k}: {v}\n" for k, v in ordered)
    return (
        f"### {c.id} - {c.name}\n"
        f"Тип: {TYPE_LABEL[c.type]}. Допустимые answer: {', '.join(allowed_values(c))}.\n"
        f"N/A: {'разрешён' if c.na_allowed else 'не разрешён'}.\n"
        f"Инструкция: {c.instruction}\n"
        + steps
    )


def facts_to_text(f: dict) -> str:
    ev = ", ".join(f"[{e['idx']}] {e['type']}" for e in f["events"]) or "нет"
    lines = [
        f"- Реплик в диалоге: {f['n_messages']}",
        f"- Первая реплика оператора: [{f['first_operator_idx']}]",
        f"- Язык клиента: {f.get('client_language')}; язык первой реплики оператора: {f.get('first_operator_language')}",
    ]
    if f["first_reaction_sec"] is not None:
        lines.append(f"- Время первой реакции оператора: {f['first_reaction_sec']} сек "
                     f"(от {'эскалации' if f['reaction_ref'] == 'escalation' else 'последней реплики клиента'}); "
                     f"порог 3 мин {'ПРЕВЫШЕН' if f['reaction_exceeded_threshold'] else 'не превышен'}")
    if f["identity_confirmed_idx"] is not None:
        lines.append(f"- Системное сообщение о подтверждении личности: [{f['identity_confirmed_idx']}]")
    else:
        lines.append("- Системного сообщения о подтверждении личности нет "
                     "(это не нарушение само по себе: проверь, требовалась ли идентификация)")
    if f.get("end_event"):
        after = f.get("end_after_last_client_sec")
        lines.append(f"- Диалог закончился событием: [{f['end_event_idx']}] {END_LABEL.get(f['end_event'], f['end_event'])}"
                     + (f", через {after} сек после последней реплики клиента" if after is not None else ""))
    else:
        lines.append(f"- Системного события о завершении нет; последняя реплика: {f.get('last_message_speaker')}")
    if f.get("wait_events"):
        lines.append("- Ожидание клиента: " + ", ".join(f"[{e['idx']}] {e['type']}" for e in f["wait_events"]))
    lines.append(f"- Все системные события: {ev}")
    return "\n".join(lines)


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
        f"Канал: {conv.get('channel')}, язык: {conv.get('language')}\n"
        + (CALL_NOTE if conv.get("channel") == "voice call" else "")
        + "\n"
        f"ФАКТЫ:\n{facts_to_text(facts)}\n\n"
        f"ДИАЛОГ:\n{transcript_to_text(msgs)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
