"""Все настройки в одном месте. Меняются переменными окружения или файлом .env в корне, код не трогаем."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Простой загрузчик .env: KEY=VALUE, без перезаписи уже заданных переменных."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")
# LiteLLM ждёт ANTHROPIC_API_KEY; поддерживаем и наше имя CLAUDE_API_KEY
if os.getenv("CLAUDE_API_KEY") and not os.getenv("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = os.environ["CLAUDE_API_KEY"]


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


class Settings:
    # Модель в формате LiteLLM: "gemini/...", "groq/...", "anthropic/...", "openai/...", "ollama/..."
    MODEL = os.getenv("CQA_MODEL", "gemini/gemini-3.5-flash")
    FALLBACK_MODELS = _list("CQA_FALLBACK_MODELS")          # по очереди при 429/503/таймаутах
    RPM = float(os.getenv("CQA_RPM", "0"))                  # лимит запросов в минуту, 0 = без ограничения
    LLM_RETRIES = int(os.getenv("CQA_LLM_RETRIES", "6"))    # повторы при сетевых ошибках и лимитах
    LLM_TIMEOUT = float(os.getenv("CQA_LLM_TIMEOUT", "120"))
    LOCAL_TIMEOUT = float(os.getenv("CQA_LOCAL_TIMEOUT", "900"))   # локальная модель на ноутбуке медленнее
    NUM_CTX = int(os.getenv("CQA_NUM_CTX", "8192"))              # окно контекста для Ollama (промпт ~5K)
    LOCAL_THINK = os.getenv("CQA_LOCAL_THINK", "0") == "1"       # режим рассуждений локальной модели
    REASONING_EFFORT = os.getenv("CQA_REASONING_EFFORT", "")  # low/medium/high, если модель поддерживает
    JSON_MODE = os.getenv("CQA_JSON_MODE", "1") == "1"
    MAX_TOKENS = int(os.getenv("CQA_MAX_TOKENS", "8000"))   # модели с рассуждением тратят токены до ответа
    MAX_RETRIES = int(os.getenv("CQA_MAX_RETRIES", "2"))      # повторы при невалидном ответе
    # Повторный опрос: "" - как в чек-листе; "1" - выключен (дешевле, для настройки); "3" - принудительно
    RESAMPLE = os.getenv("CQA_RESAMPLE", "")
    VOTE_TEMPERATURE = float(os.getenv("CQA_VOTE_TEMPERATURE", "0.7"))  # для повторного опроса
    LOW_CONFIDENCE = float(os.getenv("CQA_LOW_CONFIDENCE", "0.7"))      # ниже -> "нужна проверка"
    # Второе мнение по грубым ошибкам: если основная модель нашла грубую ошибку, её перепроверяет эта модель
    # (например anthropic/claude-sonnet-5-5). Пусто - выключено.
    VERIFY_MODEL = os.getenv("CQA_VERIFY_MODEL", "")
    VERIFY_PRICE_IN_PER_M = float(os.getenv("CQA_VERIFY_PRICE_IN_PER_M", "0"))
    VERIFY_PRICE_OUT_PER_M = float(os.getenv("CQA_VERIFY_PRICE_OUT_PER_M", "0"))
    # Цена за 1 млн токенов (USD). Берите из прайса провайдера. 0 -> попробуем взять из litellm.
    PRICE_IN_PER_M = float(os.getenv("CQA_PRICE_IN_PER_M", "0"))
    PRICE_OUT_PER_M = float(os.getenv("CQA_PRICE_OUT_PER_M", "0"))
    DB_PATH = os.getenv("CQA_DB", str(ROOT / "data" / "cqa.sqlite"))
    ORG_API_URL = os.getenv("CQA_ORG_API_URL", "")  # адрес GET-запроса организаторов
    BATCH_WORKERS = int(os.getenv("CQA_BATCH_WORKERS", "2"))
    TEAM_NAME = os.getenv("CQA_TEAM_NAME", "Freedom Utopia")
    MODEL_MODE = os.getenv("CQA_MODEL_MODE", "")   # local / external_api / hybrid; пусто = по модели
    DEFAULT_CHECKLIST = ROOT / "config" / "checklist.default.json"
    REACTION_THRESHOLD_SEC = int(os.getenv("CQA_REACTION_THRESHOLD_SEC", "180"))

    def resample_for(self, criterion) -> int:
        if not self.RESAMPLE:
            return criterion.resample
        forced = int(self.RESAMPLE)
        return 1 if forced <= 1 or criterion.resample <= 1 else forced


settings = Settings()
