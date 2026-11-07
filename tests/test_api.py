"""API для интерфейса: проверка чек-листа, переоценка изменённых критериев, апелляции, список и карточка.
Временная база и подменённая модель: реальных вызовов ИИ нет."""
import json

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.checklists import check_checklist, diff_checklists
from backend.config import settings
from backend.evaluator import evaluate as real_evaluate
from backend.reevaluate import reevaluate_conversation as real_reevaluate
from tests.test_pipeline import CONV, GOOD, FakeLLM


def edited(cl, **changes):
    """Копия чек-листа с правками по критериям: {"c06_dialog_management": {"weight": 10}}."""
    new = cl.model_copy(deep=True)
    for cid, fields in changes.items():
        c = new.criterion(cid)
        for k, v in fields.items():
            setattr(c, k, v)
    return new


# ---------- проверка и сравнение чек-листов ----------
def test_default_checklist_passes_check(checklist):
    assert check_checklist(checklist) == []


def test_check_checklist_finds_problems(checklist):
    bad = edited(checklist, c01_contact={"weight": 20}, c08_critical_errors={"weight": 5},
                 c04_consultation={"scale": [20]}, c05_empathy={"scale": [0, 1]},
                 c07_closing={"section": "nowhere", "code": "contact"})
    bad.criteria[1].id = "c01_contact"
    errs = "\n".join(check_checklist(bad))
    for needle in ("Сумма весов", "флаг", "2 разных", "только для типа scale", "nowhere",
                   "Повторяющиеся id", "Повторяющиеся code"):
        assert needle in errs, needle


def test_diff_weight_only_vs_changed(checklist):
    d = diff_checklists(checklist, edited(checklist, c06_dialog_management={"weight": 10}, c07_closing={"weight": 10}))
    assert d["weight_only"] == ["c06_dialog_management", "c07_closing"] and d["changed"] == []
    d = diff_checklists(checklist, edited(checklist, c04_consultation={"instruction": "Новая формулировка"}))
    assert d["changed"] == ["c04_consultation"] and not d["rules_changed"]
    new = checklist.model_copy(deep=True)
    new.rules += " Новое правило."
    assert diff_checklists(checklist, new)["changed"] == [c.id for c in checklist.criteria]


def test_diff_added_and_removed(checklist):
    new = checklist.model_copy(deep=True)
    removed = new.criteria.pop(4)
    added = removed.model_copy(update={"id": "c09_new"})
    new.criteria.append(added)
    d = diff_checklists(checklist, new)
    assert d["added"] == ["c09_new"] and d["removed"] == [removed.id]


# ---------- переоценка только изменённого ----------
def test_reevaluate_weight_only_needs_no_llm(checklist):
    prev = real_evaluate(CONV, checklist, llm=FakeLLM(json.dumps(GOOD)))
    assert prev["total"] == 92.31
    new_cl = edited(checklist, c06_dialog_management={"weight": 10}, c07_closing={"weight": 10})
    llm = FakeLLM(json.dumps(GOOD))
    ev, row = real_reevaluate(CONV, prev, checklist, new_cl, llm=llm)
    assert llm.calls == 0 and ev["asked_criteria"] == []
    assert ev["total"] == 84.62 and row["old_total"] == 92.31 and row["new_total"] == 84.62
    assert row["changed_answers"] == [] and all(c["reused"] for c in ev["criteria"])


def test_reevaluate_changed_criterion_only(checklist, monkeypatch):
    monkeypatch.setattr(settings, "RESAMPLE", "1")
    prev = real_evaluate(CONV, checklist, llm=FakeLLM(json.dumps(GOOD)))
    new_cl = edited(checklist, c04_consultation={"instruction": "Оцени только полноту ответа."})
    changed = json.loads(json.dumps(GOOD))
    changed["answers"][3]["answer"] = 15                       # c04 теперь 15
    seen = []

    class Spy(FakeLLM):
        def __call__(self, messages, temperature=0.0):
            seen.append(" ".join(m["content"] for m in messages))   # критерии - в системном сообщении
            return super().__call__(messages, temperature)

    llm = Spy(json.dumps(changed))
    ev, row = real_reevaluate(CONV, prev, checklist, new_cl, llm=llm)
    assert llm.calls == 1 and ev["asked_criteria"] == ["c04_consultation"]
    assert "c04_consultation" in seen[0] and "c01_contact" not in seen[0]
    assert row["changed_answers"] == [{"criterion_id": "c04_consultation", "old": 20.0, "new": 15.0}]
    reused = {c["criterion_id"]: c["reused"] for c in ev["criteria"]}
    assert reused["c04_consultation"] is False and reused["c01_contact"] is True


