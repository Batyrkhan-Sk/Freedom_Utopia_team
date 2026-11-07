"""Приём диалогов не из API организаторов: JSON-файл и вставленный текст. Плюс разбор страницы плеера."""
from __future__ import annotations

import html as _html
import re
import uuid

from .facts import detect_language

SPEAKERS = {
    "client": "client", "клиент": "client", "customer": "client", "user": "client",
    "operator": "operator", "оператор": "operator", "agent": "operator", "агент": "operator",
    "bot": "bot", "бот": "bot",
    "system": "system", "система": "system", "жүйе": "system",
}
_LINE = re.compile(r"^\s*(?:\[?(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]?\s+)?(?P<sp>[^\s:]+)\s*:\s*(?P<text>.+)$")
_SOURCE = re.compile(r"<(?:source|audio)\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)


def extract_audio_src(page: str) -> str | None:
    """audio_url организаторов - HTML-плеер; ссылка на запись лежит в <source src="...">."""
    m = _SOURCE.search(page)
    return _html.unescape(m.group(1)) if m else None


def validate_record(r: dict) -> dict:
    if not isinstance(r.get("transcript"), list) or not r["transcript"]:
        raise ValueError("transcript должен быть непустым массивом реплик")
    for i, m in enumerate(r["transcript"]):
        if not all(k in m for k in ("timestamp", "speaker", "text")):
            raise ValueError(f"реплика {i}: нужны поля timestamp, speaker, text")
        if m["speaker"] not in ("client", "operator", "bot", "system"):
            raise ValueError(f"реплика {i}: speaker должен быть client/operator/bot/system")
    out = dict(r)
    out.setdefault("conversation_id", f"upload-{uuid.uuid4().hex[:8]}")
    out.setdefault("channel", "agent chat")
    out.setdefault("audio_url", None)
    if not out.get("language"):
        out["language"] = detect_language(" ".join(m["text"] for m in r["transcript"] if m["speaker"] == "client")) or "RU"
    return out


def parse_pasted_text(text: str, channel: str = "agent chat", language: str | None = None,
                      conversation_id: str | None = None) -> dict:
    """По реплике на строку: «00:15 оператор: текст» или «client: текст». Время можно не указывать."""
    transcript, t = [], 0
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        m = _LINE.match(line)
        if not m or m.group("sp").lower() not in SPEAKERS:
            raise ValueError(f"строка {n}: ожидается «мм:сс роль: текст», роль - client/operator/bot/system")
        if m.group("ts"):
            ts = m.group("ts")
        else:
            ts, t = f"{t // 60:02d}:{t % 60:02d}", t + 10
        transcript.append({"timestamp": ts, "speaker": SPEAKERS[m.group("sp").lower()], "text": m.group("text").strip()})
    return validate_record({"conversation_id": conversation_id or f"paste-{uuid.uuid4().hex[:8]}",
                            "channel": channel, "language": language, "transcript": transcript})
