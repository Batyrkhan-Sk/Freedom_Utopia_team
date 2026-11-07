# API бэкенда CQA

Базовый адрес: `http://localhost:8000` (интерфейс открывается на `/ui/`, тот же адрес - запросы без CORS).
Интерактивная документация: `/docs`. Все тела запросов и ответов - JSON.

Номера реплик (`idx`, `evidence_msg_ids`, `checked_range`) - позиция в исходном массиве транскрипта **с 0**.
В файле сдачи (`results.json`) они переводятся в нумерацию с 1, в интерфейсе для показа - `#idx+1`.

## Диалоги

### `GET /conversations` - список
Фильтры (все необязательные): `critical=true|false`, `needs_review=true|false`, `has_open_appeal=true|false`,
`language=RU|KZ|EN`, `channel=agent chat|voice call`, `q=<часть id>`.
```json
[{"conversation_id": "p1-ru-001", "channel": "agent chat", "language": "RU", "group_id": "DEV",
  "n_messages": 12, "evaluation_id": 7, "total": 92.31, "critical_error": false,
  "needs_review": false, "open_appeals": 0, "model": "groq/openai/gpt-oss-120b", "checklist_version": 2}]
```
Не оценённый диалог: `evaluation_id`, `total`, `critical_error`, `needs_review`, `model`, `checklist_version` = `null`.

### `GET /conversations/{id}` - карточка
```json
{"conversation": {"conversation_id": "p4-ru-001", "channel": "voice call", "language": "RU", "group_id": "DEV",
                  "has_audio": true,
                  "messages": [{"idx": 0, "ts": "2026-…T10:00:01.1234567+05:00", "t": 0.0, "speaker": "operator", "text": "…"}]},
 "evaluation": { …оценка, см. ниже, или null },
 "appeals": [ …апелляции этого диалога, см. «Апелляции» ]}
```
`t` - секунды от первой реплики: для перемотки записи (`audio.currentTime = t`).
`speaker`: `client` / `operator` / `bot` / `system`.

### `GET /audio/{id}` - запись звонка
Редирект на свежую ссылку mp3 (ссылка организаторов живёт ~1 час). В плеере: `<audio src="/audio/p4-ru-001">`.
Если записи нет - 404. Есть ли запись - `conversation.has_audio`.

### Загрузка
- `POST /import?group_id=DEV` - из API организаторов → `{"imported": 65}`
- `POST /conversations` - JSON-запись или массив записей (формат API организаторов) → `{"imported": 1, "conversation_ids": ["…"]}`
- `POST /conversations/text` - вставленный текст
  ```json
  {"text": "00:00 клиент: Где мой тариф?\n01:15 оператор: Здравствуйте! Меня зовут Алина.",
   "channel": "agent chat", "language": null, "conversation_id": null}
  ```
  → `{"imported": 1, "conversation_id": "paste-…", "messages": 2}`. Роли: клиент/оператор/бот/система (RU, EN, KZ).
- Ошибка формата - 422 с текстом в `detail`.

## Оценка

### `POST /evaluate/{id}?checklist_id=chat-quality&version=` - оценить один диалог
`version` пусто = последняя версия чек-листа. Ответ - оценка (синхронно, 5-60 с):
```json
{"evaluation_id": 7, "conversation_id": "p1-ru-001", "checklist_id": "chat-quality", "checklist_version": 2,
 "model": "groq/openai/gpt-oss-120b", "llm_calls": 1, "tokens": {"in": 5200, "out": 900}, "cost_usd": 0.0,
 "facts": {"first_reaction_sec": 40, "end_event": "operator_closed", "...": "..."},
 "criteria": [
   {"criterion_id": "c01_contact", "name": "Установление контакта", "answer": "yes",
    "points": 10.0, "weight": 10, "effective_weight": 15.38,
    "evidence_msg_ids": [9], "checked_range": null, "na_reason": null,
    "explanation": "«Здравствуйте, меня зовут Дина» - приветствие и имя.",
    "confidence": 0.9, "needs_review": false, "reused": false,
    "original_answer": "no"}
 ],
 "sections": [{"section_id": "opening", "points": 25.0, "max": 25.0}],
 "total": 92.31, "critical_error": false, "needs_review": false,
 "asked_criteria": ["c01_contact", "…"],
 "appeal_history": [{"appeal_id": 3, "criterion_id": "c07_closing", "old": "no", "new": "yes",
                     "reason": "…", "at": "2026-11-07T21:10:00+00:00"}]}
```
- `answer`: `"yes"` / `"no"` / `"na"` / число ступени шкалы.
- `points` = `null` для N/A и для флага; `effective_weight` - вес после перераспределения N/A.
- Итог, баллы и флаг грубой ошибки **считает бэкенд** - интерфейс их показывает, не пересчитывает.
- `original_answer` и `appeal_history` появляются только после решения по апелляции.
- `reused: true` - ответ взят из прошлой оценки при переоценке (ИИ не спрашивали).

