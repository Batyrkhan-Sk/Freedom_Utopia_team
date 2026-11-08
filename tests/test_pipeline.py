import json

from backend.evaluator import evaluate
from backend.facts import compute_facts, build_messages, parse_ts
from backend.llm import LLMResponse

CONV = {
    "conversation_id": "t-001", "channel": "agent chat", "language": "RU", "audio_url": None,
    "transcript": [
        {"timestamp": "00:02", "speaker": "client", "text": "Где посмотреть мой тариф?"},
        {"timestamp": "00:03", "speaker": "bot", "text": "Здравствуйте! Я AIM..."},
        {"timestamp": "00:20", "speaker": "client", "text": "Позовите оператора"},
        {"timestamp": "00:21", "speaker": "system", "text": "Эскалация на оператора"},
        {"timestamp": "00:58", "speaker": "operator", "text": "Здравствуйте! Меня зовут Алина"},
        {"timestamp": "01:10", "speaker": "operator", "text": "Тариф в разделе «Профиль». Мне жаль, что пришлось искать."},
        {"timestamp": "01:30", "speaker": "client", "text": "Спасибо"},
        {"timestamp": "01:35", "speaker": "operator", "text": "Пожалуйста. Хорошего вечера!"},
    ],
}


def ans(cid, answer, ev=None, rng=None, na=None, conf=0.9):
    return {"criterion_id": cid, "explanation": "тест", "evidence_msg_ids": ev or [], "checked_range": rng,
            "na_reason": na, "answer": answer, "confidence": conf}


GOOD = {"answers": [
    ans("c01_contact", "yes", [4]), ans("c02_needs", "na", na="данных хватало"),
    ans("c03_identification", "na", na="общий вопрос"), ans("c04_consultation", 20, [5]),
    ans("c05_empathy", "yes", [5]), ans("c06_dialog_management", 10, [4]),
    ans("c07_closing", "no", [7], rng=[4, 7]), ans("c08_critical_errors", "no", rng=[0, 7]),
]}


class FakeLLM:
    """Отвечает заранее заданными текстами по очереди; считает вызовы."""
    def __init__(self, *texts):
        self.texts, self.calls = list(texts), 0

    def __call__(self, messages, temperature=0.0):
        t = self.texts[min(self.calls, len(self.texts) - 1)]
        self.calls += 1
        return LLMResponse(t, 1000, 300, 0.01, "fake")


def test_facts():
    f = compute_facts(build_messages(CONV["transcript"]))
    assert f["first_operator_idx"] == 4
    assert f["first_reaction_sec"] == 37          # 00:58 - 00:21
    assert f["reaction_ref"] == "escalation"
    assert f["reaction_exceeded_threshold"] is False


def test_parse_call_timestamp_7_digits():
    assert parse_ts("2026-01-01T10:00:01.1234567+05:00") - parse_ts("2026-01-01T10:00:00.0000000+05:00") > 1.12


def test_pipeline_happy_path(checklist, monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "RESAMPLE", "")   # как в чек-листе, независимо от CQA_RESAMPLE в .env
    llm = FakeLLM(json.dumps(GOOD))
    ev = evaluate(CONV, checklist, llm=llm)
    assert ev["total"] == 92.31
    assert llm.calls == 1 + 2 * 2        # 1 основной + по 2 повтора для c04 и c06
    assert ev["tokens"]["in"] == 5000 and ev["cost_usd"] == 0.05   # считаем ВСЕ вызовы


def test_retry_on_bad_evidence(checklist):
    bad = json.loads(json.dumps(GOOD))
    bad["answers"][0]["evidence_msg_ids"] = [99]   # такой реплики нет
    llm = FakeLLM(json.dumps(bad), json.dumps(GOOD))
    ev = evaluate(CONV, checklist, llm=llm)
    assert ev["total"] == 92.31 and llm.calls >= 2


def test_single_element_range_accepted(checklist):
    # Локальные модели пишут участок из одной реплики как [12] вместо [12, 12] - это не ошибка
    one = json.loads(json.dumps(GOOD))
    one["answers"][0]["checked_range"] = [3]
    llm = FakeLLM(json.dumps(one))
    ev = evaluate(CONV, checklist, llm=llm)
    c = next(x for x in ev["criteria"] if x["criterion_id"] == one["answers"][0]["criterion_id"])
    assert c["checked_range"] == [3, 3]
