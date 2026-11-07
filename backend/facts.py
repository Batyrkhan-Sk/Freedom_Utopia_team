"""Факты, которые считает КОД до вызова ИИ: нумерация реплик, время реакции, системные события.
Паттерны событий - приблизительные: подгоните под реальные тексты из data/raw/dev.json."""
from __future__ import annotations

import re
from datetime import datetime

from .config import settings

EVENT_PATTERNS = {
    "escalation": r"эскалац|escalat|transferr|оператор(ға)?\s+ауыстыр",
    "identity_confirmed": r"идентификац\w*\s+(пройден|подтвержд)|личност\w*\s+подтвержд|identity\s+(verified|confirmed)|жеке\s+бас\w*\s+(расталды|тексеріл)",
    "connection_lost": r"связь\s+(прерван|потеря)|соединение\s+(прерван|разорван)|connection\s+(lost|interrupted)|байланыс\s+үзіл",
    "chat_closed": r"чат\s+(закрыт|завершен|завершён)|chat\s+(closed|ended)|чат\s+жабыл",
    "client_left": r"клиент\s+покинул|(customer|client)\s+left|клиент\s+шығып",
}
_COMPILED = {k: re.compile(v, re.I) for k, v in EVENT_PATTERNS.items()}


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
            for name, rx in _COMPILED.items():
                if rx.search(m["text"]):
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
    return {
        "n_messages": len(msgs),
        "first_operator_idx": first_op["idx"] if first_op else None,
        "first_reaction_sec": reaction,
        "reaction_ref": ref_src,
        "reaction_exceeded_threshold": None if reaction is None else reaction > settings.REACTION_THRESHOLD_SEC,
        "identity_confirmed_by_system": ident is not None,
        "identity_confirmed_idx": ident,
        "events": events,
    }
