"""Факты, которые считает КОД до вызова ИИ: нумерация реплик, время реакции, системные события, язык.

Паттерны событий сверены со всеми 26 типами системных сообщений из DEV (RU/KZ/EN, чаты и звонки),
см. tests/test_facts.py. Порядок в EVENT_PATTERNS важен: на сообщение берётся первое совпадение."""
from __future__ import annotations

import re
from datetime import datetime

from .config import settings

EVENT_PATTERNS: list[tuple[str, str]] = [
    ("identity_confirmed", r"личност\w*.{0,30}подтвержд|идентификац\w*.{0,30}(пройден|подтвержд|успешн)"
                           r"|identity\s+(verified|confirmed)|жеке\s+бас\w*.{0,20}(расталды|тексерілді)"),
    ("connection_lost", r"соединени\w*.{0,30}(прерван|разорван|потерян)|связь.{0,30}(прерван|потерян)"
                        r"|connection.{0,30}(lost|interrupted|dropped)|байланыс.{0,20}үзіл"),
    ("chat_closed_by_operator", r"(оператор|агент)\w*\s+(закрыл|завершил)\s+чат"
                                r"|(agent|operator)\s+(closed|ended)\s+the\s+chat|оператор\w*\s+чатты\s+жапты"),
    ("client_left", r"клиент\w*\s+(покинул|вышел\s+из)\s+чат|(customer|client)\s+left|клиент\w*\s+чаттан\s+шықты"),
    ("client_ended_call", r"клиент\w*\s+завершил\s+(разговор|звонок)|(customer|client)\s+(ended|hung\s+up)"
                          r"|клиент\w*.{0,20}(қоңырауды|сөйлесуді)\s+аяқтады"),
    ("operator_left", r"(агент|оператор)\w*\s+вышел|(agent|operator)\s+left"),
    ("escalation", r"эскалац|escalat|transferred\s+to\s+an?\s+(agent|operator)|операторға\s+жіберілді"
                   r"|оператор\w*\s+ауыстыр|переведен\w*\s.{0,40}(представител|оператор|специалист)"),
    ("operator_joined", r"(агент|оператор)\w*\s+присоединил|(agent|operator)\s+joined"),
    ("hold_ended", r"больше\s+не\s+находится\s+на\s+удержании|off\s+hold|hold\s+ended"),
    ("hold_started", r"на\s+удержании|on\s+hold"),
    ("consultation", r"консультаци\w*\s+с\s+агент|consultation\s+with"),
    ("recording_paused", r"(запись|recording).{0,40}(приостановлен|paused)"),
    ("recording_resumed", r"(запись|recording).{0,40}(возобновлен|resumed)"),
    ("recording_started", r"(запись|recording|жазу).{0,40}(запущен|начат|started|басталды)"),
    ("language_changed", r"language\w*\s.{0,40}(updated|changed)|язык\w*.{0,30}(изменен|обновлен)"),
]
_COMPILED = [(name, re.compile(rx, re.I)) for name, rx in EVENT_PATTERNS]

# Чем закончился диалог: важно для «Завершения» (N/A, если оборвался не по вине оператора)
END_EVENTS = {"connection_lost", "client_left", "client_ended_call", "chat_closed_by_operator"}
WAIT_EVENTS = {"hold_started", "consultation"}

_KZ_LETTERS = re.compile(r"[әғқңөұүһіӘҒҚҢӨҰҮҺІ]")


def detect_event(text: str) -> str | None:
    for name, rx in _COMPILED:
        if rx.search(text):
            return name
    return None


def detect_language(text: str) -> str | None:
    """RU / KZ / EN по буквам. Казахский узнаём по специфическим буквам (ә ғ қ ң ө ұ ү һ і)."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return None
    if _KZ_LETTERS.search(text):
        return "KZ"
    cyr = sum(1 for ch in letters if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    lat = sum(1 for ch in letters if "a" <= ch.lower() <= "z")
    return "RU" if cyr >= lat else "EN"


def parse_ts(ts: str) -> float:
    """'02:41' (чат) или ISO 8601 с 7 знаками дроби (звонок) -> секунды."""
    ts = ts.strip()
    if re.fullmatch(r"\d+(:\d{2}){1,2}", ts):
        parts = [int(p) for p in ts.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    ts = re.sub(r"(\.\d{6})\d+", r"\1", ts)          # 7 знаков -> 6
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def fmt_t(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def build_messages(transcript: list[dict]) -> list[dict]:
    """Нумерация = позиция в исходном массиве. Тот же номер использует интерфейс для скролла."""
    out, t0 = [], None
    for i, m in enumerate(transcript):
        sec = parse_ts(m["timestamp"])
        t0 = sec if t0 is None else t0
        out.append({"idx": i, "ts": m["timestamp"], "t": sec - t0,   # t = offset_sec для плеера
                    "speaker": m["speaker"], "text": m["text"]})
    return out


def compute_facts(msgs: list[dict], channel: str = "agent chat") -> dict:
    events = []
    for m in msgs:
        if m["speaker"] == "system":
            name = detect_event(m["text"])
            if name:
                events.append({"idx": m["idx"], "type": name})

    first_op = next((m for m in msgs if m["speaker"] == "operator"), None)
    reaction, ref_src = None, None
    if first_op and channel != "voice call":
        esc = [e for e in events if e["type"] == "escalation" and e["idx"] < first_op["idx"]]
        if esc:
            ref, ref_src = msgs[esc[-1]["idx"]], "escalation"
        else:
            prev = [m for m in msgs[: first_op["idx"]] if m["speaker"] == "client"]
            ref, ref_src = (prev[-1], "last_client_message") if prev else (None, None)
        if ref:
            reaction = round(first_op["t"] - ref["t"])

    ident = next((e["idx"] for e in events if e["type"] == "identity_confirmed"), None)

    # Как закончился диалог
    end = next((e for e in reversed(events) if e["type"] in END_EVENTS), None)
    last_op = next((m for m in reversed(msgs) if m["speaker"] == "operator"), None)
    last_client = next((m for m in reversed(msgs) if m["speaker"] == "client"), None)
    end_after_client_sec = None
    if end and last_client and last_client["idx"] < end["idx"]:
        end_after_client_sec = round(msgs[end["idx"]]["t"] - last_client["t"])

    # Язык клиента и первой реплики оператора (для «Установления контакта»)
    client_text = " ".join(m["text"] for m in msgs if m["speaker"] == "client")
    return {
        "n_messages": len(msgs),
        "first_operator_idx": first_op["idx"] if first_op else None,
        "first_reaction_sec": reaction,
        "reaction_ref": ref_src,
        "reaction_exceeded_threshold": None if reaction is None else reaction > settings.REACTION_THRESHOLD_SEC,
        "identity_confirmed_by_system": ident is not None,
        "identity_confirmed_idx": ident,
        "end_event": end["type"] if end else None,
        "end_event_idx": end["idx"] if end else None,
        "end_after_last_client_sec": end_after_client_sec,
        "last_message_speaker": msgs[-1]["speaker"] if msgs else None,
        "last_operator_idx": last_op["idx"] if last_op else None,
        "wait_events": [e for e in events if e["type"] in WAIT_EVENTS],
        "client_language": detect_language(client_text),
        "first_operator_language": detect_language(first_op["text"]) if first_op else None,
        "events": events,
    }
