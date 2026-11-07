"""HTTP-слой. Тонкий: вся логика в evaluator / scoring / appeals."""
from __future__ import annotations

import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import db
from .appeals import apply_decision
from .config import settings
from .evaluator import evaluate
from .models import Checklist

app = FastAPI(title="CQA")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
JOBS: dict[str, dict] = {}


@app.on_event("startup")
def startup():
    db.init()
    if not db.list_checklists():  # первая загрузка: стартовый чек-лист из файла
        db.save_checklist(Checklist.model_validate(json.loads(settings.DEFAULT_CHECKLIST.read_text("utf-8"))))


# ---------- данные ----------
@app.post("/import")
def import_group(group_id: str):
    """Прокси к API организаторов (решает CORS). URL задаётся переменной CQA_ORG_API_URL."""
    r = httpx.get(settings.ORG_API_URL, params={"group_id": group_id}, timeout=60)
    body = r.json()
    if not body.get("isSuccess"):
        raise HTTPException(502, body.get("error") or "API организаторов вернул ошибку")
    records = body["data"]["records"]
    db.save_conversations(group_id, records)
    return {"imported": len(records)}


@app.get("/conversations")
def conversations():
    return db.list_conversations()


@app.get("/conversations/{cid}")
def conversation(cid: str):
    conv = db.get_conversation(cid)
    if not conv:
        raise HTTPException(404)
    return {"conversation": conv, "evaluation": db.latest_evaluation(cid)}


@app.get("/audio/{cid}")
def audio(cid: str):
    """audio_url отдаёт HTML-плеер со ссылкой на mp3, которая живёт ~1 час -> достаём свежую при каждом запросе."""
    conv = db.get_conversation(cid)
    if not conv or not conv.get("audio_url"):
        raise HTTPException(404)
    html = httpx.get(conv["audio_url"], timeout=20, follow_redirects=True).text
    m = re.search(r"https?://[^\s\"'<>]+\.mp3[^\s\"'<>]*", html)
    if not m:
        raise HTTPException(502, "mp3 не найден в плеере: проверьте разметку страницы")
    return RedirectResponse(m.group(0).replace("&amp;", "&"))


# ---------- чек-листы ----------
@app.get("/checklists")
def checklists():
    return db.list_checklists()


@app.get("/checklists/{checklist_id}")
def checklist(checklist_id: str, version: int | None = None):
    cl = db.get_checklist(checklist_id, version)
    if not cl:
        raise HTTPException(404)
    return cl


@app.post("/checklists")
def save_checklist(cl: Checklist):
    """Каждое сохранение создаёт новую версию. Старые оценки остаются привязаны к своей версии."""
    return {"checklist_id": cl.checklist_id, "version": db.save_checklist(cl)}


# ---------- оценка ----------
def _run(conv_id: str, checklist_id: str, version: int | None) -> dict:
    conv, cl = db.get_conversation(conv_id), db.get_checklist(checklist_id, version)
    if not conv or not cl:
        raise HTTPException(404, "диалог или чек-лист не найден")
    ev = evaluate(conv, cl)
    ev["evaluation_id"] = db.save_evaluation(ev)
    return ev


@app.post("/evaluate/{conv_id}")
def evaluate_one(conv_id: str, checklist_id: str = "chat-quality", version: int | None = None):
    return _run(conv_id, checklist_id, version)


class BatchIn(BaseModel):
    conversation_ids: list[str] | None = None   # None -> все
    checklist_id: str = "chat-quality"
    version: int | None = None


def _batch(job_id: str, ids: list[str], cid: str, ver: int | None):
    job = JOBS[job_id]

    def one(i):
        try:
            _run(i, cid, ver)
        except Exception as e:  # одна ошибка не должна ронять пакет
            job["failed"].append({"id": i, "error": str(e)})
        finally:
            job["done"] += 1

    with ThreadPoolExecutor(settings.BATCH_WORKERS) as ex:
        list(ex.map(one, ids))
    job["status"] = "finished"


@app.post("/evaluate-batch")
def evaluate_batch(body: BatchIn, bg: BackgroundTasks):
    ids = body.conversation_ids or [c["conversation_id"] for c in db.list_conversations()]
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "running", "total": len(ids), "done": 0, "failed": []}
    bg.add_task(_batch, job_id, ids, body.checklist_id, body.version)
    return {"job_id": job_id, "total": len(ids)}


@app.get("/jobs/{job_id}")
def job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404)
    return JOBS[job_id]


# ---------- апелляции ----------
class AppealIn(BaseModel):
    evaluation_id: int
    criterion_id: str
    comment: str


class DecisionIn(BaseModel):
    new_answer: str | float   # то, что поставил проверяющий; совпадает с исходным = «подтвердить»
    reason: str


@app.post("/appeals")
def create_appeal(a: AppealIn):
    if not db.get_evaluation(a.evaluation_id):
        raise HTTPException(404, "оценка не найдена")
    return {"appeal_id": db.create_appeal(a.evaluation_id, a.criterion_id, a.comment)}


@app.get("/appeals")
def appeals(status: str | None = None):
    return db.list_appeals(status)


@app.get("/appeals/{aid}")
def appeal(aid: int):
    ap = db.get_appeal(aid)
    if not ap:
        raise HTTPException(404)
    ev = db.get_evaluation(ap["evaluation_id"])
    return {**ap, "evaluation": ev}   # фронтенд берёт из оценки реплики-доказательства и рисует фрагмент


@app.post("/appeals/{aid}/decision")
def decide(aid: int, d: DecisionIn):
    ap = db.get_appeal(aid)
    if not ap:
        raise HTTPException(404)
    ev = db.get_evaluation(ap["evaluation_id"])
    cl = db.get_checklist(ev["checklist_id"], ev["checklist_version"])   # именно та версия, по которой оценивали
    try:
        res = apply_decision(ev, cl, ap["criterion_id"], d.new_answer, d.reason)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.save_decision(aid, res)
    return res