# ---------- API целиком ----------
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "api.sqlite"))
    monkeypatch.setattr(settings, "RESAMPLE", "1")
    fake = FakeLLM(json.dumps(GOOD))
    monkeypatch.setattr(main, "evaluate", lambda conv, cl: real_evaluate(conv, cl, llm=fake))
    monkeypatch.setattr(main, "reevaluate_conversation",
                        lambda conv, prev, old, new: real_reevaluate(conv, prev, old, new, llm=fake))
    main.JOBS.clear()
    with TestClient(main.app) as c:
        c.post("/conversations", json=CONV)
        yield c


def test_list_and_card(client):
    assert client.get("/conversations").json()[0]["total"] is None          # ещё не оценён
    ev = client.post("/evaluate/t-001").json()
    items = client.get("/conversations", params={"critical": False, "language": "ru"}).json()
    assert len(items) == 1 and items[0]["total"] == 92.31 and items[0]["evaluation_id"] == ev["evaluation_id"]
    assert items[0]["open_appeals"] == 0 and items[0]["checklist_version"] == 1 and items[0]["model"] == "fake"
    assert client.get("/conversations", params={"critical": True}).json() == []
    assert client.get("/conversations", params={"q": "nope"}).json() == []
    card = client.get("/conversations/t-001").json()
    conv = card["conversation"]
    assert conv["has_audio"] is False and "audio_url" not in conv and conv["group_id"] == "upload"
    assert conv["messages"][4] == {"idx": 4, "ts": "00:58", "t": 56.0, "speaker": "operator",
                                   "text": "Здравствуйте! Меня зовут Алина"}
    assert card["evaluation"]["total"] == 92.31 and card["appeals"] == []


def test_appeal_change_updates_evaluation(client):
    ev = client.post("/evaluate/t-001").json()
    aid = client.post("/appeals", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "c07_closing",
                                        "comment": "Оператор попрощался и подвёл итог"}).json()["appeal_id"]
    assert client.get("/conversations", params={"has_open_appeal": True}).json()[0]["open_appeals"] == 1
    res = client.post(f"/appeals/{aid}/decision", json={"new_answer": "yes", "reason": "Итог подведён"}).json()
    upd = res["evaluation"]
    assert res["changed"] and upd["total"] == 100.0
    c07 = next(c for c in upd["criteria"] if c["criterion_id"] == "c07_closing")
    assert c07["answer"] == "yes" and c07["original_answer"] == "no" and c07["points"] == 5.0
    assert upd["appeal_history"][0]["old"] == "no" and upd["appeal_history"][0]["new"] == "yes"
    assert client.get("/conversations/t-001").json()["evaluation"]["total"] == 100.0
    rows = client.get("/appeals", params={"status": "decided"}).json()
    assert rows[0]["conversation_id"] == "t-001" and rows[0]["decision"]["new_answer"] == "yes"
    assert client.get("/appeals", params={"status": "open"}).json() == []
    assert client.get("/conversations/t-001").json()["appeals"][0]["status"] == "decided"


def test_appeal_confirm_keeps_total(client):
    ev = client.post("/evaluate/t-001").json()
    aid = client.post("/appeals", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "c01_contact",
                                        "comment": "Не согласен"}).json()["appeal_id"]
    res = client.post(f"/appeals/{aid}/decision", json={"new_answer": "yes", "reason": "Оценка верна"}).json()
    assert not res["changed"] and res["evaluation"]["total"] == 92.31
    c01 = next(c for c in res["evaluation"]["criteria"] if c["criterion_id"] == "c01_contact")
    assert c01["original_answer"] == "yes"
    assert client.get(f"/appeals/{aid}").json()["status"] == "decided"


