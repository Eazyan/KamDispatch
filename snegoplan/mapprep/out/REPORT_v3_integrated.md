# Map package v3 — интегрированный отчёт (2026-11-22)

Задача: `mapprep/TASK_integrate_map_v3.md`. Скрипт: `mapprep/prep/integrate_map_v3.py` (одна команда из корня проекта, без сети).

## Команда воспроизведения

```
python mapprep/prep/integrate_map_v3.py
```

## Входы (sha256)

- `mapprep\out\graph_v3.json`: `c29d4675e8f230015cde370b0d6e13b9d13f7a93f65516bf7f889c34b8162c76`
- `mapprep\out\zones_v3.json`: `611f203b585d37b3fbac6801c0ad8e587462a6a53ea22ef4bb5700981ef49b32`
- `mapprep\out\service_classification.csv`: `49ad93f27ccdbf252aef95a0fdd8e939013d8b5022d0e67d9859f11be1c647bd`
- `mapprep\out\edge_weights.csv`: `b8e2a6633ea278fd94b6d9d9f70b578199fa2fde46c063ac74634109526a55ad`
- `mapdata\data\ksodd_street_match.csv`: `4f7d7c994e392bc45a9002cf0d426aca1b867a743009bb1dacb1d2b0f2f83df7`
- `mapdata\data\ksodd_point_match.csv`: `0b89d384a683a1613756bb576977d70cfcb465464595ea557ade6c6bd33d076a`
- `mapdata\data\traffic_lights_match.csv`: `460cea95f8e7d1a7189fc1184ac4e056a675c4c3d89bbda0ff0a5018e07d2733`
- `mapdata\data\poi_critical.csv`: `f6661f0c522bafb61b3ace2e4628a95ef2221c883d72f8f89a5f00dee8f69d13`

## Что вошло в основной слой

- Все 9620 узлов и 23172 рёбер graph_v3 (исходные поля не изменены), 9 зон, обратные рёбра и one-way (206) сохранены.
- Сервисная классификация: svc_passable / svc_municipal_service / svc_required_current_stage / svc_priority (+weight/source/confidence) / svc_critical_access / ownership / review_flag на каждом ребре.
- КСОДД high-confidence (match=yes + high, с source/confidence/rationale на значение): **2981 значений на 2601 рёбер** (street: 92 строк/ 2608 значений; points: 68 строк/ 373 значений). Пустая `category` не переносилась — автоматически приоритет/категория не назначались.
- Веса приоритетов 1/2/3 = 10/3/1; автобусные маршруты и критические подъезды к больницам/клиникам в P1 сохранены (источник — classification, не КСОДД-средние матчи).
- **Этап 1 (`svc_required_current_stage`)**: 3814 рёбер / 400.0 км (направленные) — municipal_service И (bus_route ИЛИ critical_access). P1 без допуска в этап 1 (магистрали без автобуса/крит.доступа): 151 рёбер / 14.9 км — следующие этапы. Зафиксировано `mapprep/TASK_align_stage1_map_v3.md`.
- Расхождение svc_priority vs исходный priority графа: 1642 рёбер (все — upgrade крит.подъезд/автобус по classification; поле priority графа сохранено, флаг priority_diff на ребре).

## Отдельные слои

- Светофоры: 87 объектов (61 КСОДД + 26 OSM) с исходным status/match/confidence; cycle_s/phases пустые, не заполнялись; недоказанные точки на рёбра не снапались.
- Критичные POI: 208 строк (162 matched представителя + 46 ambiguous-дублей), critical_access/bus_route_near отдельно от категорий дорог.

## Review (`map_package_v3_review.csv`) — 326 строк

| Таблица | Статус | Confidence | Строк |
|---|---|---|---|
| ksodd_point_match | conflict | high | 16 |
| ksodd_point_match | no | low | 9 |
| ksodd_point_match | yes | medium | 65 |
| ksodd_street_match | conflict | high | 112 |
| ksodd_street_match | no | low | 7 |
| ksodd_street_match | yes | medium | 32 |
| poi_critical | ambiguous | — | 46 |
| traffic_lights_match | ambiguous | low | 2 |
| traffic_lights_match | unmatched | high | 26 |
| traffic_lights_match | unmatched | low | 11 |

Все medium/low/unmatched/uncertain исходных CSV учтены; независимые конфликты high-строк: 128.

## Машинные проверки (acceptance)

| Проверка | Результат |
|---|---|
| узлы/рёбра сохранены | PASS (9620 / 23172) |
| edge_id уникальны | PASS (23172) |
| classification покрытие ровно 1 раз | PASS |
| обратные рёбра сохранены | PASS |
| зоны сохранены | PASS |
| required_current_stage => municipal_service | PASS |
| этап 1 = municipal И (bus_route ИЛИ crit), только такие | PASS (нарушений: 0) |
| все municipal bus/crit включены в этап 1 | PASS (пропущено: 0) |
| парные рёбра: согласованные сервисные поля | PASS (расхождений: 0) |
| веса 1/2/3 = 10/3/1 | PASS |
| ownership=unknown у всех | PASS |
| циклы/фазы светофоров пусты | PASS |
| sha256 graph_v3 до=после | PASS (c29d4675e8f23001…) |
| source/confidence у каждого KSODD-значения | PASS |
| конфликтные high-значения не в основном слое | PASS (конфликтных пар: 128) |
| high-счётчики = исходным CSV | PASS (street 92/92, point 68/68) |
| review-счётчики согласованы (строки+конфликты) | PASS (review 326, ожидалось >= 198 (независимых конфликтов: 128)) |
| package валидный JSON | PASS |

## Ограничения

- ownership не подтверждён ни для одного ребра (нет реестра/КСОДД-владения)
- lanes/width/maxspeed графа ~88% - estimate (см. REPORT_service.md)
- циклы светофоров в КСОДД отсутствуют - в планировании estimate 75 с из build_v3
- category дорог в road_categories.csv пуста - автоматически не назначается

## Backout

- `mapprep/out/graph_v3.json` не изменён (sha256 до = после, см. выше).
- Удаление выходов возвращает состояние «как было»:
  `Remove-Item mapprep/out/map_package_v3.json, mapprep/out/edge_weights_v3_integrated.csv, mapprep/out/map_package_v3_review.csv, mapprep/out/check_map_v3_integrated.html, mapprep/out/REPORT_v3_integrated.md`

## QA-карта

`check_map_v3_integrated.html` — автономный canvas-просмотрщик (без сети): режимы зоны/P1-P3/**Этап 1 (красный = bus/крит.доступ, оранжевый = municipal этапов 2+, серый = nonmunicipal)**/сервисный класс/КСОДД-enrichment; слои светофоров (цвет = статус матча) и критичных POI; легенда различает подтверждённое (КСОДД high, сплошное) и оценочное (estimate).
