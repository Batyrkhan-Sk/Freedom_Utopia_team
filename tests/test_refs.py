from backend.refs import human_refs, humanize_evaluation


def test_refs_shift_to_one_based():
    assert human_refs("данные в [62] и [68]", 80) == "данные в [63] и [69]"
    assert human_refs("участок [10–12], реплики [9, 11]", 20) == "участок [11–13], реплики [10, 12]"
    assert human_refs("ссылка [500] не реплика", 20) == "ссылка [500] не реплика"
    assert human_refs("без ссылок", 20) == "без ссылок" and human_refs(None, 5) is None


def test_reviewer_text_untouched_and_original_kept():
    ev = {"criteria": [{"explanation": "Решение проверяющего: итог в [9]", "ai_explanation": "нет итога в [9]",
                        "na_reason": None},
                       {"explanation": "Оператор в [9] представился", "na_reason": "общий вопрос [0]"}]}
    out = humanize_evaluation(ev, 12)
    assert out["criteria"][0]["explanation"] == "Решение проверяющего: итог в [9]"
    assert out["criteria"][0]["ai_explanation"] == "нет итога в [10]"
    assert out["criteria"][1]["explanation"] == "Оператор в [10] представился"
    assert out["criteria"][1]["na_reason"] == "общий вопрос [1]"
    assert ev["criteria"][1]["explanation"] == "Оператор в [9] представился"      # хранимая оценка не меняется
