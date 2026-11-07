"""Все 26 типов системных сообщений из DEV должны распознаваться. Новое сообщение в данных -> добавить сюда."""
import pytest

from backend.facts import build_messages, compute_facts, detect_event, detect_language

SYSTEM_MESSAGES = [
    ("Эскалация на оператора.", "escalation"),
    ("Чат операторға жіберілді.", "escalation"),
    ("Chat transferred to an agent.", "escalation"),
    ("Клиент переведен от агента на представителя службы поддержки", "escalation"),
    ("Агент присоединился к разговору с клиентом.", "operator_joined"),
    ("Клиент завершил разговор.", "client_ended_call"),
    ("Bot dialog and transcription languages have been updated. The dialog language changed from kk-KZ to ru-RU.", "language_changed"),
    ("Запись и транскрибирование запущены.", "recording_started"),
    ("Жазу және транскрипция басталды.", "recording_started"),
    ("Запись и транскрибирование приостановлены.", "recording_paused"),
    ("Запись и транскрибирование возобновлены.", "recording_resumed"),
    ("Оператор закрыл чат.", "chat_closed_by_operator"),
    ("Оператор чатты жапты.", "chat_closed_by_operator"),
    ("Agent closed the chat.", "chat_closed_by_operator"),
    ("Личность клиента подтверждена.", "identity_confirmed"),
    ("Клиенттің жеке басы расталды.", "identity_confirmed"),
    ("Customer identity verified.", "identity_confirmed"),
    ("Соединение с клиентом прервано.", "connection_lost"),
    ("Клиентпен байланыс үзілді.", "connection_lost"),
    ("Connection to customer lost.", "connection_lost"),
    ("Клиент покинул чат.", "client_left"),
    ("Инициирована консультация с агентом.", "consultation"),
    ("Началась консультация с агентом.", "consultation"),
    ("Клиент находится на удержании.", "hold_started"),
    ("Клиент больше не находится на удержании.", "hold_ended"),
    ("Агент вышел из разговора.", "operator_left"),
]


@pytest.mark.parametrize("text,expected", SYSTEM_MESSAGES)
def test_every_dev_system_message_is_detected(text, expected):
    assert detect_event(text) == expected


@pytest.mark.parametrize("text,lang", [
    ("Сәлеметсіз бе, менің атым Аружан.", "KZ"),
    ("Здравствуйте! Меня зовут Алина.", "RU"),
    ("Hi, I'm Maya from support.", "EN"),
    ("12345", None),
])
def test_language_detection(text, lang):
    assert detect_language(text) == lang


def _msgs(*rows):
    return build_messages([{"timestamp": t, "speaker": s, "text": x} for t, s, x in rows])


def test_identity_and_end_event_facts():
    f = compute_facts(_msgs(
        ("00:00", "client", "Пополнила, денег нет"),
        ("00:05", "system", "Эскалация на оператора."),
        ("00:40", "operator", "Здравствуйте, меня зовут Роман."),
        ("00:50", "system", "Личность клиента подтверждена."),
        ("01:00", "client", "Ок, это всё"),
        ("01:18", "system", "Соединение с клиентом прервано."),
    ))
    assert f["identity_confirmed_by_system"] and f["identity_confirmed_idx"] == 3
    assert f["first_reaction_sec"] == 35 and f["reaction_ref"] == "escalation"
    assert f["end_event"] == "connection_lost" and f["end_after_last_client_sec"] == 18
    assert f["client_language"] == "RU" and f["first_operator_language"] == "RU"


def test_kz_escalation_is_reaction_reference():
    f = compute_facts(_msgs(
        ("00:00", "client", "Тариф қайдан көремін?"),
        ("00:38", "system", "Чат операторға жіберілді."),
        ("01:15", "operator", "Сәлеметсіз бе, менің атым Аружан."),
    ))
    assert f["reaction_ref"] == "escalation" and f["first_reaction_sec"] == 37
    assert f["client_language"] == "KZ"
