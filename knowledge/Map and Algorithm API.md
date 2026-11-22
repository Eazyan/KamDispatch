# API и форматы обмена карты и алгоритма

Этот справочник отвечает на четыре вопроса по каждому стыку: что вызвать, что передать, что получить и что считать ошибкой. Пути к файлам указаны от корня проекта. В таблицах `T?` означает поле, которое может отсутствовать, `T / null` означает поле, допускающее явное значение `null`. `int` — целое JSON-число, `number` — конечное JSON-число, `Dict<K,V>` — JSON-объект со значениями указанного типа.

## 1. Рабочие интерфейсы

| Кто вызывает | Команда и рабочая папка | Вход | Выход | Ошибка или ограничение |
|---|---|---|---|---|
| Команда карты | `python mapprep/prep/build_v3.py` из корня | `mapdata/data/osm_petropavlovsk.json`, локальные данные высот и v2 для привязки высот | `mapprep/out/graph_v3.json`, `zones_v3.json`, `edge_weights.csv`, `mean_weights.json`, `prep_stats_v3.json` | Проверки сборки должны пройти. Выходы находятся по фиксированным путям |
| Команда карты | `python mapprep/prep/classify_service.py` из корня | `graph_v3.json` и `mapdata/data/attractors.csv` | `service_classification.csv` и `manual_review.csv` | Неоднозначные случаи попадают в review. Признак обслуживания не подтверждает владельца дороги |
| Команда карты | `python mapprep/prep/integrate_map_v3.py` из корня | `graph_v3.json`, `zones_v3.json`, `service_classification.csv`, `edge_weights.csv` и CSV `ksodd_street_match`, `ksodd_point_match`, `traffic_lights_match`, `poi_critical` из `mapdata/data/` | `map_package_v3.json`, `edge_weights_v3_integrated.csv`, `map_package_v3_review.csv`, QA HTML и отчёт | Скрипт отказывается перезаписывать существующие выходы. Конфликтующие данные КСОДД попадают в review |
| Команда алгоритма | `python -m prep.build --zone 3 --out out/zone3_plows.json --map out/zone3_map.json` из `work/prep` | `input/graph_v2.json`, `input/snegoplan_input.xlsx`. Опции `--input-dir` (по умолчанию `input`), `--time-limit-s` (10), `--seed` (1) | HGS instance JSON и sidecar JSON из одного прогона | Нет условий зоны или подходящей техники → остановка. Ребро без совместимого класса → warning и задача с пустым `service_time` |
| Команда алгоритма | `hgs input.json output.json` или `hgs - -` | HGS instance JSON | HGS solution JSON | Ненулевой код процесса означает сбой запуска. Успешное завершение процесса может вернуть `status="infeasible"` |
| Команда алгоритма | `python -m prep.decode --map out/zone3_map.json --solution out/zone3_solution.json --out out/zone3_routes.geojson` из `work/prep` | Sidecar и solution того же прогона | GeoJSON `FeatureCollection` | Несовпадающие ID или отсутствие пути могут привести к исключению. Геометрию нельзя считать проверенной по факту записи файла |
| Команда алгоритма | `python verify_run.py --instance out/zone3_plows.json --solution out/zone3_solution.json --geojson out/zone3_routes.geojson` из `work/prep` | Instance и solution, `--geojson` необязателен | Текстовый отчёт. Успешный прогон заканчивается `VERIFY OK` | Проверяет покрытие, допустимость, смены, дедлайны и сравнение с baseline. Нарушение условия завершает проверку ошибкой |
| Локальный просмотр | `python work/prep/build_view.py` из корня | По умолчанию `out/zone3_routes.geojson`, `input/graph_v2.json`, `input/zones.json` относительно `work/prep`. Опции `--geojson`, `--graph`, `--zones`, `--out` | `work/prep/out/zone3_view.html` | Просмотрщик использует готовый результат. Проверку маршрутов выполняет отдельный шаг |

