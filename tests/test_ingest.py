import pytest

from backend.ingest import extract_audio_src, parse_pasted_text, validate_record

# Формат реальной страницы плеера организаторов: ссылка без ".mp3", с &amp; в параметрах
PLAYER_PAGE = """<audio id="myAudio" controls style="width:100%;">
  <source src="https://example.crm11.dynamics.com/api/filedownload/1015/0F/55/abc/def?sp=r&amp;st=2026-11-07T07:21:26Z&amp;sig=xyz%3D&amp;rsct=audio%2Fmpeg" type="audio/mpeg">
</audio>"""


def test_extract_audio_src_from_real_player_format():
    src = extract_audio_src(PLAYER_PAGE)
    assert src.startswith("https://example.crm11.dynamics.com/api/filedownload/")
    assert "&amp;" not in src and "&st=" in src and src.endswith("rsct=audio%2Fmpeg")


def test_extract_audio_src_missing():
    assert extract_audio_src("<html><body>нет плеера</body></html>") is None


def test_parse_pasted_text_ru_roles_and_times():
    rec = parse_pasted_text("00:00 клиент: Где мой тариф?\n\n01:15 оператор: Здравствуйте! Меня зовут Алина.")
    assert [m["speaker"] for m in rec["transcript"]] == ["client", "operator"]
    assert rec["transcript"][1]["timestamp"] == "01:15"
    assert rec["language"] == "RU" and rec["channel"] == "agent chat"


def test_parse_pasted_text_without_times():
    rec = parse_pasted_text("client: Hi\noperator: Hello, I'm Maya")
    assert [m["timestamp"] for m in rec["transcript"]] == ["00:00", "00:10"]
    assert rec["language"] == "EN"


def test_parse_pasted_text_bad_role():
    with pytest.raises(ValueError, match="строка 1"):
        parse_pasted_text("manager: привет")


def test_validate_record_rejects_bad_speaker():
    with pytest.raises(ValueError):
        validate_record({"transcript": [{"timestamp": "00:00", "speaker": "boss", "text": "x"}]})