### `POST /evaluate-batch` - оценить много
`{"conversation_ids": null, "checklist_id": "chat-quality", "version": null}` (null = все) → `{"job_id": "a1b2c3d4", "total": 65}`

### `POST /reevaluate` - переоценка после правки чек-листа
```json
{"checklist_id": "chat-quality", "version": null, "conversation_ids": null}
```
`version` null = последняя; `conversation_ids` null = все диалоги, у которых есть оценка.
ИИ спрашивают только о новых и изменённых критериях; если менялись только веса - только пересчёт, без ИИ.
Ответ: `{"job_id": "…", "total": 24}`.

### `GET /jobs/{job_id}` - ход пакетной оценки/переоценки
```json
{"status": "running|finished", "total": 24, "done": 10, "failed": [{"id": "p1-ru-005", "error": "…"}],
 "diffs": [{"conversation_id": "p1-ru-001", "old_total": 92.31, "new_total": 84.62,
            "old_critical": false, "new_critical": false,
            "asked_criteria": [],
            "changed_answers": [{"criterion_id": "c04_consultation", "old": 20.0, "new": 15.0}],
            "evaluation_id": 31}]}
```
`diffs` есть только у задач `/reevaluate`.

## Чек-лист

- `GET /checklists` → `[{"checklist_id": "chat-quality", "latest_version": 2}]`
- `GET /checklists/{id}?version=` → чек-лист целиком (пусто = последняя версия)
- `POST /checklists` - сохранить **новую версию** (тело - чек-лист целиком) → `{"checklist_id": "chat-quality", "version": 3}`.
  Ошибки проверки → 422 `{"detail": ["Сумма весов критериев должна быть 100, сейчас 95", "…"]}`.
  Проверяется: уникальные id и code, сумма весов (кроме флага) = 100, вес флага = 0, у шкалы ≥ 2 значения,
  шкала только у типа scale, секция существует.
- `GET /checklists/{id}/diff?from_version=1&to_version=2` →
  `{"added": [], "removed": [], "changed": ["c04_consultation"], "weight_only": ["c07_closing"], "rules_changed": false}`
  (`changed`/`added` - ИИ спросят заново, `weight_only` - только пересчёт; `rules_changed` - изменены общие правила, переспрашивается всё).

Чек-лист (сохраняйте **все** поля, даже те, что интерфейс не показывает):
```json
{"checklist_id": "chat-quality", "version": 2, "name": "…", "rounding": 2, "rules": "общие правила для ИИ",
 "sections": [{"id": "opening", "name": "Начало диалога"}],
 "criteria": [{"id": "c04_consultation", "code": "consultation", "name": "Корректность и полнота консультации",
               "section": "security_resolution", "type": "scale", "weight": 20, "na_allowed": true,
               "scale": [0, 5, 10, 15, 20],
               "scale_descriptions": {"20": "Все существенные вопросы закрыты…", "15": "…"},
               "instruction": "текст для ИИ: когда какая ступень", "evidence_roles": ["operator"], "resample": 3}]}
```
`type`: `yes_no` / `scale` / `flag` (флаг - грубые ошибки, вес 0, в итог не входит). `code` - код в файле сдачи.

## Апелляции

- `POST /appeals` `{"evaluation_id": 7, "criterion_id": "c07_closing", "comment": "…"}` → `{"appeal_id": 3}`
- `GET /appeals?status=open|decided` → строки:
  ```json
  [{"id": 3, "evaluation_id": 7, "conversation_id": "p1-ru-001", "criterion_id": "c07_closing",
    "comment": "…", "status": "open", "created_at": "2026-11-07 21:05:00", "decision": null}]
  ```
- `GET /appeals/{id}` → апелляция + `decision` + `evaluation`
- `POST /appeals/{id}/decision` `{"new_answer": "yes", "reason": "Итог подведён"}`
  (`new_answer` равен текущему ответу = «подтвердить»). Решение записывается в саму оценку:
  ответ меняется, исходный сохраняется в `original_answer`, итог пересчитывается, апелляция - `decided`.
  ```json
  {"criterion_id": "c07_closing", "reason": "…", "original_answer": "no", "new_answer": "yes", "changed": true,
   "original_total": 92.31, "final_total": 100.0, "final_critical_error": false, "final_criteria": {"…": "…"},
   "evaluation": { …обновлённая оценка… }}
  ```
  Недопустимый ответ (например N/A там, где запрещён) → 422.

## Интерфейс
`GET /` → редирект на `/ui/` (папка `frontend/`), если её нет - на `/docs`.
