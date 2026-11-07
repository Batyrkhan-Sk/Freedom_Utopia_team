"""Повторы и переключение модели без реальных вызовов: подменяем _call и sleep."""
import pytest

from backend import llm
from backend.config import settings


class RateLimitError(Exception): ...
class ServiceUnavailableError(Exception): ...
class PaymentRequiredError(Exception): ...
class AuthenticationError(Exception): ...


class _Resp:
    class usage: prompt_tokens, completion_tokens = 10, 5
    class _Msg: content = '{"answers": []}'
    class _Choice: pass
    choices = [_Choice()]
_Resp._Choice.message = _Resp._Msg


@pytest.fixture
def fake(monkeypatch):
    calls = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: calls.append(("sleep", s)))
    monkeypatch.setattr(settings, "RPM", 0)
    monkeypatch.setattr(settings, "MODEL", "gemini/main")
    monkeypatch.setattr(settings, "FALLBACK_MODELS", ["groq/backup"])
    monkeypatch.setattr(llm, "_cost", lambda *a: 0.0)

    def install(*outcomes):
        seq = list(outcomes)
        def _call(model, messages, temperature):
            calls.append(("call", model))
            o = seq.pop(0)
            if isinstance(o, Exception):
                raise o
            return _Resp()
        monkeypatch.setattr(llm, "_call", _call)
        return calls
    return install


def test_429_waits_provider_delay_then_succeeds(fake):
    calls = fake(RateLimitError("Please retry in 5.6s."), "ok")
    r = llm.complete([])
    assert r.model == "gemini/main" and ("sleep", 6.6) in calls


def test_two_503_switch_to_fallback_model(fake):
    calls = fake(ServiceUnavailableError("high demand"), ServiceUnavailableError("high demand"), "ok")
    r = llm.complete([])
    assert r.model == "groq/backup"
    assert [c[1] for c in calls if c[0] == "call"] == ["gemini/main", "gemini/main", "groq/backup"]


def test_402_switches_model_immediately(fake):
    fake(PaymentRequiredError("prepayment credits are depleted"), "ok")
    assert llm.complete([]).model == "groq/backup"


def test_auth_error_is_not_retried(fake):
    fake(AuthenticationError("bad key"))
    with pytest.raises(AuthenticationError):
        llm.complete([])


def test_claude_gets_no_temperature():
    assert not llm._supports_temperature("anthropic/claude-opus-5-5")
    assert llm._supports_temperature("gemini/gemini-3.5-flash")
