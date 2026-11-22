# Задача: классификация рёбер graph_v3 — passable / municipal_service / required_current_stage + приоритеты 1/2/3

Рабочая папка: `mapprep/`. Скрипт `mapprep/prep/classify_service.py` (Windows, Python с `-I`). Не трогать файлы
вне `mapprep/` (кроме ЧТЕНИЯ `mapdata/data/`). **Не изменять `out/graph_v3.json`** — результат пишется
отдельными файлами; граф только читается. Отчёт — `mapprep/REPORT_service.md` (по-русски, коротко).

## Вход (проверено по локальным файлам, 22.11.2026)
- `out/graph_v3.json` — 9 620 узлов / 23 172 ребра / 2 498.0 км (QA подтверждено). **Важно:** 2 498 км —
  сумма по всем НАПРАВЛЕННЫМ рёбрам (20 451 двусторонняя пара + 206 one-way), т.е. ~1 250 км уникальных
  улиц. В отчётах всегда указывать, по направленным рёбрам или по парам считается; не выдавать
  направленные км за уникальные улицы.
- `mapdata/data/attractors.csv` — 2 157 POI (проверено: 2 157 строк данных) — больницы/клиники для критических подъездов.
- `mapdata/data/ksodd_tables/*.csv` + привязки `mapdata/TASK_match_ksodd.md` — ТОЛЬКО если задача mapdata
  выполнена; КСОДД-приоритет без доказательства из привязки не назначать (см. правила).
- `out/poi_v3.json` — если есть; иначе брать больницы напрямую из attractors.csv.

## Классы рёбер (новые поля, не ломая схему v2/v3)
- `passable` (bool) — ребро доступно для проезда техники (по умолчанию true у всех рёбер графа;
  false только при явном доказательстве — закрытие, частная территория без права проезда; каждый
  случай в rationale).
- `municipal_service` (bool) — уборка ребра — обязанность муниципалитета. **Дворы остаются в scope**
  (предыдущее решение): living_street/courtyard-ответвления — municipal_service=true.
- `required_current_stage` (bool) — ребро подлежит обслуживанию на **текущем (первом) этапе**:
  `municipal_service` И (`bus_route=true` на ребре/физической паре ИЛИ `critical_access=true`).
  **priority ≠ этап:** просто `priority==1` (магистраль без автобуса/крит.доступа) основанием этапа
  не является — остаётся для следующих этапов (зафиксировано `mapprep/TASK_align_stage1_map_v3.md`,
  решение пользователя). Подмножество municipal_service (инвариант: required_current_stage ⇒
  municipal_service). nonmunicipal рёбра с bus/crit в этап не берутся — уходят в manual_review
  (issue=`nonmunicipal_buscrit`).
- `ownership` ∈ {municipal, unknown}. Всё, что нельзя однозначно отнести к муниципальному по
  данным OSM/КСОДД, — unknown и попадает в `out/manual_review.csv` (не выкидывать из графа).
- Отдельно посчитать и показать в отчёте долю «nonmunicipal/estimated»: service/подъезды и рёбра,
  у которых lanes/width/maxspeed все = estimate (~88% рёбер по QA v3) — распределение по классам.

## Приоритеты (веса 10 / 3 / 1)
- `priority` 1/2/3, `priority_weight` 10/3/1 — сохраняет вес = приоритет × км из CLAUDE.md.
- **priority 1, первая очередь:** (а) автобусные маршруты (`bus_route=true`); (б) trunk/primary/secondary
  (+_link); (в) критические подъезды: рёбра в ≤150 м от attractors kind=hospital/clinic (пожарные/скорая —
  если в attractors есть tag=fire_station/emergency, их тоже; что нашлось — перечислить в отчёте).
- priority 2: tertiary/residential/unclassified с названием; priority 3: living_street/service или без названия.
- `priority_source` ∈ {osm_bus, osm_highway, attractor, ksodd, estimate} + `priority_confidence`
  (high|medium|low с числом 0–1). ksodd — только при подтверждённой привязке из
  `mapdata/data/ksodd_street_match.csv` (match=yes); road_categories.csv не содержит приоритета
  (category пуст — проверено в WORKLOG), поэтому **автоматически КСОДД-приоритет не назначать**:
  любой priority_source=ksodd без записи в привязке — баг, ловится инвариантом.

## Парная семантика
- Для каждой двусторонней пары (u→v, v→u): одинаковые `municipal_service`, `required_current_stage`,
  `priority`, `priority_weight`, `ownership`. Расхождения — автоматически в `out/manual_review.csv`,
  в граф решение не писать до разбора. Обратное ребро обязано быть в той же зоне (уже инвариант v3 —
  проверить и сохранить).

## Выход
- `out/edge_service.csv`: `edge_id, passable, municipal_service, required_current_stage, priority,
  priority_weight, priority_source, priority_confidence, critical_access, ownership, review_flag, rationale`.
- `out/graph_v3_service.json` — копия graph_v3 + эти поля на рёбрах (source-граф не тронут).
- `out/manual_review.csv` — ownership unknown, парные конфликты, сомнительные nonmunicipal.
- `out/check_service_map.html` — QA-карта: цвет по классу/приоритету, слои review и критических подъездов
  (параметризовать существующий `prep/build_check_map.py`, не ломая старый вызов).
- `REPORT_service.md`.

## Готово, когда (acceptance)
- **Инварианты** (скрипт-проверка, все PASS): required_current_stage ⇒ municipal_service; парные рёбра
  идентичны по сервисным полям; обратное ребро в той же зоне; priority_source=ksodd ⇒ есть запись
  привязки; passable=false только с заполненным rationale; сумма км по направленным рёбрам = 2 498.0.
- **Распределения** в отчёте: км и число рёбер по (priority × класс × зона), доля estimate по приоритетам,
  доля ownership=unknown, critical_access (сколько рёбер, какие POI покрыты); км указаны и по направленным,
  и по парам.
- **QA-карта** открывается, слои видны, точки manual_review на месте.
- **Backout:** `out/graph_v3.json` не изменён (хэш до/после в отчёте), удаление выходных файлов
  возвращает состояние «как было»; в отчёте команда/список удаляемых файлов.
