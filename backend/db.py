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
    q = """SELECT c.data, (SELECT data FROM evaluations e WHERE e.conversation_id=c.id
                           ORDER BY e.id DESC LIMIT 1) AS ev FROM conversations c"""
    out = []
    with conn() as c:
        for row in c.execute(q):
            d, ev = json.loads(row["data"]), (json.loads(row["ev"]) if row["ev"] else None)
            out.append({"conversation_id": d["conversation_id"], "channel": d["channel"],
                        "language": d["language"], "n_messages": len(d["transcript"]),
                        "total": ev["total"] if ev else None,
                        "needs_review": ev["needs_review"] if ev else None,
                        "critical_error": ev["critical_error"] if ev else None})
    return out


# ----- checklists (каждое сохранение = новая версия) -----
def save_checklist(cl: Checklist) -> int:
    with conn() as c:
        row = c.execute("SELECT MAX(version) v FROM checklists WHERE checklist_id=?", (cl.checklist_id,)).fetchone()
        cl.version = (row["v"] or 0) + 1
        c.execute("INSERT INTO checklists VALUES(?,?,?)", (cl.checklist_id, cl.version, cl.model_dump_json()))
    return cl.version


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
        return cur.lastrowid


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


def list_appeals(status: str | None = None) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM appeals" + (" WHERE status=?" if status else "") + " ORDER BY id DESC",
                         (status,) if status else ()).fetchall()
    return [dict(r) for r in rows]


def save_decision(appeal_id: int, result: dict):
    with conn() as c:
        c.execute("INSERT INTO decisions(appeal_id, data) VALUES(?,?)",
                  (appeal_id, json.dumps(result, ensure_ascii=False)))
        c.execute("UPDATE appeals SET status='resolved' WHERE id=?", (appeal_id,))