def test_save_checklist_validation_and_diff(client):
    cl = client.get("/checklists/chat-quality").json()
    bad = json.loads(json.dumps(cl))
    bad["criteria"][0]["weight"] = 30
    r = client.post("/checklists", json=bad)
    assert r.status_code == 422 and any("Сумма весов" in e for e in r.json()["detail"])
    good = json.loads(json.dumps(cl))
    good["criteria"][5]["weight"], good["criteria"][6]["weight"] = 10, 10
    v = client.post("/checklists", json=good).json()["version"]
    assert v == 2
    d = client.get("/checklists/chat-quality/diff", params={"from_version": 1, "to_version": 2}).json()
    assert d["weight_only"] == ["c06_dialog_management", "c07_closing"] and d["changed"] == []


def test_reevaluate_job_rescores_without_llm(client):
    client.post("/evaluate/t-001")
    cl = client.get("/checklists/chat-quality").json()
    cl["criteria"][5]["weight"], cl["criteria"][6]["weight"] = 10, 10
    v = client.post("/checklists", json=cl).json()["version"]
    job = client.post("/reevaluate", json={"checklist_id": "chat-quality", "version": v}).json()
    assert job["total"] == 1
    st = client.get(f"/jobs/{job['job_id']}").json()                    # BackgroundTasks выполнены после ответа
    assert st["status"] == "finished" and st["failed"] == []
    row = st["diffs"][0]
    assert row["old_total"] == 92.31 and row["new_total"] == 84.62 and row["asked_criteria"] == []
    item = client.get("/conversations").json()[0]
    assert item["total"] == 84.62 and item["checklist_version"] == v and item["evaluation_id"] == row["evaluation_id"]


def test_root_redirect(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] in ("/ui/", "/docs")


def _weight_only_change(client):
    cl = client.get("/checklists/chat-quality").json()
    w = {c["id"]: c for c in cl["criteria"]}
    w["c01_contact"]["weight"], w["c02_needs"]["weight"] = 15, 10      # сумма остаётся 100
    v = client.post("/checklists", json=cl).json()["version"]
    job = client.post("/reevaluate", json={"checklist_id": "chat-quality", "version": v}).json()
    return client.get(f"/jobs/{job['job_id']}").json()


def test_appeal_decision_survives_reevaluation(client):
    ev = client.post("/evaluate/t-001").json()
    aid = client.post("/appeals", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "c07_closing",
                                        "comment": "Итог был"}).json()["appeal_id"]
    client.post(f"/appeals/{aid}/decision", json={"new_answer": "yes", "reason": "Итог подведён в [9]"})
    assert _weight_only_change(client)["diffs"][0]["asked_criteria"] == []
    new = client.get("/conversations/t-001").json()["evaluation"]
    c07 = next(c for c in new["criteria"] if c["criterion_id"] == "c07_closing")
    assert c07["answer"] == "yes" and c07["original_answer"] == "no" and c07["reused"]
    assert c07["explanation"] == "Решение проверяющего: Итог подведён в [9]" and c07["ai_explanation"]
    assert new["appeal_history"][0]["appeal_id"] == aid


def test_open_appeal_moves_to_latest_evaluation(client):
    ev = client.post("/evaluate/t-001").json()
    aid = client.post("/appeals", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "c07_closing",
                                        "comment": "Итог был"}).json()["appeal_id"]
    new_id = _weight_only_change(client)["diffs"][0]["evaluation_id"]
    assert new_id != ev["evaluation_id"]
    assert client.get(f"/appeals/{aid}").json()["evaluation_id"] == new_id
    res = client.post(f"/appeals/{aid}/decision", json={"new_answer": "yes", "reason": "ok"}).json()
    assert client.get("/conversations/t-001").json()["evaluation"]["evaluation_id"] == new_id
    assert client.get("/conversations/t-001").json()["evaluation"]["total"] == res["evaluation"]["total"]


def test_upload_keeps_group_id(client):
    rec = json.loads(json.dumps(CONV)); rec["conversation_id"] = "t-dev"
    client.post("/conversations", params={"group_id": "DEV"}, json=rec)
    assert client.get("/conversations/t-dev").json()["conversation"]["group_id"] == "DEV"


def test_format_warnings_on_code_rules(checklist):
    from backend.checklists import format_warnings
    assert format_warnings(checklist) == []                                  # стартовый чек-лист в формате
    swapped = edited(checklist, c05_empathy={"na_allowed": True})
    assert any("empathy" in w and "N/A" in w for w in format_warnings(swapped))
    renamed = edited(checklist, c06_dialog_management={"code": "custom"})
    w = format_warnings(renamed)
    assert any("custom" in x for x in w) and any("dialogue" in x for x in w)
