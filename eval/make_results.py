"""Файл сдачи results/<dev|prod>/results.json одной командой: получить набор -> оценить -> выгрузить -> проверить.

python -m eval.make_results --group DEV             # все 65 диалогов DEV
python -m eval.make_results --group PROD            # 10 контрольных чатов (последний час)
python -m eval.make_results --group PROD --api-group CONTROL   # если в API у набора другое имя group_id
python -m eval.make_results --group DEV --ids p1-ru-001,p1-kz-003   # часть набора (файл не пишется)
python -m eval.make_results --group PROD --check    # только проверить уже готовый файл

Повторный запуск продолжает с места остановки: успешные оценки берутся из кэша
(data/eval_runs/<group>_cache.json), ошибки пробуются заново. --fresh - оценить всё заново.
Оценки также сохраняются в базу, чтобы их сразу было видно в интерфейсе.
Чек-лист - действующая (последняя) версия из базы; если базы нет - config/checklist.default.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import db  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.evaluator import evaluate  # noqa: E402
from backend.export import build_document, error_result, to_result, validate_document  # noqa: E402
from backend.ingest import fetch_group  # noqa: E402
from backend.models import Checklist  # noqa: E402

RAW = ROOT / "data" / "raw"
RUNS = ROOT / "data" / "eval_runs"
EXPECTED_COUNT = {"DEV": 65, "PROD": 10}


def load_records(group: str, refetch: bool) -> list[dict]:
    path = RAW / f"{group.lower()}.json"
    if path.exists() and not refetch:
        body = json.loads(path.read_text("utf-8"))
        return body["data"]["records"] if "data" in body else body
    records = fetch_group(group)
    RAW.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"data": {"records": records}}, ensure_ascii=False), "utf-8")
    return records


def active_checklist() -> Checklist:
    db.init()
    return db.sync_default_checklist(settings.DEFAULT_CHECKLIST)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--group", required=True, help="DEV или PROD: папка results/dev или results/prod")
    p.add_argument("--api-group", help="group_id в API организаторов, если отличается от --group")
    p.add_argument("--ids", help="через запятую: оценить только эти (для проверки, файл сдачи не пишется)")
    p.add_argument("--workers", type=int, default=settings.BATCH_WORKERS)
    p.add_argument("--fresh", action="store_true", help="не брать оценки из кэша")
    p.add_argument("--refetch", action="store_true", help="заново скачать набор из API")
    p.add_argument("--check", action="store_true", help="только проверить готовый results.json")
    p.add_argument("--custom-checklist", action="store_true",
                   help="чек-лист изменён (критерий заменён): не требовать ровно 8 официальных кодов")
    a = p.parse_args()
    group = a.group.upper()
    out = ROOT / "results" / group.lower() / "results.json"
    t_start = time.time()

    records = load_records(a.api_group or group, a.refetch)
    ids_all = [r["conversation_id"] for r in records]
    lengths = {r["conversation_id"]: len(r["transcript"]) for r in records}
    checklist = active_checklist()
    strict = not a.custom_checklist

    if a.check:
        errors = validate_document(json.loads(out.read_text("utf-8")), ids_all, lengths, strict)
        print("\n".join(errors) if errors else f"{out}: формат верный, записей {len(ids_all)}")
        sys.exit(1 if errors else 0)

    if len(records) != EXPECTED_COUNT.get(group, len(records)):
        print(f"ВНИМАНИЕ: в наборе {len(records)} диалогов, ожидалось {EXPECTED_COUNT[group]}")
    ids = a.ids.split(",") if a.ids else ids_all
    recs = {r["conversation_id"]: r for r in records}
    db.save_conversations(group, records)

    RUNS.mkdir(parents=True, exist_ok=True)
    cache_path = RUNS / f"{group.lower()}_cache.json"
    cache = {} if a.fresh or not cache_path.exists() else json.loads(cache_path.read_text("utf-8"))
    version = f"{checklist.checklist_id}:{checklist.version}"
    lock = threading.Lock()
    print(f"Набор {group}: {len(ids)} диалогов; чек-лист {version}; модель {settings.MODEL}; "
          f"резерв {settings.FALLBACK_MODELS or 'нет'}; потоков {a.workers}", flush=True)

    def one(cid: str) -> None:
        hit = cache.get(cid)
        if hit and hit.get("ok") and hit.get("checklist") == version:
            return
        t0 = time.time()
        try:
            ev = evaluate(recs[cid], checklist)
            ev["evaluation_id"] = db.save_evaluation(ev)
            entry = {"ok": True, "checklist": version, "evaluation": ev}
            msg = f"{ev['total']}{' ⚠ грубая ошибка' if ev['critical_error'] else ''} ({ev['model']})"
        except Exception as e:  # одна ошибка не останавливает прогон: в файле будет status=error
            entry = {"ok": False, "checklist": version, "error": f"{type(e).__name__}: {e}"}
            msg = "ОШИБКА " + entry["error"][:150]
        with lock:
            cache[cid] = entry
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")
            done = sum(1 for i in ids if cache.get(i, {}).get("checklist") == version)
            print(f"[{done}/{len(ids)}] {cid}: {msg}, {time.time() - t0:.0f} с", flush=True)

    with ThreadPoolExecutor(max(1, a.workers)) as ex:
        list(ex.map(one, ids))

    results, models, cost = [], [], 0.0
    for cid in ids:
        e = cache.get(cid) or {"ok": False, "error": "не оценён"}
        if e["ok"]:
            results.append(to_result(e["evaluation"], checklist, lengths.get(cid)))
            models.append(e["evaluation"]["model"])
            cost += e["evaluation"].get("cost_usd") or 0
        else:
            results.append(error_result(cid, e["error"]))
    doc = build_document(results, models, settings.TEAM_NAME, settings.MODEL_MODE)
    errors = validate_document(doc, ids, lengths, strict)

    n_err = sum(r["status"] == "error" for r in results)
    print(f"\nГотово за {time.time() - t_start:.0f} с: успешно {len(results) - n_err}, ошибок {n_err}, "
          f"грубых ошибок найдено {sum(bool(r['score'] and r['score']['critical_flag']) for r in results)}, "
          f"стоимость ${cost:.4f}")
    if a.ids:
        print("Задан --ids: файл сдачи не перезаписан (нужен полный набор).")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), "utf-8")
    print(f"Записан {out.relative_to(ROOT)}")
    if errors:
        print("ПРОВЕРКА ФОРМАТА НЕ ПРОЙДЕНА:\n  " + "\n  ".join(errors))
        sys.exit(1)
    print("Проверка формата пройдена.")
    if n_err:
        print(f"Есть {n_err} диалогов с ошибкой: запустите ту же команду ещё раз, они будут оценены заново.")
    print(f"Сдача: git add {out.relative_to(ROOT).as_posix()} && git commit -m \"results {group}\" && git push")


if __name__ == "__main__":
    main()