`prep.build` берёт зону из `edge.zone` в графе. `zones.json` нужен просмотру, а не созданию instance. Прогон зоны 3 на v2 и зона 3 в v3 относятся к разным наборам данных. Ссылки на артефакты: [v3-пакет](../mapprep/out/map_package_v3.json), [вход HGS](../work/prep/out/zone3_plows.json), [sidecar](../work/prep/out/zone3_map.json), [выход HGS](../work/prep/out/zone3_solution.json), [GeoJSON](../work/prep/out/zone3_routes.geojson).

## 2. Карта на выходе mapprep

| Артефакт | Поле | Тип | Смысл |
|---|---|---|---|
| `graph_v3.json` | `meta` | object | `created, source, crs` — строки, `bbox` — четыре числа, `depot_node, dump_node` — сейчас `null`, `prep` — метаданные сборки |
| `graph_v3.json`, `map_package_v3.json` | `nodes, edges` | `Node[], Edge[]` | Узлы и ориентированные дуги. Схемы ниже |
| `zones_v3.json`, `map_package_v3.json` | `zones` | `Zone[]` | Распределение рёбер по зонам |
| `map_package_v3.json` | `meta` | object | `created, task, script, graph_sha256_before: string`, `inputs: Dict<string,string>` с SHA-256, `counts: Dict<string,number>`, `rules, limitations: string[]` |
| `map_package_v3.json` | `layers.traffic_lights, layers.poi_critical` | `TrafficLight[], CriticalPoi[]` | Дополнительные слои. `traffic_lights` содержит в том числе записи без координат |

### Узлы, рёбра и зоны

| Объект | Поле | Тип | Единица, значения или инвариант |
|---|---|---|---|
| Node | `id` | int | ID узла графа. Сохранять без 32-битного приведения |
| Node | `lon, lat` | number | Градусы WGS84 |
| Node | `kind` | string | В v3 наблюдаются `junction`, `end`, `crossing`, `traffic_signals` |
| Node | `traffic_signals_source?` | string | `osm` у помеченных узлов. Реестр светофоров находится в `layers.traffic_lights` |
| Node | `elevation_m, elevation_source` | number, string | Метры. `srtm` или `estimate` |
| Node | `zone` | int | ID зоны узла |
| Edge | `id, u, v, key` | string, int, int, int | `id="<u>-<v>-<key>"`. `u/v` ссылаются на узлы. `key` различает параллельные дуги |
| Edge | `geometry` | `[number,number][]` | Минимум две точки, порядок `[lon,lat]`, направление `u→v` |
| Edge | `length_m, slope_pct` | number | Метры и проценты. Положительный уклон означает подъём в сторону `v` |
| Edge | `name, highway` | string / null, string | Название и классификация дороги OSM |
| Edge | `oneway, required, bus_route` | boolean | Односторонность, исходный флаг графа, признак автобусного маршрута |
| Edge | `street_id, reverse_edge_id, task_id` | string, string / null, string / null | Физический сегмент, обратная дуга, историческая привязка к задаче. В v3 `task_id` пустой |
| Edge | `lanes, width_m, width_dir_m, maxspeed` | int, number, number, number / null | Число полос, ширина всей проезжей части в метрах, ширина направления в метрах, ограничение км/ч |
| Edge | `lanes_source, width_source, maxspeed_source` | string | В текущей сборке `osm` или `estimate` |
| Edge | `osm_way_ids` | `string[]` | OSM way IDs |
| Edge | `priority, zone` | int | Исходный приоритет `1/2/3` и ID зоны дуги |
| Zone | `id, name, node_ids, edge_ids` | int, string, `int[]`, `string[]` | При выборе задач использовать зону ребра. Пограничная дуга может соединять узлы разных зон |
| Zone | `stats` | object | `nodes, edges: int`, `required_km: Dict<string,number>` с ключами `1, 2, 3, total`, `bbox: [number,number,number,number]` в порядке запад, юг, восток, север |
| Zone | `hull_geojson` | GeoJSON Feature | `Polygon` с замкнутым контуром. Показывает охват зоны на карте |

