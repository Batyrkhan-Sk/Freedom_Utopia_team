# CQA: оценка качества диалогов

## Запуск
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...                 # ключ выбранного провайдера
export CQA_MODEL="anthropic/claude-sonnet-5-5"   # любая модель LiteLLM, в т.ч. ollama/...
export CQA_PRICE_IN_PER_M=...  CQA_PRICE_OUT_PER_M=...   # цена за 1 млн токенов из прайса
export CQA_ORG_API_URL="<адрес GET организаторов>"
uvicorn backend.main:app --reload            # http://localhost:8000/docs
pytest                                       # тесты расчёта и конвейера (без вызовов модели)
```

## Порядок работы
1. `POST /import?group_id=DEV` - загрузить диалоги
2. `POST /evaluate/{conversation_id}` или `POST /evaluate-batch` + `GET /jobs/{id}`
3. `GET /conversations/{id}` - диалог + оценка
4. `POST /appeals` -> `POST /appeals/{id}/decision` - апелляция и решение
5. `POST /checklists` - сохранить изменённый чек-лист (новая версия), потом переоценить

## Структура
- `config/checklist.default.json` - стартовый чек-лист (instruction и rules заменить текстом из PDF)
- `backend/facts.py` - факты до ИИ; `prompt.py` - промпт из чек-листа; `llm.py` - вызов модели
- `backend/validator.py` - проверка ответа ИИ; `scoring.py` - расчёт; `evaluator.py` - конвейер
- `backend/appeals.py` - пересчёт после решения; `db.py` - SQLite; `main.py` - API
