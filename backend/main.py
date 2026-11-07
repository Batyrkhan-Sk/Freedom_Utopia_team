"""HTTP-слой. Тонкий: вся логика в evaluator / scoring / appeals."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .appeals import apply_decision, apply_to_evaluation
from .checklists import check_checklist, diff_checklists
from .config import settings
from .evaluator import evaluate
from .facts import build_messages
from .ingest import extract_audio_src, fetch_group, parse_pasted_text, validate_record
from .models import Checklist
from .reevaluate import reevaluate_conversation
from .refs import humanize_evaluation

app = FastAPI(title="CQA")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
JOBS: dict[str, dict] = {}


@app.on_event("startup")
def startup():
    db.init()
    db.sync_default_checklist(settings.DEFAULT_CHECKLIST)   # изменился файл чек-листа -> новая версия в базе


# ---------- данные ----------
@app.post("/import")
def import_group(group_id: str):
    """Прокси к API организаторов (решает CORS). URL задаётся переменной CQA_ORG_API_URL."""
    try:
        records = fetch_group(group_id)
    except ValueError as e:
        raise HTTPException(502, str(e))
    db.save_conversations(group_id, records)
    return {"imported": len(records)}


@app.get("/conversations")
def conversations(critical: bool | None = None, needs_review: bool | None = None,
                  has_open_appeal: bool | None = None, language: str | None = None,
                  channel: str | None = None, q: str | None = None):
    """Список для интерфейса с фильтрами. Не оценённые диалоги: total/critical_error/... = null."""
    out = []
    for x in db.list_conversations():
        if critical is not None and bool(x["critical_error"]) != critical:
            continue
        if needs_review is not None and bool(x["needs_review"]) != needs_review:
            continue
        if has_open_appeal is not None and (x["open_appeals"] > 0) != has_open_appeal:
            continue
        if language and (x["language"] or "").upper() != language.upper():
            continue
        if channel and x["channel"] != channel:
            continue
        if q and q.lower() not in x["conversation_id"].lower():
            continue
        out.append(x)
    return out


@app.get("/conversations/{cid}")
def conversation(cid: str):
    """Карточка: реплики с номером idx (как в evidence_msg_ids) и смещением t (сек) для перемотки записи."""
    conv = db.get_conversation(cid)
    if not conv:
        raise HTTPException(404)
    msgs = build_messages(conv["transcript"])
    return {
        "conversation": {
            "conversation_id": conv["conversation_id"], "channel": conv.get("channel"),
            "language": conv.get("language"), "group_id": db.conversation_group(cid),
            "has_audio": bool(conv.get("audio_url")),
            "messages": [{"idx": m["idx"], "ts": m["ts"], "t": m["t"], "speaker": m["speaker"], "text": m["text"]}
                         for m in msgs],
        },
        "evaluation": humanize_evaluation(db.latest_evaluation(cid), len(msgs)),   # номера реплик с 1
        "appeals": db.appeals_for_conversation(cid),
    }


@app.get("/audio/{cid}")
def audio(cid: str):
    """audio_url отдаёт HTML-плеер со ссылкой на mp3, которая живёт ~1 час -> достаём свежую при каждом запросе."""
    conv = db.get_conversation(cid)
    if not conv or not conv.get("audio_url"):
        raise HTTPException(404)
    page = httpx.get(conv["audio_url"], timeout=20, follow_redirects=True).text
    src = extract_audio_src(page)
    if not src:
        raise HTTPException(502, "ссылка на запись не найдена в странице плеера")
    return RedirectResponse(src)


class PasteIn(BaseModel):
    text: str
    channel: str = "agent chat"
    language: str | None = None
    conversation_id: str | None = None


@app.post("/conversations")
def upload_conversations(body: dict | list[dict], group_id: str = "upload"):
    """JSON-файл: одна запись или массив в формате API организаторов."""
    records = body if isinstance(body, list) else [body]
    try:
        records = [validate_record(r) for r in records]
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.save_conversations(group_id, records)
    return {"imported": len(records), "conversation_ids": [r["conversation_id"] for r in records]}


@app.post("/conversations/text")
def paste_conversation(body: PasteIn):
    """Вставленный текст: «мм:сс роль: текст» по строке на реплику."""
    try:
        rec = parse_pasted_text(body.text, body.channel, body.language, body.conversation_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.save_conversations("upload", [rec])
    return {"imported": 1, "conversation_id": rec["conversation_id"], "messages": len(rec["transcript"])}


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
    errors = check_checklist(cl)
    if errors:
        raise HTTPException(422, errors)
    return {"checklist_id": cl.checklist_id, "version": db.save_checklist(cl)}


@app.get("/checklists/{checklist_id}/diff")
def checklist_diff(checklist_id: str, from_version: int, to_version: int | None = None):
    """Что изменилось между версиями: changed/added - ИИ спросят заново, weight_only - только пересчёт."""
    old, new = db.get_checklist(checklist_id, from_version), db.get_checklist(checklist_id, to_version)
    if not old or not new:
        raise HTTPException(404, "версия чек-листа не найдена")
    return diff_checklists(old, new)


# ---------- оценка ----------
def _run(conv_id: str, checklist_id: str, version: int | None) -> dict:
    conv, cl = db.get_conversation(conv_id), db.get_checklist(checklist_id, version)
    if not conv or not cl:
        raise HTTPException(404, "диалог или чек-лист не найден")
    ev = evaluate(conv, cl)
    ev["evaluation_id"] = db.save_evaluation(ev)
    return ev


def _n_msgs(cid: str) -> int:
    conv = db.get_conversation(cid)
    return len(conv["transcript"]) if conv else 0


@app.post("/evaluate/{conv_id}")
def evaluate_one(conv_id: str, checklist_id: str = "chat-quality", version: int | None = None):
    ev = _run(conv_id, checklist_id, version)
    return humanize_evaluation(ev, _n_msgs(conv_id))


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


class ReevaluateIn(BaseModel):
    checklist_id: str = "chat-quality"
    version: int | None = None                  # None -> последняя версия
    conversation_ids: list[str] | None = None   # None -> все диалоги, у которых есть оценка


def _reevaluate(job_id: str, ids: list[str], checklist_id: str, version: int | None):
    job = JOBS[job_id]
    new_cl = db.get_checklist(checklist_id, version)

    def one(i):
        try:
            conv, prev = db.get_conversation(i), db.latest_evaluation(i)
            old_cl = db.get_checklist(prev["checklist_id"], prev["checklist_version"]) if prev else None
            ev, row = reevaluate_conversation(conv, prev, old_cl, new_cl)
            row["evaluation_id"] = ev["evaluation_id"] = db.save_evaluation(ev)
            job["diffs"].append(row)
        except Exception as e:  # одна ошибка не должна ронять пакет
            job["failed"].append({"id": i, "error": str(e)})
        finally:
            job["done"] += 1

    with ThreadPoolExecutor(settings.BATCH_WORKERS) as ex:
        list(ex.map(one, ids))
    job["status"] = "finished"


@app.post("/reevaluate")
def reevaluate(body: ReevaluateIn, bg: BackgroundTasks):
    """Переоценка после правки чек-листа: ИИ спрашивают только о новых/изменённых критериях."""
    if not db.get_checklist(body.checklist_id, body.version):
        raise HTTPException(404, "чек-лист не найден")
    ids = body.conversation_ids or [c["conversation_id"] for c in db.list_conversations() if c["evaluation_id"]]
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "running", "total": len(ids), "done": 0, "failed": [], "diffs": []}
    bg.add_task(_reevaluate, job_id, ids, body.checklist_id, body.version)
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
    updated = apply_to_evaluation(ev, cl, res, aid)   # решение меняет саму оценку: итог в карточке актуален
    db.update_evaluation(ap["evaluation_id"], updated)
    ev = db.get_evaluation(ap["evaluation_id"])
    return {**res, "evaluation": humanize_evaluation(ev, _n_msgs(ev["conversation_id"]))}


# ---------- интерфейс ----------
_FRONTEND = settings.DEFAULT_CHECKLIST.parent.parent / "frontend"
if _FRONTEND.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_FRONTEND), html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui/" if _FRONTEND.is_dir() else "/docs")