### Дополнения пакета v3

| Поле | Тип | Как читать |
|---|---|---|
| `svc_passable, svc_municipal_service, svc_required_current_stage` | boolean | Доступность, эвристика обслуживания, отбор этапа 1. Для планируемой уборки на первом этапе использовать `svc_required_current_stage`. Флаг `required` из исходного графа оставлен для совместимости |
| `svc_priority, svc_priority_weight` | int | Класс `1/2/3`, вес `10/3/1` |
| `svc_priority_source, svc_priority_confidence` | string, number | Источник `attractor / osm_bus / osm_highway / estimate`, уверенность по шкале 0–1 |
| `svc_critical_access, svc_review_flag, priority_diff` | boolean | Критический подъезд, нужна ручная проверка, отличие от исходного приоритета |
| `svc_ownership, svc_rationale` | string | Сейчас владение `unknown`. `rationale` — пояснение |
| `ksodd_<attr>?` | string | Значение из CSV, даже если по смыслу это число. В т.ч. `lanes`, `max_intensity_veh_per_h`, `max_intensity_per_day`, `load_factor`. Присутствует только при подтверждённой привязке без конфликта |
| `ksodd_<attr>_source?`, `_confidence?`, `_rationale?` | string | Источник, уверенность и основание для `ksodd_<attr>`, с полным префиксом соответствующего атрибута |
| `TrafficLight.lon, lat` | number / null | Географические координаты. Точки без координат нельзя рисовать или привязывать к маршруту |
| Остальные поля `TrafficLight` | string | `ksodd_id, address, osm_id, cluster_id, status, match, confidence, cycle_s, phases, note, rationale, source, addr_check`. `cycle_s` и `phases` сейчас пустые строки |
| `CriticalPoi.lon, lat` | number | WGS84 |
| Остальные поля `CriticalPoi` | string | `osm_id, kind, tag, name, zone, nearest_edge_id, distance_m, critical_access, bus_route_near, status, rationale`. Числа и флаги в этом слое тоже приходят строками |

Типы и источники можно сверить по [генератору v3](../mapprep/prep/build_v3.py), [классификатору](../mapprep/prep/classify_service.py) и [сборщику пакета](../mapprep/prep/integrate_map_v3.py).

| CSV на стыке карты | Ключ | Поля, которые нужны потребителю |
|---|---|---|
| `service_classification.csv` | `edge_id: string` | `passable, municipal_service, required_current_stage, critical_access, review_flag` → boolean из `True/False`, `priority, priority_weight` → int, `priority_confidence` → number, `priority_source, ownership, rationale` → string |
| `edge_weights.csv` | `edge_id: string` | `zone, lanes` → int, `width_dir_m, maxspeed, tl_delay_s, v_work_kmh, v_dead_kmh` → number при наличии значения. `highway` — string. `bus_stop` сейчас записывает `0/1` из признака автобусного маршрута, `near_poi` и `tl_delay_s` в базовой выгрузке пустые |
| `map_package_v3_review.csv` | Пара `table, entity` для поиска источника, может повторяться | `table, entity, status, confidence, reason, source` — строки, одна строка на замечание |

CSV хранит значения в виде текста. Пустая ячейка неизвестной величины остаётся пропуском при преобразовании, особенно для `maxspeed` и `tl_delay_s`.

## 3. Сценарий на входе prep

Поддерживаемый формат — XLSX с четырьмя листами. Ниже перечислены поля, участвующие в подготовке задач плугов, и поля, которые важно отличать от фактически применяемых. Полный список колонок задаёт [загрузчик Excel](../work/prep/prep/excel.py).

