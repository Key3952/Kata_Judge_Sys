# План интеграции старого UI со старой бизнес-логикой на новой БД

## Контекст и цель

Ветка переведена на нормализованную SQLAlchemy-схему (`models.py`, `db_service.py`),
но `app.py` содержит только JSON API — все прежние страницы (`templates/*.html`)
остались без роутов. Прежний генератор протоколов (`generate_protocols.py`) читает
только CSV-папки `competitions/<slug>/...`.

**Цель:** вернуть все прежние страницы и весь прежний функционал (регистрация,
судейская форма, табло, главное табло realtime, админ-панели, генерация протоколов
Excel/PDF/CSV с скачиванием из браузера), при этом **единственный источник истины —
новая SQLite-БД**. CSV-папка соревнования становится *проекцией* БД, перегенерируемой
после каждой мутации, поэтому прежний `generate_protocols.py` работает без изменений.

Мост между страницами/генератором и новой БД уже начат в `legacy_bridge.py`
(контексты страниц + проекция `project_competition()`).

## Ключевые решения

1. **Идентификатор соревнования** для URL/прежних путей — `Competition.folder_name`
   (slug). Отображаемое название, дата, место, возрастная подложка — `CompetitionMeta`
   (п.3, п.11 — «на горячую»).
2. **Формат оценок.** Судейская форма исторически шлёт
   `{judge, pos, pair, tori, uke, details: {техника: {m1,m2,med,big,c_plus,c_minus,forgotten}}, total}`.
   Правила old == new (штрафы 1/1/3/5, забытая=0 и итог/2, ±0.5 коррекция).
   Поэтому: `JudgeScore.raw_json` хранит raw формы судьи как есть; канон для расчёта —
   `details` по техникам дисциплины; `ScoreItem` заполняется из деталей
   (penalty = m1+m2+med+big+c_minus−c_plus) → обе модели scoring дают один итог.
3. **Этапы.** `Discipline.stage_cfg` (JSON): `{current_stage: prelim|final, mode,
   status, final_top_n}` + `final_source_stage`. Действия админа: P (prelim), F
   (открыть финал из TOP-N предыдущего этапа), S (сразу финал), ⛔ (закрыть этап).
4. **Позиции судей.** В judge_form судья выбирает ФИО **и место**; колонки
   `Судья 1..5` в `final_protocol.csv` соответствуют позициям из
   `JudgeListEntry.position`.
5. **Realtime (п.7).** SocketIO-комнаты `tablo:<folder>`; события `tablo_update`
   (главное табло переключает iframe) и `tablo_refresh` (обычное табло обновляет
   содержимое без F5). События шлются после каждой мутации оценок/пар/этапов/meta.
6. **CSV — только импорт** прежних глобальных реестров (`data/participants.csv`,
   `data/judges.csv`, п.1 — из полной даты извлекается год). Запись — только в БД.

## Фазы работ

### Фаза 1. Схема БД и миграция
- [x] Модели: `Discipline.display_name/stage_cfg/final_source_stage`,
      `Competition.main_tablo_discipline/banner`, `SafeUnicodeJSON` для stage_cfg.
