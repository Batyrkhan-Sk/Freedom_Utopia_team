from backend.appeals import apply_decision
from backend.scoring import round_half_up, score

# Пример p1-ru-001 из документа: 60 / 65 * 100 = 92.31
BASE = {"c01_contact": "yes", "c02_needs": "na", "c03_identification": "na", "c04_consultation": 20.0,
        "c05_empathy": "yes", "c06_dialog_management": 10.0, "c07_closing": "no", "c08_critical_errors": "no"}


def test_example_p1_ru_001(checklist):
    r = score(checklist, BASE)
    assert r["total"] == 92.31
    assert r["critical_error"] is False
    assert r["criteria"]["c07_closing"]["effective_weight"] == 7.69   # 5 / 65 * 100


def test_scale_points(checklist):
    r = score(checklist, {**BASE, "c06_dialog_management": 7.0})
    assert r["criteria"]["c06_dialog_management"]["points"] == 10.5   # 7 / 10 * 15
    r = score(checklist, {**BASE, "c06_dialog_management": 4.0})
    assert r["criteria"]["c06_dialog_management"]["points"] == 6.0


def test_na_redistribution_all_yes(checklist):
    ans = {**BASE, "c07_closing": "yes"}
    assert score(checklist, ans)["total"] == 100.0


def test_no_applicable_total_is_none(checklist):
    ans = {"c01_contact": "na", "c02_needs": "na", "c03_identification": "na", "c04_consultation": "na",
           "c05_empathy": "na", "c06_dialog_management": "na", "c07_closing": "na", "c08_critical_errors": "no"}
    assert score(checklist, ans)["total"] is None


def test_flag_does_not_zero_total(checklist):
    r = score(checklist, {**BASE, "c08_critical_errors": "yes"})
    assert r["critical_error"] is True and r["total"] == 92.31


def test_round_half_up():
    assert round_half_up(2.675, 2) == 2.68   # float-округление дало бы 2.67


def test_appeal_recalculates(checklist):
    ev = {"total": 92.31, "criteria": [{"criterion_id": k, "answer": v} for k, v in BASE.items()]}
    res = apply_decision(ev, checklist, "c07_closing", "yes", "Оператор предложил помощь в [10]")
    assert res["original_total"] == 92.31 and res["final_total"] == 100.0 and res["changed"]


# ---- Эталон: итоги по 21 диалогу из data/answer_key/draft.json ----
import json  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

_KEY = json.loads((Path(__file__).resolve().parent.parent / "data/answer_key/draft.json").read_text("utf-8"))


@pytest.mark.parametrize("cid", list(_KEY["conversations"]))
def test_answer_key_totals(checklist, cid):
    conv = _KEY["conversations"][cid]
    r = score(checklist, {k: v["answer"] for k, v in conv["criteria"].items()})
    assert r["total"] == conv["expected_total"]
    assert r["critical_error"] == conv["critical_error"]