| Лист | Колонка или значение | Тип после чтения | Как используется |
|---|---|---|---|
| Типы техники | `тип (модель), класс` | string | Ключ для строки «Машины», классы плугов `КДМ` и `грейдер` |
| Типы техники | `ширина_габарит_м, макс_ширина_уборки_м` | number | Габарит для проезда, рабочая ширина для количества проходов, метры |
| Типы техники | `скорость_уборки_кмч, скорость_перегона_кмч, href_снега_см` | number | км/ч, км/ч, см. Пустой `href_снега_см` → 20. Пустая скорость → 0 |
| Машины | `id, тип, кол-во` | string, string, int | ID, ссылка на модель, количество (по умолчанию 1). Группа раскладывается в ID с суффиксами |
| Машины | `старт_lon, старт_lat, финиш_lon, финиш_lat` | number | Координаты связываются с ближайшими узлами конкретной карты |
| Машины | `старт_адрес, финиш_адрес, водитель` | string / null | Описательные поля sidecar |
| Условия | `название строки, высота снега, коэффициент трафика, плотность` | string, number, number, string / null | Значения читаются по позиции. Для плугов работают высота (см) и коэффициент скорости (>0). Плотность сохраняется как метаданные |
| Точки | `id, вид, lat, lon, название` | строковые значения и координаты number | Лист читается. Старт/финиш плугов берётся из «Машины» |

Смены в текущем prep заданы константами `0` и `28800` секунд. Число проходов — `max(1, ceil(width_dir_m / max_work_width_m))`. Класс подходит по ширине при `width_m ≥ gabarit_m + 0.5`. Вес равен `priority_weight × length_m / 1000`. Для приоритета 1 текущий v2 prep передаёт мягкий дедлайн `10800` секунд с коэффициентом `50`. Этот параметр существующего прогона не переносится автоматически в новый сценарий v3. [Код подготовки](../work/prep/prep/build.py) · [формулы](../work/prep/prep/formulas.py)

## 4. Вход HGS

| JSON-путь | Тип | Обязательность | Значение |
|---|---|---|---|
| `n_nodes` | int | Да | Размер сжатого множества узлов N |
| `classes` | `Dict<string,{matrix:number[][]}>` | Да | По матрице на класс. N×N, строка откуда, столбец куда, секунды. `-1` в prep означает недостижимость, контракт принимает отрицательное значение |
| `tasks[]` | object | Да | Один объект соответствует одному направленному проходу |
| `tasks[].id, start_node, end_node` | int | Да | ID задачи уникален внутри instance, узлы — индексы `0..N-1` |
| `tasks[].service_time` | `Dict<string,number>` | Да | Секунды уборки по классам. Отсутствие класса означает несовместимость |
| `tasks[].weight` | number | Да | Коэффициент при времени завершения задачи |
| `tasks[].ready_time` | number | По умолчанию 0 | Самое раннее начало, секунды |
| `tasks[].deadline` | number / null | Нет | Мягкий срок завершения, секунды |
| `tasks[].deadline_weight` | number | По умолчанию 0 | Штраф за секунду опоздания |
| `vehicles[]` | object | Да | Запись на каждую машину |
| `vehicles[].id, class` | string | Да | ID машины и ключ в `classes` |
| `vehicles[].start_node, end_node` | int | Да | Индексы в матрице |
| `vehicles[].shift_start, shift_end` | number | Да | Секунды от начала сценария. Возврат после `shift_end` штрафуется в HGS |
| `objective` | object | Prep всегда передаёт | Числовые коэффициенты `w_latency, w_deadhead, w_wait, w_overtime, w_makespan` |
| `params` | object | Prep всегда передаёт | `time_limit_s: number`, `seed, pop_min, pop_gen, max_iter_no_improve: int` |

Минимальный иллюстративный instance с одним проходом и двумя узлами.

