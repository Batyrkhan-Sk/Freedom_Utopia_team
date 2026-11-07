# CQA: оценка качества диалогов

## Запуск
```bash
pip install -r requirements.txt
cp .env.example .env                         # Windows: copy .env.example .env; впишите ключи и адрес API
uvicorn backend.main:app --reload            # http://localhost:8000/docs
pytest                                       # тесты (без вызовов модели)
```
Все настройки читаются из `.env` (см. `.env.example`): модель, резервные модели, лимит запросов в минуту,
повторный опрос. Модель задаётся строкой LiteLLM: `gemini/...`, `groq/...`, `anthropic/...`, `openai/...`, `ollama/...`.

## Точность на эталоне
```bash
python -m eval.run_answer_key                # все диалоги эталона, отчёт по критериям и языкам
python -m eval.run_answer_key --ids p1-ru-001,p1-kz-003
```
Эталон: `data/answer_key/draft.json` (21 диалог DEV), правила толкования: `docs/rules.md`.
Нужен `data/raw/dev.json` (ответ API организаторов для `group_id=DEV`, в git не хранится).

## Сдача результатов (формат CQA_Student_Guide_Appendix_Results.pdf)
```bash
python -m eval.make_results --group DEV            # 65 диалогов -> results/dev/results.json
python -m eval.make_results --group PROD           # 10 контрольных чатов -> results/prod/results.json
python -m eval.make_results --group PROD --check   # только проверить формат готового файла
```
Получает набор из API организаторов, оценивает действующей версией чек-листа, пишет файл и проверяет его
по правилам приложения (коды критериев, true/false/null, реплики с 1, записи с ошибкой). Повторный запуск
доделывает только упавшие диалоги. Эталон `data/answer_key/` используется только для измерения точности
(`eval/run_answer_key.py`); оценщик его не видит.

## Порядок работы
1. `POST /import?group_id=DEV` - загрузить диалоги из API организаторов;
   `POST /conversations` - JSON-файл; `POST /conversations/text` - вставленный текст («мм:сс роль: текст»)
2. `POST /evaluate/{conversation_id}` или `POST /evaluate-batch` + `GET /jobs/{id}`
3. `GET /conversations/{id}` - диалог + оценка; `GET /audio/{id}` - свежая ссылка на запись звонка
4. `POST /appeals` -> `POST /appeals/{id}/decision` - апелляция и решение
5. `POST /checklists` - сохранить изменённый чек-лист (новая версия), потом переоценить

## Структура
- `config/checklist.default.json` - стартовый чек-лист (текст PDF, ступени шкал, секции)
- `backend/facts.py` - факты до ИИ (события, время реакции, язык); `prompt.py` - промпт из чек-листа
- `backend/llm.py` - вызов модели: ограничение частоты, повторы при 429/503, резервные модели
- `backend/validator.py` - проверка ответа ИИ; `scoring.py` - расчёт; `evaluator.py` - конвейер
- `backend/ingest.py` - загрузка JSON и текста, разбор страницы плеера
- `backend/appeals.py` - пересчёт после решения; `db.py` - SQLite; `main.py` - API
- `eval/run_answer_key.py` - прогон по эталону; `eval/make_results.py` + `backend/export.py` - файл сдачи
