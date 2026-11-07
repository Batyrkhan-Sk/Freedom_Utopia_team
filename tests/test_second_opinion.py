"""Второе мнение по грубым ошибкам: вторая модель перепроверяет только флаг «yes»."""
import json

import pytest

from backend.config import settings
from backend.evaluator import evaluate
from backend.llm import LLMResponse
from tests.test_pipeline import CONV, GOOD, ans

CRIT_YES = {"answers": [a if a["criterion_id"] != "c08_critical_errors"
                        else ans("c08_critical_errors", "yes", [6], conf=0.8) for a in GOOD["answers"]]}


class Fake:
    def __init__(self, text, model, fail=False):
        self.text, self.model, self.fail, self.calls = text, model, fail, 0

    def __call__(self, messages, temperature=0.0):
        self.calls += 1
        if self.fail:
            raise RuntimeError("verifier down")
        return LLMResponse(self.text, 1000, 300, 0.02, self.model)


def one(cid, answer, conf=0.9):
    return json.dumps({"answers": [ans(cid, answer, [6] if answer == "yes" else [], rng=None if answer == "yes" else [0, 7],
                                       conf=conf)]})


@pytest.fixture(autouse=True)
def _no_resample(monkeypatch):
    monkeypatch.setattr(settings, "RESAMPLE", "1")


def crit(ev):
    return next(c for c in ev["criteria"] if c["criterion_id"] == "c08_critical_errors")


def test_confirmed_keeps_critical(checklist):
    main, ver = Fake(json.dumps(CRIT_YES), "haiku"), Fake(one("c08_critical_errors", "yes", 0.95), "sonnet")
    ev = evaluate(CONV, checklist, llm=main, verify_llm=ver)
    c = crit(ev)
    assert ev["critical_error"] and c["answer"] == "yes" and c["second_opinion"]["status"] == "confirmed"
    assert ver.calls == 1 and ev["verify_model"] == "sonnet" and ev["model"] == "haiku"
    assert c["confidence"] == 0.95 and not c["needs_review"]


def test_disputed_uses_second_model_and_flags_review(checklist):
    main, ver = Fake(json.dumps(CRIT_YES), "haiku"), Fake(one("c08_critical_errors", "no"), "sonnet")
    ev = evaluate(CONV, checklist, llm=main, verify_llm=ver)
    c = crit(ev)
    assert not ev["critical_error"] and c["answer"] == "no" and c["needs_review"] and ev["needs_review"]
    assert c["second_opinion"]["status"] == "disputed" and c["second_opinion"]["first_answer"] == "yes"


def test_verifier_failure_keeps_first_answer(checklist):
    main, ver = Fake(json.dumps(CRIT_YES), "haiku"), Fake("", "sonnet", fail=True)
    ev = evaluate(CONV, checklist, llm=main, verify_llm=ver)
    assert ev["critical_error"] and crit(ev)["second_opinion"]["status"] == "failed"


def test_no_critical_no_second_call(checklist):
    main, ver = Fake(json.dumps(GOOD), "haiku"), Fake(one("c08_critical_errors", "yes"), "sonnet")
    ev = evaluate(CONV, checklist, llm=main, verify_llm=ver)
    assert ver.calls == 0 and "second_opinion" not in crit(ev) and ev["verify_model"] is None
