"""SQLite. JSON храним текстом: схема простая, правки контракта не требуют миграций."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings
from .models import Checklist

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, group_id TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS checklists(checklist_id TEXT, version INTEGER, data TEXT, PRIMARY KEY(checklist_id, version));
CREATE TABLE IF NOT EXISTS evaluations(id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT,
  checklist_id TEXT, checklist_version INTEGER, data TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS appeals(id INTEGER PRIMARY KEY AUTOINCREMENT, evaluation_id INTEGER,
  criterion_id TEXT, comment TEXT, status TEXT DEFAULT 'open', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, appeal_id INTEGER,
  data TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""


@contextmanager
def conn():
    Path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(settings.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)


# ----- conversations -----
def save_conversations(group_id: str, records: list[dict]):
    with conn() as c:
        for r in records:
            c.execute("INSERT OR REPLACE INTO conversations VALUES(?,?,?)",
                      (r["conversation_id"], group_id, json.dumps(r, ensure_ascii=False)))


def get_conversation(cid: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT data FROM conversations WHERE id=?", (cid,)).fetchone()
    return json.loads(row["data"]) if row else None


def list_conversations() -> list[dict]:
    """Список для интерфейса: данные последней оценки и число открытых апелляций по диалогу."""
    q = """SELECT c.id, c.group_id, c.data,
                  (SELECT e.id FROM evaluations e WHERE e.conversation_id=c.id ORDER BY e.id DESC LIMIT 1) AS eid
           FROM conversations c ORDER BY c.id"""
    out = []
    with conn() as c:
        rows = c.execute(q).fetchall()
        for row in rows:
            d = json.loads(row["data"])
            ev = None
            if row["eid"]:
                ev = json.loads(c.execute("SELECT data FROM evaluations WHERE id=?", (row["eid"],)).fetchone()["data"])
            open_n = c.execute("""SELECT COUNT(*) n FROM appeals a JOIN evaluations e ON e.id=a.evaluation_id
                                  WHERE e.conversation_id=? AND a.status='open'""", (row["id"],)).fetchone()["n"]
            out.append({"conversation_id": d["conversation_id"], "channel": d.get("channel"),
                        "language": d.get("language"), "group_id": row["group_id"],
                        "n_messages": len(d.get("transcript") or []),
                        "evaluation_id": row["eid"],
                        "total": ev["total"] if ev else None,
                        "critical_error": ev["critical_error"] if ev else None,
                        "needs_review": ev["needs_review"] if ev else None,
                        "open_appeals": open_n,
                        "model": ev.get("model") if ev else None,
                        "checklist_version": ev.get("checklist_version") if ev else None})
    return out


def conversation_group(cid: str) -> str | None:
    with conn() as c:
        row = c.execute("SELECT group_id FROM conversations WHERE id=?", (cid,)).fetchone()
    return row["group_id"] if row else None


# ----- checklists (каждое сохранение = новая версия) -----
def save_checklist(cl: Checklist) -> int:
    with conn() as c:
        row = c.execute("SELECT MAX(version) v FROM checklists WHERE checklist_id=?", (cl.checklist_id,)).fetchone()
        cl.version = (row["v"] or 0) + 1
        c.execute("INSERT INTO checklists VALUES(?,?,?)", (cl.checklist_id, cl.version, cl.model_dump_json()))
    return cl.version


def sync_default_checklist(path: Path) -> Checklist:
    """Стартовый чек-лист из файла -> база. Если файл изменился с прошлой синхронизации (новые правила),
    сохраняем его как новую версию: иначе оценка шла бы по устаревшей копии в базе.
    Правки из интерфейса тоже становятся версиями; файл перекрывает их, только когда меняется сам."""
    import hashlib
    text = path.read_text("utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    cl = Checklist.model_validate_json(text)
    with conn() as c:
        row = c.execute("SELECT value FROM meta WHERE key='default_checklist_sha256'").fetchone()
    if not row or row["value"] != digest or get_checklist(cl.checklist_id) is None:
        save_checklist(cl)
        with conn() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES('default_checklist_sha256', ?)", (digest,))
    return get_checklist(cl.checklist_id)


def get_checklist(checklist_id: str, version: int | None = None) -> Checklist | None:
    with conn() as c:
        if version is None:
            row = c.execute("SELECT data FROM checklists WHERE checklist_id=? ORDER BY version DESC LIMIT 1",
                            (checklist_id,)).fetchone()
        else:
            row = c.execute("SELECT data FROM checklists WHERE checklist_id=? AND version=?",
                            (checklist_id, version)).fetchone()
    return Checklist.model_validate_json(row["data"]) if row else None


def list_checklists() -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT checklist_id, MAX(version) v FROM checklists GROUP BY checklist_id").fetchall()
    return [{"checklist_id": r["checklist_id"], "latest_version": r["v"]} for r in rows]


# ----- evaluations -----
def save_evaluation(ev: dict) -> int:
    with conn() as c:
        cur = c.execute("INSERT INTO evaluations(conversation_id, checklist_id, checklist_version, data) VALUES(?,?,?,?)",
                        (ev["conversation_id"], ev["checklist_id"], ev["checklist_version"],
                         json.dumps(ev, ensure_ascii=False)))
        # Открытые апелляции переходят на новую оценку: решение должно менять актуальную оценку, а не старую
        c.execute("""UPDATE appeals SET evaluation_id=? WHERE status='open' AND evaluation_id IN
                     (SELECT id FROM evaluations WHERE conversation_id=? AND id<>?)""",
                  (cur.lastrowid, ev["conversation_id"], cur.lastrowid))
        return cur.lastrowid


def update_evaluation(eid: int, ev: dict):
    """Оценка после решения по апелляции: перезаписываем на месте, история - в appeal_history."""
    data = {k: v for k, v in ev.items() if k != "evaluation_id"}
    with conn() as c:
        c.execute("UPDATE evaluations SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), eid))


def get_evaluation(eid: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT id, data FROM evaluations WHERE id=?", (eid,)).fetchone()
    return {**json.loads(row["data"]), "evaluation_id": row["id"]} if row else None


def latest_evaluation(cid: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT id, data FROM evaluations WHERE conversation_id=? ORDER BY id DESC LIMIT 1",
                        (cid,)).fetchone()
    return {**json.loads(row["data"]), "evaluation_id": row["id"]} if row else None


# ----- appeals / decisions -----
def create_appeal(evaluation_id: int, criterion_id: str, comment: str) -> int:
    with conn() as c:
        return c.execute("INSERT INTO appeals(evaluation_id, criterion_id, comment) VALUES(?,?,?)",
                         (evaluation_id, criterion_id, comment)).lastrowid


def get_appeal(aid: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM appeals WHERE id=?", (aid,)).fetchone()
        dec = c.execute("SELECT data FROM decisions WHERE appeal_id=? ORDER BY id DESC LIMIT 1", (aid,)).fetchone()
    return {**dict(row), "decision": json.loads(dec["data"]) if dec else None} if row else None


def _appeal_rows(where: str, args: tuple) -> list[dict]:
    q = f"""SELECT a.*, e.conversation_id,
                   (SELECT d.data FROM decisions d WHERE d.appeal_id=a.id ORDER BY d.id DESC LIMIT 1) AS dec
            FROM appeals a LEFT JOIN evaluations e ON e.id=a.evaluation_id {where} ORDER BY a.id DESC"""
    with conn() as c:
        rows = c.execute(q, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        dec = d.pop("dec")
        d["decision"] = json.loads(dec) if dec else None
        out.append(d)
    return out


def list_appeals(status: str | None = None) -> list[dict]:
    """status: open / decided. В каждой строке conversation_id и последнее решение (или null)."""
    return _appeal_rows("WHERE a.status=?" if status else "", (status,) if status else ())


def appeals_for_conversation(cid: str) -> list[dict]:
    return _appeal_rows("WHERE e.conversation_id=?", (cid,))


def save_decision(appeal_id: int, result: dict):
    with conn() as c:
        c.execute("INSERT INTO decisions(appeal_id, data) VALUES(?,?)",
                  (appeal_id, json.dumps(result, ensure_ascii=False)))
    set_appeal_status(appeal_id, "decided")


def set_appeal_status(appeal_id: int, status: str):
    with conn() as c:
        c.execute("UPDATE appeals SET status=? WHERE id=?", (status, appeal_id))