```json
{
  "n_nodes": 2,
  "classes": {"КДМ": {"matrix": [[0, 12], [15, 0]]}},
  "tasks": [
    {"id": 0, "start_node": 0, "end_node": 1, "service_time": {"КДМ": 30},
     "weight": 1, "ready_time": 0, "deadline": null, "deadline_weight": 0}
  ],
  "vehicles": [
    {"id": "M01", "class": "КДМ", "start_node": 0, "end_node": 0,
     "shift_start": 0, "shift_end": 28800}
  ],
  "objective": {"w_latency": 1, "w_deadhead": 0.2, "w_wait": 0, "w_overtime": 10, "w_makespan": 0},
  "params": {"time_limit_s": 10, "seed": 1, "pop_min": 25, "pop_gen": 40, "max_iter_no_improve": 20000}
}
```

## 5. Выход HGS

| JSON-путь | Тип | Значение |
|---|---|---|
| `status` | enum `ok / infeasible` | Статус решения |
| `routes[].vehicle` | string | Тот же ID, что `vehicles[].id` |
| `routes[].visits[].task` | int | Тот же ID, что `tasks[].id` |
| `routes[].visits[].arrive, start, end` | number | Секунды от начала сценария. `arrive ≤ start ≤ end`. Разность `start - arrive` — ожидание |
| `routes[].return_time` | number | Время возврата на конечный узел, от начала сценария, секунды |
| `routes[].deadhead, wait` | number | Длительности перегона и ожидания, секунды |
| `unassigned` | `int[]` | ID невыполненных задач. Для принятого полного маршрута массив пустой |
| `objective` | object | Числа `total, latency, deadhead, wait, overtime, makespan`. `lateness?` присутствует в сохранённом результате зоны 3. Компоненты и итог описаны в SPEC |
| `stats` | object | `iterations, seed: int`, `time_s, best_found_at_s: number` |

Согласованный пример решения для входа выше. Машина обслуживает ребро за 30 с и возвращается за 15 с. Итоговый score равен 30 + 0,2 × 15.

```json
{
  "status": "ok",
  "routes": [{"vehicle": "M01", "visits": [{"task": 0, "arrive": 0, "start": 0, "end": 30}],
              "return_time": 45, "deadhead": 15, "wait": 0}],
  "unassigned": [],
  "objective": {"total": 33, "latency": 30, "deadhead": 15, "wait": 0, "overtime": 0, "makespan": 45},
  "stats": {"iterations": 1, "time_s": 0.01, "best_found_at_s": 0.01, "seed": 1}
}
```

Интерфейс HGS задаёт [клиентская SPEC](../work/prep/input/SPEC.md). Числа в примерах иллюстративные, фактические файлы приведены в разделе 1.

## 6. Sidecar и GeoJSON

| Sidecar | Поля и типы | Зачем |
|---|---|---|
| `meta` | `zone: int`, `condition: object` (`name, snow_h_cm, traffic_coef, density_name`), `input_dir: string`, `warnings, skipped_machines: string[]`, `objective, params: object` | Условия и диагностика запуска |
| `classes` | `Dict<string,{v_transit_kmh:number,gabarit_m:number}>` | Восстановление путей |
| `nodes[]` | `i, id: int`, `lon, lat: number` | Связь `i` из instance с исходным node ID |
| `tasks[]` | `id, start_node, end_node, start_graph_node, end_graph_node, pass_index, passes_dir, priority: int`, `edge_id: string`, `street: string / null`, `geometry: [number,number][]`, `length_m, weight: number`, `service_time: Dict<string,number>` | Обратное преобразование задачи. `pass_index` начинается с нуля. Две задачи на одной дуге различаются индексом прохода |
| `vehicles[]` | `id, machine_id, class, type: string`, `driver, start_addr, finish_addr: string / null`, `start_node, end_node, start_i, end_i: int`, `snap_start_dist_m, snap_finish_dist_m, shift_start, shift_end: number` | Связь стартов, финишей и ID машин. `start_node/end_node` здесь — ID графа, `start_i/end_i` — индексы HGS |
| `graph` | `nodes[]{id:int,lon:number,lat:number}`, `edges[]{id,u,v,length_m,slope_pct,oneway,width_m,name,geometry,highway}` с типами из таблицы Edge | Граф перегона, в том числе вне выбранной зоны |

