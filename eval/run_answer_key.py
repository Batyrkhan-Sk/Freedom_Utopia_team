"""Прогон оценщика по эталону и отчёт о точности.

python -m eval.run_answer_key                      # все диалоги эталона, модель из .env (CQA_MODEL)
python -m eval.run_answer_key --ids p1-ru-001,p1-kz-003
python -m eval.run_answer_key --resume data/eval_runs/<файл>.json   # дозапустить прерванный прогон
python -m eval.run_answer_key --report data/eval_runs/<файл>.json   # только отчёт
python -m eval.run_answer_key --from-cache data/eval_runs/dev_cache.json   # отчёт по кэшу make_results (без вызовов ИИ)

Нужен data/raw/dev.json (скачивается из API организаторов, в git не хранится).
Совет для настройки: CQA_RESAMPLE=1 (без повторного опроса) - в 3-5 раз меньше вызовов.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.evaluator import evaluate  # noqa: E402
from backend.models import Checklist  # noqa: E402

KEY_PATH = ROOT / "data" / "answer_key" / "draft.json"
DEV_PATH = ROOT / "data" / "raw" / "dev.json"
RUNS = ROOT / "data" / "eval_runs"


def _norm(a):
    return int(a) if isinstance(a, float) and a.is_integer() else a


def run(ids: list[str], out: Path, checklist: Checklist, key: dict) -> dict:
    recs = {r["conversation_id"]: r for r in json.loads(DEV_PATH.read_text("utf-8"))["data"]["records"]}
    results = json.loads(out.read_text("utf-8")) if out.exists() else {}
    for cid in ids:
        if cid in results and results[cid].get("ok"):
            continue
        t0 = time.time()
        try:
            ev = evaluate(recs[cid], checklist)
            results[cid] = {
                "ok": True, "total": ev["total"], "critical": ev["critical_error"],
                "answers": {c["criterion_id"]: _norm(c["answer"]) for c in ev["criteria"]},
                "explanations": {c["criterion_id"]: c["explanation"] for c in ev["criteria"]},
                "calls": ev["llm_calls"], "tokens": ev["tokens"], "cost_usd": ev["cost_usd"],
                "model": ev["model"], "sec": round(time.time() - t0, 1),
            }
            print(f"{cid}: {ev['total']} (эталон {key[cid]['expected_total']}), "
                  f"вызовов {ev['llm_calls']}, {results[cid]['sec']} с, {ev['model']}", flush=True)
        except Exception as e:  # одна ошибка не останавливает прогон
            results[cid] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
            print(f"{cid}: ОШИБКА {results[cid]['error'][:150]}", flush=True)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1), "utf-8")
    return results


def report(results: dict, key: dict, checklist: Checklist) -> None:
    crit_ids = [c.id for c in checklist.criteria]
    ok_c, n_c, ok_l, n_l = Counter(), Counter(), Counter(), Counter()
    confusion, misses = Counter(), defaultdict(list)
    exact, abs_err, flag_ok, done, failed = 0, [], 0, 0, []
    cost, sec, calls = 0.0, [], []
    for cid, k in key.items():
        r = results.get(cid)
        if not r:
            continue
        if not r["ok"]:
            failed.append(cid)
            continue
        done += 1
        lang = "ЗВОНКИ" if cid.startswith("p4") else cid.split("-")[1].upper()
        for c in crit_ids:
            want, got = str(k["criteria"][c]["answer"]), str(r["answers"].get(c))
            hit = want == got
            ok_c[c] += hit; n_c[c] += 1; ok_l[lang] += hit; n_l[lang] += 1
            if not hit:
                confusion[(want, got)] += 1
                misses[c].append(f"{cid}: {want}→{got}")
        exact += r["total"] == k["expected_total"]
        if r["total"] is not None and k["expected_total"] is not None:
            abs_err.append(abs(r["total"] - k["expected_total"]))
        flag_ok += r["critical"] == k["critical_error"]
        cost += r.get("cost_usd") or 0; sec.append(r["sec"]); calls.append(r["calls"])

    pct = lambda a, b: f"{100 * a / b:.0f}% ({a}/{b})" if b else "-"
    avg = lambda xs: sum(xs) / len(xs) if xs else 0
    print(f"\nДиалогов: {done}, ошибок выполнения: {len(failed)} {failed or ''}")
    print(f"Критерии совпали с эталоном: {pct(sum(ok_c.values()), sum(n_c.values()))}")
    chats_ok, chats_n = sum(ok_l[x] for x in ("RU", "KZ", "EN")), sum(n_l[x] for x in ("RU", "KZ", "EN"))
    print(f"  только чаты (как на контроле): {pct(chats_ok, chats_n)}")
    print(f"Итог совпал точно: {pct(exact, done)}; средняя ошибка итога: {avg(abs_err):.1f} балла")
    print(f"Флаг грубой ошибки верен: {pct(flag_ok, done)}")
    print("\nПо критериям:")
    for c in crit_ids:
        print(f"  {c:<24} {pct(ok_c[c], n_c[c]):>12}")
    print("По языкам:")
    for lang in ("RU", "KZ", "EN", "ЗВОНКИ"):
        if n_l[lang]:
            print(f"  {lang:<24} {pct(ok_l[lang], n_l[lang]):>12}")
    print(f"\nВызовов на диалог: {avg(calls):.1f}; секунд на диалог: {avg(sec):.0f}; стоимость прогона: ${cost:.4f}")
    print("Частые ошибки (эталон→модель): " + ", ".join(f"{w}→{g} ×{n}" for (w, g), n in confusion.most_common(6)))
    for c in crit_ids:
        if misses[c]:
            print(f"  {c}: " + "; ".join(misses[c]))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--ids", help="через запятую; по умолчанию весь эталон")
    p.add_argument("--resume", type=Path)
    p.add_argument("--report", type=Path)
    p.add_argument("--from-cache", type=Path, help="кэш eval.make_results: отчёт по эталонным диалогам из него")
    a = p.parse_args()

    checklist = Checklist.model_validate(json.loads(settings.DEFAULT_CHECKLIST.read_text("utf-8")))
    key = json.loads(KEY_PATH.read_text("utf-8"))["conversations"]
    if a.from_cache:
        cache = json.loads(a.from_cache.read_text("utf-8"))
        results = {}
        for cid, e in cache.items():
            if cid not in key:
                continue
            if not e.get("ok"):
                results[cid] = {"ok": False, "error": e.get("error", "")}
                continue
            ev = e["evaluation"]
            results[cid] = {"ok": True, "total": ev["total"], "critical": ev["critical_error"],
                            "answers": {c["criterion_id"]: _norm(c["answer"]) for c in ev["criteria"]},
                            "calls": ev["llm_calls"], "cost_usd": ev["cost_usd"], "sec": 0}
        report(results, key, checklist)
        return
    if a.report:
        report(json.loads(a.report.read_text("utf-8")), key, checklist)
        return
    ids = a.ids.split(",") if a.ids else list(key)
    RUNS.mkdir(parents=True, exist_ok=True)
    # секунды и режим опроса в имени: параллельные прогоны не пишут в один файл
    vote = f"vote{settings.RESAMPLE}" if settings.RESAMPLE else "votecl"
    out = a.resume or RUNS / f"{datetime.now():%Y%m%d-%H%M%S}_{settings.MODEL.replace('/', '_')}_{vote}.json"
    print(f"Модель: {settings.MODEL}; резерв: {settings.FALLBACK_MODELS or 'нет'}; RPM: {settings.RPM or '∞'}; "
          f"повторный опрос: {settings.RESAMPLE or 'как в чек-листе'}\nРезультаты: {out}", flush=True)
    results = run(ids, out, checklist, {k: key[k] for k in ids})
    report(results, {k: key[k] for k in ids}, checklist)


if __name__ == "__main__":
    main()
