# Задача: POI (больницы, мэрия) + автобусные остановки + сопоставление КСОДД с графом

Рабочая папка: `mapdata/` (Windows, Python с флагом `-I` — OSM-JSON не доверенный). Скрипты в `mapdata/scripts/`,
CSV в `mapdata/data/`, отчёт — дописать в конец `mapdata/REPORT.md` разделом «## POI и сопоставление КСОДД» (по-русски, коротко).
Не трогать файлы вне `mapdata/` (кроме ЧТЕНИЕГО `mapprep/out/graph_v2.json`).

## Вход
- `data/osm_petropavlovsk.json` — полная выгрузка (в ней: светофоры `highway=traffic_signals` (26),
  остановки `public_transport` (372), relation bus (94) с way-членами).
- `data/ksodd_tables/` — CSV: `traffic_lights.csv` (61 объект), `lanes.csv` (50 направлений),
  `road_categories.csv` (81 улица), `traffic_intensity.csv` (142 направления).
- `mapprep/out/graph_v2.json` — рёбра с `name` (названия улиц), для сопоставления.

## Что сделать
1. **POI (новое, Overpass).** ОSM-выгрузке нет зданий. Скачать МИНИАТУРНЫЙ запрос (out center tags):
   `node/way/rel` внутри bbox города (см. `scripts/overpass_query.ql`) с тегами
   `amenity~"hospital|clinic"`, `office=government`, `building=public`, `place~"city|town"` →
   `data/raw/osm_poi.json` (зеркала по очереди: overpass.kumi.systems → overpass-api.de; при 429/504
   пауза 30 с, 2 повтора, потом следующее; логируй в `data/overpass_poi_attempts.log`).
   Парсинг → `data/poi.csv`: `osm_id, lon, lat, kind (hospital|clinic|government|public), name, osm_tags`.
   kind: hospital/clinic из amenity, government из office=government (мэрия, администрации),
   public из building=public без других срабатываний.
   Если оба зеркала не дали файл — в отчёте «заблокировано, POI из сырой выгрузки нет» и идти дальше.
2. **Автобусные остановки.** Из `osm_petropavlovsk.json` все node с `public_transport` →
   `data/bus_stops.csv`: `osm_id, lon, lat, name, route (ref/маршруты через relation route=bus, к которым
   относится остановка — через relations members type=node)`. Маршруты: 94 relation `route=bus` с
   `ref`/`name`.
3. **Светофоры.** ОSM (26 node `traffic_signals`) → `data/traffic_signals_osm.csv`
   (`osm_id, lon, lat, direction (traffic_signals:direction), nearest_crossing (имя ближайшей улицы
   если у way с name в 25 м)`).
   **Сопоставление с КСОДД**: адреса/названия из `ksodd_tables/traffic_lights.csv` (колонки — смотри
   файл) — строковое фаззи-сопоставление (нормализация: нижний регистр, «ул»/«улица», опечатки
   допустимы через difflib ratio ≥0.75) → колонки `ksodd_ref, match (yes/no), note`. Выйти
   `data/traffic_lights_match.csv` (объединённые 61+26: у OSM-объектов без пары `ksodd_ref` пуст,
   у КСОДД-объектов без пары `lon/lat/osm_id` пустые).
4. **Улицы КСОДД ↔ graph_v2.** `ksodd_tables/lanes.csv` и `road_categories.csv` (колонка с улицей —
   смотри файлы) сопоставить с `name` рёбер в `mapprep/out/graph_v2.json` (те же нормализации,
   difflib ≥0.8; «пр. Лесная» = «проспект Лесная» — сокращения раскрыть) →
   `data/ksodd_street_match.csv`: `ksodd_file, ksodd_street, v2_street, score, match (yes|no|uncertain),
   values (JSON-строка: что берём — lanes/ширина/категория)`. Только matched|uncertain — дальше
   слиться в граф; не выдумывать значения.
5. **Статистика**: число POI по kind, остановок, светофоров OSM vs КСОДД (сколько совпало),
   улиц КСОДД совпавших с v2.

## Готово, когда
- все 5 CSV в `data/` (UTF-8, BOM без, запятая — разделитель, кавычки при запятых);
- `data/raw/osm_poi.json` (или лог блокировки в отчёте);
- в REPORT.md новый раздел: файлы, числа, какие зеркала сработали, что заблокировано/не совпало.