| GeoJSON результата | Тип | Значение |
|---|---|---|
| `type, features` | `"FeatureCollection", Feature[]` | Контейнер. Текущий экспорт также пишет `crs.properties.name="EPSG:4326"` |
| `features[].geometry` | `{type:"LineString",coordinates:[number,number][] }` | Координаты в порядке `[lon,lat]` |
| `properties.vehicle, kind` | string, `service / deadhead` | ID машины и вид движения |
| `properties.task, street` | int / null, string / null | Для перегона оба `null` |
| `properties.start_time, end_time` | string | `HH:MM`, отображаемое время из секунд сценария. Для точных расчётов используйте числовые времена HGS |
| `properties.edge_id, pass_index, priority` | string, int, int | Присутствуют только на `service` |

Связь записей выглядит так: `solution.routes[].visits[].task → sidecar.tasks[].id → (edge_id, pass_index) → дуга карты`. В текущем GeoJSON перегон может объединять несколько дуг в одну линию, и его `properties` не содержит списка этих дуг. Финальный перегон помечается `deadhead`. Отчёт по километрам доступен внутри `decode(pmap, solution)` как `Dict<vehicle_id,{service_km:number,deadhead_km:number,return_hhmm:string}>`. В CLI GeoJSON этот объект не выгружается. [Реализация decode](../work/prep/prep/decode.py)

## 7. HTTP, который доступен сейчас

Эти маршруты обслуживают локальный MVP на `http://127.0.0.1:8000`. Карта v3 и HGS доступны по файловым контрактам из раздела 1.

| Метод и путь | Вход | Ответ |
|---|---|---|
| `GET /api/basemap` | Без тела | `200` · `{bbox:number[4], layers:object}` со слоями базовой карты MVP |
| `GET /api/graph/edges` | Без тела | `200` · GeoJSON `FeatureCollection` рёбер симулятора, свойства `id, name, required, task_id, status, slope_pct, length_m` |
| `GET /api/plan` | Без тела | `200` · `Plan` из симулятора, `routes: Dict<vehicle_id,RouteStep[]>` |
| `POST /api/plan/solve` | JSON `{weights?: ObjectiveWeights / null, time_limit_s?: number / null}`. Пустое тело запроса замените JSON-объектом `{}` | `200` · `Plan`, рассчитанный текущим solver симулятора |

`ObjectiveWeights` содержит числовые поля `deadhead`, `time`, `fuel` с дефолтами `0.5`, `0.3`, `0.2`. Например, `{"weights":{"deadhead":0.7,"time":0.2,"fuel":0.1},"time_limit_s":5}`.

| Поля ответа MVP `Plan` | Тип |
|---|---|
| `id, solver` | string |
| `created_at, plan_start_t` | number, секунды симуляции |
| `weights` | `ObjectiveWeights` |
| `routes` | `Dict<string,RouteStep[]>`, ключ — ID машины |
| `metrics, vehicle_metrics` | `Metrics` и `Dict<string,Metrics>`. Поля `deadhead_km, service_km, total_time_min, fuel_l, truck_empty_km, truck_loaded_km, truck_fuel_l` — числа, `objective` — число или `null` |
| `unassigned_task_ids` | `string[]` |
| `RouteStep` | `kind: service / deadhead / unload / return`, `edge_id, task_id: string / null`, `from_node, to_node: int`, `length_m, t_start, t_end, fuel_l: number`, `loaded, done: boolean` |

[Роуты MVP](../backend/app/api.py) · [типы MVP](../backend/core/models.py) · [CONTRACTS.md](../CONTRACTS.md)

