# Задача: пересобрать граф города из свежего OSM → graph_v3.json (+ слитие КСОДД/POI, веса рёбер для HGS)

Рабочая папка: `mapprep/`. Новый скрипт `mapprep/prep/build_v3.py` (можно импортировать функции из `prep/run_prep.py`: parse_lanes, parse_width, parse_maxspeed, default_lanes, zones_partition и т.д. — не ломать сам run_prep.py). Windows, Python, запуск с `-I`. Не трогать файлы вне `mapprep/` (кроме ЧТЕНИЯ `mapdata/data/`). Отчёт — `mapprep/REPORT_v3.md` (по-русски, коротко).

## Вход
- `../mapdata/data/osm_petropavlovsk.json` — Overpass JSON (out geom), 9163 дорог, светофоры, переходы, автобусные relation, остановки.
- `out/graph_v2.json` — образец схемы (meta/nodes/edges) и высоты узлов; `input/elevation_cache.json`.
- `../mapdata/data/*.csv` — CSV от `mapdata/TASK_poi_ksodd.md`: `ksodd_street_match.csv`, `bus_stops.csv`, `traffic_lights_match.csv`, `poi.csv` (если задачи mapdata выполнены; иначе пропустить пункты 9–12 и отметить в отчёте).

## Что сделать
1. **Дороги.** Берём way с highway ∈ {motorway, trunk, primary, secondary, tertiary (+ _link), unclassified, residential, living_street, service}. service — только без service=parking_aisle/driveway (их выкинуть). Разрезать way на рёбра по перекрёсткам (узел общий у ≥2 дорог или конец way). Учесть oneway (yes/-1/true/1). Для двусторонней — два ребра u→v и v→u с `reverse_edge_id`. Оставить крупнейшую сильно связную компоненту; что выкинуто — в отчёт.
2. **Схема ребра и узла — как в graph_v2** (id "u-v-key", length_m, slope_pct, geometry [lon,lat], name, highway, oneway, required, street_id, reverse_edge_id, task_id, lanes, width_m, width_dir_m, maxspeed, osm_way_ids, bus_route, priority, zone). Правила lanes/width/maxspeed/priority — как в `prep/README.md`.
3. **Источник у каждого значения.** Для lanes, width_m, maxspeed, priority, elevation_m — поле `<имя>_source` ∈ {osm, ksodd, estimate}. (В v2 было `default` → теперь `estimate`; `lanes`-вывод ширины → `estimate`.) Значения из КСОДД на этом шаге не подставлять — только оставить место.
4. **Высоты.** Узлы, совпавшие с узлами v2 (координаты до 6 знаков), берут их elevation_m, source=`srtm`. Для остальных — попробовать https://api.opentopodata.org/v1/srtm30m (батчи по 100 точек, пауза 1.1 с, не больше 1000 запросов), source=`srtm`; при недоступности — IDW по 8 ближайшим известным узлам, source=`estimate`. Кеш дописывать в `input/elevation_cache_v3.json`. slope_pct = Δh/length·100.
5. **Узлы.** `kind`: junction | traffic_signals | crossing | end. Светофор из OSM (highway=traffic_signals) — на узел графа, если он узел дороги; если в 25 м от перекрёстка — на ближайший junction. Поле `traffic_signals_source`=osm.
6. **Автобусы.** relation route=bus/trolleybus → way-члены → bus_route=true на рёбрах, priority=1.
7. **Зоны.** 9 зон тем же алгоритмом (zones_partition и правила владения из README). Нумерация с севера на юг.
8. **Выход:** `out/graph_v3.json` (meta: дата, источник, число узлов/рёбер, сумма км), `out/zones_v3.json`, `out/prep_stats_v3.json`; `out/check_map_v3.html` через `prep/build_check_map.py` (параметризовать путь, не ломая старый вызов) — светофоры отдельным слоем.
9. **Значения КСОДД (дополнение).** `lanes.csv` / `road_categories.csv` из `mapdata/data/ksodd_tables/`, сверенные с названиями v2 через `ksodd_street_match.csv` (только `match=yes`): подставлять lanes/категорию с `lanes_source="ksodd"` / `category_source="ksodd"`; uncertain — не подставлять. Значения, пришедшие ни от OSM ни от КСОДД — `estimate`.
10. **Светофоры (дополнение).** Для узлов `kind=traffic_signals` поле `tl_cycle_s`: из `traffic_lights_match.csv`/КСОДД если `cycle_s` заполнен, иначе 75 (среднее 60–90), `tl_cycle_source` = ksodd|estimate. На каждое ребро, входящее в узел со светофором, `tl_delay_s` = цикл/3 (учёт задержки в стоимости рёбер пока НЕ делать — только данные), `tl_delay_source="estimate"`.
11. **Остановки (дополнение).** Ближайший junction в ≤30 м от остановки из `bus_stops.csv` → на узле `bus_stop=true, bus_stop_ref="<название/маршруты>"`; остановки без узла — в `out/poi_v3.json` рядом с POI.
12. **POI (дополнение).** `poi.csv` (больницы/клиники/администрации) → `out/poi_v3.json` (`{kind, lon, lat, name, zone, nearest_edge_id}`) и на рёбрах в ≤150 м — `near_poi="<kind>"` (приоритет НЕ менять, только флаг).
13. **Таблица весов для HGS (дополнение).** `out/edge_weights.csv`: `edge_id, zone, highway, lanes, width_dir_m, maxspeed, tl_delay_s, bus_stop, near_poi, v_work_kmh, v_dead_kmh` — скорости по классу: v_work = min(20, базовая), базовая: trunk/primary/secondary 20, tertiary 15, прочее 10 (КДМ рабочая ~20 км/ч — см. CLAUDE.md «поправке справочника»); v_dead: trunk/primary/secondary 40, tertiary 30, прочее 25; если maxspeed задан и меньше базовой v_dead — v_dead = maxspeed; service = 10. Формулы — в meta отчёта. И `out/mean_weights.json`: средние по (zone, highway): среднее v_work, v_dead, доля рёбер с tl_delay>0, доля bus_stop — чтобы калибровать средние веса рёбер в HGS.

## Готово, когда
- graph_v3.json грузится, все рёбра имеют все поля схемы и *_source; у каждого двустороннего ребра есть обратное в той же зоне;
- граф сильно связный; 9 зон, каждая связная;
- REPORT_v3.md: число узлов/рёбер/км против v2 (1700 рёбер), покрытие lanes/width/maxspeed/tl_cycle по источникам (osm/ksodd/estimate), светофоров на узлах, автобусных рёбер, км по зонам, сколько значений КСОДД реально легло в граф, что не получилось;
- `out/edge_weights.csv` + `out/mean_weights.json` + `out/poi_v3.json` на месте (или причина пропуска в отчёте).
