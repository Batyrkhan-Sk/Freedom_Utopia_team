import json

from backend.config import settings
from backend.export import build_document, error_result, model_info, to_result, validate_document
from backend.models import Checklist
from backend.scoring import score

CL = Checklist.model_validate(json.loads(settings.DEFAULT_CHECKLIST.read_text("utf-8")))

# Пример из приложения (стр. 2): итог 85.38
APPENDIX = {"c01_contact": "yes", "c02_needs": "na", "c03_identification": "na", "c04_consultation": 15,
            "c05_empathy": "yes", "c06_dialog_management": 7, "c07_closing": "yes", "c08_critical_errors": "no"}


def fake_eval(answers=APPENDIX, cid="demo-001", evidence=(3,)):
    sc = score(CL, answers)
    return {"conversation_id": cid, "total": sc["total"], "critical_error": sc["critical_error"],
            "criteria": [{"criterion_id": k, "answer": v, "explanation": "x", "evidence_msg_ids": list(evidence)}
                         for k, v in answers.items()]}


def test_appendix_example_total_and_values():
    r = to_result(fake_eval(), CL)
    assert r["score"] == {"final_score": 85.38, "critical_flag": False}
    vals = {c["criterion_id"]: c["value"] for c in r["criteria"]}
    assert vals == {"contact": True, "discovery": None, "identification": None, "consultation": 15,
                    "empathy": True, "dialogue": 7, "closure": True, "critical": False}


def test_evidence_turns_are_one_based():
    r = to_result(fake_eval(evidence=(0, 4, 4)), CL)
    assert r["criteria"][0]["evidence_turns"] == [1, 5]


def test_document_with_error_entry_is_valid():
    doc = build_document([to_result(fake_eval(), CL), error_result("demo-002", "Не удалось получить ответ модели.")],
                         ["groq/openai/gpt-oss-120b"], "Team")
    assert doc["model"] == {"mode": "external_api", "name": "groq/openai/gpt-oss-120b"}
    assert validate_document(doc, ["demo-001", "demo-002"], {"demo-001": 10}) == []


def test_validator_catches_problems():
    good = to_result(fake_eval(), CL)
    bad = json.loads(json.dumps(good))
    bad["criteria"][0]["value"] = 1                      # contact: число вместо true
    bad["criteria"][5]["value"] = 5                      # dialogue: нет такой ступени
    bad["criteria"][1]["evidence_turns"] = [0]           # нумерация с 1
    bad["score"]["critical_flag"] = True                 # не совпадает с critical
    doc = build_document([bad, good], ["m"], "Team")
    errs = "\n".join(validate_document(doc, ["demo-001", "demo-003"], {"demo-001": 10}))
    for needle in ("contact: недопустимое value 1", "dialogue: недопустимое value 5", "evidence_turns [0]",
                   "critical_flag", "повторяющиеся", "нет записей", "demo-003"):
        assert needle in errs, needle


def test_true_is_not_accepted_as_scale_value():
    r = to_result(fake_eval(), CL)
    r["criteria"][3]["value"] = True                     # consultation
    errs = validate_document(build_document([r], ["m"], "T"))
    assert any("consultation" in e for e in errs)


def test_model_mode_detection():
    assert model_info(["ollama/gemma4"])["mode"] == "local"
    assert model_info(["ollama/gemma4", "groq/x"]) == {"mode": "hybrid", "name": "groq/x, ollama/gemma4"}


def test_missing_action_uses_checked_range_as_evidence():
    ev = fake_eval(evidence=())
    ev["criteria"][1]["checked_range"] = [9, 11]
    r = to_result(ev, CL)
    assert r["criteria"][1]["evidence_turns"] == [10, 11, 12] and r["criteria"][0]["evidence_turns"] == []