## 8. Предлагаемый HTTP v1 для карты и HGS

Это контракт для интеграции, по которому backend, карта, алгоритм и фронт могут согласовать работу. Маршруты `/api/v1/...` ниже пока не реализованы. Файл карты и принятый результат прогона хранятся по ID. Подготовка HGS выполняется асинхронно, чтобы не удерживать HTTP-запрос до окончания оптимизации.

| Метод и путь | Вход | Успешный ответ | Ошибки |
|---|---|---|---|
| `GET /api/v1/maps/{map_dataset_id}/zones` | `map_dataset_id: string` в пути | `200` · `{schema_version:1,map_dataset_id:string,zones:ZoneSummaryV1[]}` | `404` · неизвестный dataset |
| `GET /api/v1/maps/{map_dataset_id}/edges?zone_id=3` | `map_dataset_id: string`, `zone_id: int` в query, обязателен | `200` · `MapEdgesV1`, GeoJSON направленных дуг зоны | `404` · неизвестные dataset или зона, `422` · неверный тип |
| `POST /api/v1/route-runs` | JSON `CreateRouteRunV1` | `202` · `RouteRunStatusV1` со статусом `queued` и заголовком `Location` с URL статуса | `404` · неизвестные карта/зона/сценарий, `422` · неверные параметры или карта и сценарий несовместимы |
| `GET /api/v1/route-runs/{run_id}` | `run_id: string` | `200` · `RouteRunStatusV1` | `404` · неизвестный запуск |
| `GET /api/v1/route-runs/{run_id}/result` | `run_id: string` | `200` · `RouteResultV1` для принятого решения | `404` · неизвестный запуск, `409` · принятого результата нет, текущий статус доступен по URL запуска |

### DTO карты

| DTO | Поля | Тип и правило |
|---|---|---|
| `ZoneSummaryV1` | `zone_id, name, hull_geojson` | `int, string, GeoJSON Feature<Polygon>`. `zone_id` имеет смысл в пределах `map_dataset_id` |
| `MapEdgesV1` | `type, schema_version, map_dataset_id, zone_id, features` | `"FeatureCollection", 1, string, int, Feature<LineString, MapEdgePropertiesV1>[]` |
| `MapEdgePropertiesV1` | `edge_id, from_node_id, to_node_id, street_name` | `string, int, int, string / null`. ID берутся из одной версии карты |
| `MapEdgePropertiesV1` | `length_m, priority, passable, required_current_stage` | `number, 1/2/3, boolean, boolean`. Для v3 последние три поля читаются из `svc_priority, svc_passable, svc_required_current_stage` |

### DTO запуска и результата

| DTO | Поля | Тип и правило |
|---|---|---|
| `CreateRouteRunV1` | `map_dataset_id, scenario_id, zone_id` | `string, string, int`. Сценарий зарегистрирован заранее и указывает на конкретную версию исходного Excel |
| `CreateRouteRunV1` | `time_limit_s?, seed?` | `int > 0` (по умолчанию 10), `int ≥ 0` (по умолчанию 1) |
| `RouteRunStatusV1` | `run_id, status, error` | `string`, enum `queued / running / succeeded / infeasible / failed`, `ApiErrorV1 / null`. `succeeded` выставляется после проверки полного результата, `infeasible` соответствует отсутствию допустимого решения |
| `RouteResultV1` | `schema_version, run_id, map_dataset_id, scenario_id, zone_id` | `1, string, string, string, int` |
| `RouteResultV1` | `routes, metrics, unassigned_tasks` | `VehicleRouteV1[], MetricsV1, TaskRefV1[]`. У принятого результата `unassigned_tasks=[]` |
| `VehicleRouteV1` | `vehicle_id, return_time_s, segments` | `string, number, SegmentV1[]`. Сегменты упорядочены по времени |
| `SegmentV1` | `kind, start_s, end_s, edge_ids, geometry, length_m, task` | `service / deadhead / wait`, два `number` в секундах, `string[]`, `LineString / null`, `number` в метрах, `TaskRefV1 / null` |
| `TaskRefV1` | `edge_id, pass_index` | `string, int ≥ 0`. Проход уникален в рамках данной карты и запуска |
| `MetricsV1` | `service_m, deadhead_m, makespan_s, objective_score` | `number`. Длины считаются по дугам с учётом повторных проездов. `makespan_s` — максимум `return_time_s`. `objective_score` — `objective.total` HGS |
| `ApiErrorV1` | `code, message, field?` | `string, string, string`. `code` для клиента, `message` для человека, `field` указывает на поле ввода при наличии |

