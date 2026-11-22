# Отчёт: классификация сервисных рёбер graph_v3 (passable / municipal_service / required_current_stage + приоритеты)

Дата: 22.11.2026 (пересборка «этап 1» по `mapprep/TASK_align_stage1_map_v3.md`).
Скрипт: `mapprep/prep/classify_service.py` (только локальные данные, сети нет).
QA-карта: `mapprep/prep/build_check_map.py` (параметризован, старый вызов без 4-го аргумента не меняется).

## Решение: priority и этапность — разные признаки
- `priority` 1/2/3 (веса 10/3/1) — значимость дороги, сохранена без изменений.
- **Этап 1 (`required_current_stage`)** = `municipal_service` И (`bus_route` ИЛИ `critical_access`).
  Просто `priority==1` (магистраль без автобуса/крит.доступа) основанием этапа **не** является —
  это следующие этапы. Прежняя ошибка (`required = municipal_service` для всей сети) исправлена
  в источнике истины (`classify_service.py`) и пересобраны все derivative outputs.
- nonmunicipal рёбра с bus/crit в этап 1 **не** берутся (инвариант required ⇒ municipal) —
  2 физические пары (4 направленных ребра, 0.124 км пары / 0.247 км направленных, зона 8,
  service-проезды с bus_route) помечены в `manual_review.csv` (issue=`nonmunicipal_buscrit`).

## Входы
- `mapprep/out/graph_v3.json` (только чтение): 9 620 узлов / 23 172 направленных ребра /
  2 498.03 км по направленным рёбрам (11 483 двусторонние пары + 206 one-way) ≈ 1 255.8 км физических улиц.
- `mapdata/data/attractors.csv`: критические POI — `amenity=hospital` (47) + `amenity=clinic` (13) = 60.
  Тегов `fire_station`/`emergency` в attractors **нет** (проверено) — только больницы и клиники.
- КСОДД-привязка (`ksodd_street_match.csv`) для приоритетов **не используется** → `priority_source=ksodd` нигде не назначен.

## Выходы (все в `mapprep/out/`)
- `service_classification.csv` — 23 172 строки (по направленному ребру), колонки:
  `edge_id, passable, municipal_service, required_current_stage, priority, priority_weight,
  priority_source, priority_confidence, critical_access, ownership, review_flag, rationale`.
- `manual_review.csv` — 7 944 строки (по физической паре): nonmunicipal-кандидаты (в т.ч. 2 `nonmunicipal_buscrit`),
  estimate-приоритеты; парных конфликтов 0.
- `check_service_map.html` — QA-карта (см. ниже).
- `REPORT_service.md` — этот файл.

## Правила классификации (без изменений, кроме этапа)
- **passable=true у всех** рёбер: way с `access=private/no/customers` уже исключены при сборке graph_v3.
- **municipal_service=true**: публичные улицы (дворы остаются в scope) + service-подъезды к критическим объектам.
  **municipal_service=false**: прочие service-подъезды без доказательства публичного статуса (576.7 км
  municipal / 679.0 км nonmunicipal по физ. парам — без изменений).
- **ownership = unknown у всех 23 172 рёбер** (подтвердить владение нечем) — без изменений.
- **Приоритеты (веса 10/3/1 без изменений):** P1 = крит. подъезд ≤150 м (attractor, 0.9) → автобус (osm_bus, 0.95)
  → trunk/primary/secondary(+_link) (osm_highway, 0.8); P2 = tertiary/residential/unclassified(+tertiary_link)
  с названием (0.8); P3 = living_street/service или без названия (0.7/0.5).
- **Парная семантика:** решение на пару, копия на оба направления; one-way (206) отдельно.

## Метрики ЭТАПА 1 (required_current_stage)
### Объём
- **3 814 направленных рёбер / 400.0 км направленных** = **1 983 физические пары / 205.3 км пары**.
### По основанию (направленные / пары)
| Основание | рёбра | км (напр.) | пары | км (пары) |
|---|---|---|---|---|
| автобусный маршрут (только bus) | 1 892 | 263.3 | 1 008 | 136.3 |
| критический подъезд (только crit) | 1 672 | 118.8 | 841 | 59.6 |
| bus + crit | 250 | 17.9 | 134 | 9.4 |
### По зонам (физ. км пары / пары, шт)
| Зона | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| км этапа 1 | 6.4 | 15.7 | 8.6 | 16.6 | 57.1 | 54.4 | 21.6 | 17.5 | 7.3 |
| пары | 29 | 43 | 13 | 141 | 779 | 626 | 107 | 228 | 17 |
### Связность этапа 1 (неориентированно)
- Компонент: **33**, крупнейшая — 190.6 км (**92.84%**); 27 мелких «островков» <1 км суммарно 5.47 км
  (отрезки автобусных/крит. подъездов, не связанные с основным этап-1 контуром; для CARP маршруты
  строятся внутри компонент зоны).
