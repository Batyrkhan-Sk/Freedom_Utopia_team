"""Все настройки в одном месте. Меняются переменными окружения, код не трогаем."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Settings:
    # Модель в формате LiteLLM: "anthropic/...", "openai/...", "ollama/qwen2.5:7b" и т.д.
    MODEL = os.getenv("CQA_MODEL", "anthropic/claude-sonnet-5-5")
    JSON_MODE = os.getenv("CQA_JSON_MODE", "1") == "1"
    MAX_TOKENS = int(os.getenv("CQA_MAX_TOKENS", "2500"))
    MAX_RETRIES = int(os.getenv("CQA_MAX_RETRIES", "2"))      # повторы при невалидном ответе
    VOTE_TEMPERATURE = float(os.getenv("CQA_VOTE_TEMPERATURE", "0.7"))  # для повторного опроса
    LOW_CONFIDENCE = float(os.getenv("CQA_LOW_CONFIDENCE", "0.7"))      # ниже -> "нужна проверка"
    # Цена за 1 млн токенов (USD). Берите из прайса провайдера. 0 -> попробуем взять из litellm.
    PRICE_IN_PER_M = float(os.getenv("CQA_PRICE_IN_PER_M", "0"))
    PRICE_OUT_PER_M = float(os.getenv("CQA_PRICE_OUT_PER_M", "0"))
    DB_PATH = os.getenv("CQA_DB", str(ROOT / "data" / "cqa.sqlite"))
    ORG_API_URL = os.getenv("CQA_ORG_API_URL", "")  # адрес GET-запроса организаторов
    BATCH_WORKERS = int(os.getenv("CQA_BATCH_WORKERS", "4"))
    DEFAULT_CHECKLIST = ROOT / "config" / "checklist.default.json"
    REACTION_THRESHOLD_SEC = int(os.getenv("CQA_REACTION_THRESHOLD_SEC", "180"))


settings = Settings()