`start_s` и `end_s` измеряются в секундах от начала сценария. Для `service` ровно один `edge_id` и заполнен `task`. Для `deadhead` `task=null` и список дуг непрерывен в направлении проезда. Для `wait` длина 0, `edge_ids=[]`, `geometry=null`, `task=null`. Числовые ID HGS остаются в instance/sidecar. Перед публикацией результата проверяются совпадение версии карты, покрытие проходов, направления, временная последовательность и связность маршрута.

Пример запроса создания прогона и ответа `202`. Идентификаторы примерные.

```json
{"map_dataset_id":"petropavlovsk-v3-2026-11-22","scenario_id":"snow-medium-01","zone_id":3,"time_limit_s":10,"seed":1}
```

```json
{"run_id":"run-001","status":"queued","error":null}
```

Например, `Location: /api/v1/route-runs/run-001`. HTTP ошибки v1 возвращают `{"error": ApiErrorV1}`. Для `failed` и `infeasible` поле `error` в ответе статуса заполнено. Примеры кодов — `unknown_dataset`, `invalid_zone_id`, `result_unavailable`, `solver_infeasible` и `validation_failed`.

Пример ответа `GET /api/v1/maps/petropavlovsk-v3-2026-11-22/edges?zone_id=3`. Дуга и координаты иллюстративные.

```json
{
  "type": "FeatureCollection",
  "schema_version": 1,
  "map_dataset_id": "petropavlovsk-v3-2026-11-22",
  "zone_id": 3,
  "features": [{
    "type": "Feature",
    "geometry": {"type": "LineString", "coordinates": [[158.65,53.04],[158.651,53.04]]},
    "properties": {
      "edge_id": "101-102-0", "from_node_id": 101, "to_node_id": 102, "street_name": null,
      "length_m": 67, "priority": 1, "passable": true, "required_current_stage": true
    }
  }]
}
```

Пример ответа `GET /api/v1/route-runs/run-001/result` для однозадачного сценария. Финиш машины совпадает с концом обслуженного ребра.

```json
{
  "schema_version": 1,
  "run_id": "run-001",
  "map_dataset_id": "petropavlovsk-v3-2026-11-22",
  "scenario_id": "snow-medium-01",
  "zone_id": 3,
  "routes": [{
    "vehicle_id": "M01",
    "return_time_s": 30,
    "segments": [{
      "kind": "service", "start_s": 0, "end_s": 30,
      "edge_ids": ["101-102-0"],
      "task": {"edge_id": "101-102-0", "pass_index": 0},
      "geometry": {"type": "LineString", "coordinates": [[158.65,53.04],[158.651,53.04]]},
      "length_m": 67
    }]
  }],
  "metrics": {"service_m": 67, "deadhead_m": 0, "makespan_s": 30, "objective_score": 30},
  "unassigned_tasks": []
}
```

Карта отвечает за выпуск привязанных к версии ID и смысл `svc_*`. Алгоритм отвечает за преобразование задач, запись дуг маршрута и проверку решения. Backend регистрирует карты и сценарии, выдаёт HTTP DTO. Клиент использует числовые времена и один и тот же `map_dataset_id` для геометрии и маршрутов.