- [x] `migrate_old_data.py`: идемпотентный перенос данных из прежней схемы
      (`database.py` + CSV `data/*`, `competitions/*/…csv`) в новую; извлечение года;
      перенос оценок из protocols/*.csv (details_json → raw_json); заморозка снимков.
- [x] Тесты миграции (`test_migration.py`).

### Фаза 2. Сервисный слой (db_service / legacy_bridge)
- [ ] `db_service`: canonical raw из details, `save_judge_score` c позицией судьи,
      `get_or_create_discipline(display_name)`, `get_discipline_by_key`,
      `delete_discipline/delete_competition/clear_participants/clear_judges`,
      `move_pairs_to_final(top_n)` (пересортировка номеров), leaderboard по позициям.
- [ ] `legacy_bridge`: stage_cfg из БД (не stage.json), config.json ↔ meta,
      projection с колонками по позициям и именами файлов `<judge>_<pos>_<tori>-<uke>.csv`.

### Фаза 3. Роуты app.py (все прежние страницы)
- Публичные: `/` (public_dashboard), `/judge/<comp>/<kata>`, `/tablo/<comp>/<kata>`,
  `/main-tablo/<comp>` (+ empty/dynamic), `/results/<comp>/<kata>`.
- Админ: `/admin/login|logout|dashboard`, `/admin/config`, `/admin/data-editor`,
  `/admin/<comp>` (edit), `/admin/<comp>/protocols`, `/admin/<comp>/delete`,
  `/admin/<comp>/open|close`, `/admin/<comp>/add-discipline|remove-discipline`,
  `/admin/<comp>/set-main-tablo`, `/admin/<comp>/<kata>/stage`,
  `/admin/<comp>/generate-protocols`, `/admin/clear-participants|clear-judges`,
  `/admin/upload-banner`, `/competitions/<path:…>` (скачивание файлов).
- Регистрация: `GET/POST /<comp>/<kata>/register` + префикс `/admin/...`.
- API совместимости: `/api/participants/search|info|column-suggestions`,
  `/api/judges/search`, `/api/data/participants|judges|competitions` (CRUD),
  `/api/<comp>/<kata>/registration-data|save-judge-action|get-judge-scores/<…>`,
  `/api/competition/status`.
- SocketIO: `join_tablo/leave_tablo`, эмиссии после мутаций.

### Фаза 4. Шаблоны (правка под новые данные/пути)
- [ ] `base.html`: ключ сессии `is_admin`, ссылки login/logout.
- [ ] `tablo.html`: realtime-обновление через socket.io (+ polling fallback),
      meta из БД (дата/подложка на горячую).
- [ ] `main_tablo_dynamic.html`: CDN socket.io → локальный (offline-safe) не завезён —
      оставить CDN, добавить fallback; событие `tablo_update` уже ожидается.
- [ ] `registration.html`: поле «Дата рождения» → год (п.1), label «Год рождения».
- [ ] `judge_form.html`/`data_editor.html`/`edit_competition.html` — проверка форматов.

### Фаза 5. Протоколы Excel/PDF/CSV (скачивание из браузера)
- [ ] После мутаций и по кнопке «Сгенерировать» — `project_competition()` →
      `generate_protocols.generate_competition_protocols()`; результат складывать в
      `downloads/` и отдавать через `/competitions/<path>` (attachment).
- [ ] Прямые download-роуты API (`/api/export/protocol.{csv,xlsx,pdf}` уже есть) —
      привести к единому виду с фронтом.

### Фаза 6. Тестирование (пошаговое)
1. Юнит: scoring old/new совпадают на деталях; year-extraction.
2. Миграция: old-БД/CSV → новая схема, повторный запуск идемпотентен.
3. API-тесты прежних endpoints (search/info/column-suggestions/registration POST).
4. Судейский цикл: save-judge-action → get-judge-scores → tablo rows/place.
5. Этапы: P/F(TOP-N)/S/⛔, перенос пар в финал, протокол финала.
6. Реестры: правка ФИО в data-editor видна во всех активных компах (п.2);
   finish-заморозка.
7. Жеребьёвка (п.8) — порядок старта на табло.
8. Техники 5..10 (п.9) — усечение rows, not_performed.
9. Meta на горячую (п.3/п.11) — title/date/subtitle меняются без перезагрузки компa.
10. Realtime (п.7) — emit leaderboard/tablo_update при сохранении оценки.
11. Экспорт: generate-protocols создаёт xlsx/pdf/csv; GET /competitions/... отдаёт файл.
12. Дым: все GET-страницы 200; `python -m pytest -q`; запуск сервера + curl.

## Критерии приёмки
- Все прежние URL работают и отображают данные из новой БД.
- Протоколы генерируются в том же формате/структуре папок, скачиваются из браузера.
- `pytest` зелёный (старые test_app/test_database + новые тесты интеграции).
- CSV используется только для импорта; ручная правка CSV не влияет на данные приложения.