- Справочно, вся municipal сеть (этапы 2+): 57 компонент, крупнейшая 564.1 км (97.81%).

## Муниципальное покрытие ПОСЛЕДУЮЩИХ этапов (municipal, не этап 1)
- **3 179 физических пар / 371.4 км пары** (направленные ≈ 742.8 км).
- По зонам (физ. км пары): 1: 22.9, 2: 31.8, 3: 18.3, 4: 70.7, 5: 66.0, 6: 68.0, 7: 28.7, 8: 43.9, 9: 21.0.

## P1 без допуска в этап 1
- **151 направленное ребро / 14.9 км направленных** = 80 пар / 7.7 км пары — магистрали
  trunk/primary/secondary без автобусного маршрута и вне крит. доступа; остаются P1 по весу,
  но уборка — следующие этапы.

## Инварианты (скрипт + независимая проверка `logs/verify_stage1.py`) — все PASS
| # | Проверка | Результат |
|---|---|---|
| 1 | required_current_stage ⇒ municipal_service | PASS |
| 2 | этап 1 = municipal И (bus_route ИЛИ critical_access), обе стороны | PASS, 0 нарушений |
| 3 | все municipal bus/crit включены в этап 1 | PASS (bus 2 142 + crit 1 922 направленных; с пересечением) |
| 4 | municipal без bus/crit в этап 1 не попали | PASS |
| 5 | nonmunicipal bus/crit исключены и в review | PASS, 2 пары (`nonmunicipal_buscrit`) |
| 6 | парные рёбра идентичны (10 сервисных полей) | PASS, 0 расхождений |
| 7 | обратное ребро в той же зоне | PASS |
| 8 | priority_source=ksodd ⇒ привязка | PASS, ksodd не используется |
| 9 | passable=false только с rationale | PASS, passable=false нет |
| 10 | Σ км по направленным рёбрам = 2 498.0 | PASS, 2 498.03 |
| 11 | веса 1/2/3 = 10/3/1 | PASS |
| 12 | CSV покрывает все рёбра ровно один раз | PASS, 23 172/23 172 |
| 13 | ownership=unknown у всех | PASS |
| 14 | все 9 зон сохранены | PASS |

## Прочие распределения (без изменений)
- По приоритетам (физ. км пары): P1 213.0 (2 063 пары), P2 165.1 (1 559), P3 877.7 (8 067).
- Доля estimate (lanes+width+maxspeed все estimate) по приоритетам: P1 45.4%, P2 77.9%, P3 95.8%.
- Critical access: 975 физ. пар / 1 922 направленных ребра в ≤150 м от hospital/clinic; покрытие POI 59/60
  (1 больница в 10.3 км от графа, вне bbox выгрузки).

## QA-карта `check_service_map.html`
- Режимы зоны/приоритет/ширина/источник — как раньше; сервисные:
  - **«Этап 1 (автобус/крит.доступ)»**: красный — этап 1, оранжевый — municipal этапов 2+, серый — nonmunicipal;
  - «Сервисный класс» (синий/красный/фиолетовый) и слои «На проверке» (7 944 точки), «Крит. подъезды»,
    «Больницы/клиники» (60) — без изменений.
- Проверено: данные встроены (23 172 ребра, stage1-флаг у 3 814), оба JS-блока проходят `node --check`.

## Backout
- Прежние outputs перенесены (не удалены) в `mapprep/out/archive/pre_stage1/` — 9 файлов:
  `service_classification.csv`, `manual_review.csv`, `check_service_map.html`, `REPORT_service.md`,
  `map_package_v3.json`, `edge_weights_v3_integrated.csv`, `map_package_v3_review.csv`,
  `check_map_v3_integrated.html`, `REPORT_v3_integrated.md`.
- `out/graph_v3.json` **не изменён**: sha256 до = после =
  `c29d4675e8f230015cde370b0d6e13b9d13f7a93f65516bf7f889c34b8162c76`.
- Удаление новых выходов возвращает состояние «как было»:
  `Remove-Item mapprep/out/service_classification.csv, mapprep/out/manual_review.csv, mapprep/out/check_service_map.html, mapprep/out/REPORT_service.md`
  (интегрированный package — по списку из `REPORT_v3_integrated.md`).

## Ограничения и следующие шаги
1. Ownership везде unknown — нужна привязка КСОДД или реестр муниципальных дорог.
2. 2 nonmunicipal service-проезда с bus_route (зона 8) ждут ручного решения: подтвердить публичный
   статус → перевести в municipal (и тогда они войдут в этап 1), либо оставить nonmunicipal.
3. service-подъезды (679 км) — крупнейший блок manual_review (здания из residential.csv как признак).
4. Этапы 2+ пока одним блоком (371.4 км municipal) — дробление на этапы 2, 3... — отдельное решение
   пользователя.
