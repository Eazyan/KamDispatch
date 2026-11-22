# Отчёт: выгрузка данных карты Петропавловска-Камчатского

Дата: 2026-11-21. Рабочая папка: `/home/claude/mapdata` (скрипты в `scripts/`, данные в `data/`).

## 1. OSM / Overpass — ✅ скачано
- Файл: `data/osm_petropavlovsk.json` — 13 683 800 байт, bbox 52.9,158.5–53.2,158.8, формат out body geom.
- Источник: `https://overpass.kumi.systems/api/interpreter` (первое рабочее зеркало).
- Лог попыток: `data/overpass_attempts.log`.
- Состав выгрузки: все `way[highway]` + `node[highway]`, `public_transport`, `relation[route~"bus|trolleybus"]`, `amenity=parking`, `parking_lane`, `barrier`.

### Статистика (`data/osm_stats.txt`)
- Элементов: 11 787 (ways 10 333, nodes 1 360, relations 94).
- Дороги: 9 163 way с `highway=*` (primary 215, secondary 200, tertiary 108, residential 869, service 4694, footway 928, track 870, path 449, steps 429, unclassified 303, living_street 59 …).
- Разметка/параметры: `lanes` — 559 way, `lanes:forward` — 305, `lanes:backward` — 302, `turn:lanes*` — 41/94/88, `maxspeed` — 594, `width` — 57, `surface` — 2432, `oneway` — 232, `lit` — 557, `incline` — 275, `name` — 1193, `ref` — 81, `bridge` — 36, `tunnel` — 5.
- Светофоры: 26 node `highway=traffic_signals`; направленные `traffic_signals:direction` — 34 (forward 15, backward 18, both 1).
- Переходы: 279 node `crossing`; знаков `stop`/`give_way` в выгрузке нет (0).
- Остановки: 372 node с `public_transport` (включая `highway=bus_stop`; чистых тега `bus_stop` нет — mapped через PTv2).
- Маршруты: 94 relation `route=bus` (троллейбусов нет).
- Парковки: 360 объектов `amenity=parking`; `parking:lane*` почти не размечен (1 way).
- Барьеры: 341 node.

## 2. КСОДД PDF — ❌ заблокировано
- `https://pkgo.ru/upload/medialibrary/d1a/d1a47f280e3871b728a1a70a7508f5b5.pdf` — 2 попытки (urllib + curl), обе: **Connection reset by peer** (HTTP 000).
- Таблицы `data/ksodd_tables/` пусты, текст не извлекался.

## 3. Прочие источники — ❌ заблокировано
- `https://pkgo.ru/` и `https://npa.pkgo.ru/` — 1 попытка каждый, **Connection reset by peer**.

## Заблокированные хосты
| Хост | Симптом |
|---|---|
| overpass-api.de | connection reset (2 попытки) |
| pkgo.ru | connection reset (2 попытки, PDF и главная) |
| npa.pkgo.ru | connection reset (1 попытка) |

Не проверялись (не потребовались): overpass.private.coffee, maps.mail.ru (Kumi отдал данные с первой попытки).

## КСОДД и документы (обновление, 21.11.2026 — с локального ПК)

Разделы 2–3 выше описывали блокировку из облачного воркера. С локального ПК pkgo.ru доступен, всё скачано.

### Скачанные файлы (`data/raw/`)
| Файл | URL | Размер |
|---|---|---|
| `ksodd.pdf` | https://pkgo.ru/upload/medialibrary/d1a/d1a47f280e3871b728a1a70a7508f5b5.pdf | 28 903 178 байт |
| `pkrti_transport_programma_2006.pdf` | https://pkgo.ru/upload/iblock/e52/e52a6ae322229c72688696734c50daf2.pdf | 1 983 426 байт |

- КСОДД Петропавловск-Камчатского г.о., 2021–2035, 250 стр., текстовый слой есть.
- Постановление администрации от 14.09.2021 № 2006 — муниципальная программа «Развитие транспортной системы ПКГО» (25 стр., текстовый слой есть; ПКРТИ упомянута в перечне документов, п. 3). Отдельного документа «ПКРТИ» на сайте найти не удалось.

### Таблицы (`data/ksodd_tables/`, CSV UTF-8, скрипт `scripts/parse_ksodd.py`)
| Файл | Строк | Источник |
|---|---|---|
| `traffic_lights.csv` | 61 светофорный объект (табл. 2.5.1) | ksodd.pdf стр. 42–44 |
| `traffic_intensity.csv` | 142 направления | ksodd.pdf стр. 65–70 (табл. 2.9.1, среднесуточные ед/сутки по 43 точкам замера) |
| `lanes.csv` | 50 направлений (7 пересечений) | ksodd.pdf стр. 73–77 (табл. 2.9.3: число полос, пропускная способность, макс. интенсивность авт/час, коэф. загрузки) |
| `road_categories.csv` | 81 улица | ksodd.pdf стр. 35–38 (табл. 2.4.2.1: протяжённость улицы и участка в ненормативном состоянии) |

### Чего нет / не удалось
- **Циклы и число фаз светофоров** — в КСОДД не приведены (колонки `cycle_s`, `phases` пустые); есть только адресная привязка 61 объекта (9 из них на трассе А-401, отмечены в `note`).
- **Категории улиц** отдельной таблицей в КСОДД нет (колонка `category` пустая); заполнена только протяжённость из табл. 2.4.2.1.
- **Перечень автомобильных дорог общего пользования местного значения** как отдельный документ на pkgo.ru не найден (поиск по сайту, разделы «НПА», «Дорожное хозяйство», «Муниципальные программы»).
- **https://npa.pkgo.ru/** — недоступен (HTTP 404 на корень, 2 попытки).
- Год замеров интенсивности в табл. 2.9.1 явно не указан (КСОДД 2021–2035, обследование ~2020–2021), колонка `year` пустая.

## Здания и POI

Дата: 2026-11-22. Источник вместо Overpass (Overpass дал ошибки, по TASK_buildings.md не повторялся): официальный PBF Geofabrik `https://download.geofabrik.de/russia/far-eastern-fed-district-latest.osm.pbf` (зеркала Overpass не использовались). Скачан `curl.exe -q -L`, TLS (Schannel), max-time 180; HEAD-проверка: 401 217 335 байт (< лимита 500 МБ), скачано за ~65 с, размер совпал с Content-Length. Сырой файл: `data/raw/far-eastern-fed-district-latest.osm.pbf`.

### Обработка (скрипт `scripts/build_pbf_buildings.py`, pyosmium)
- pyosmium в изолированном venv `data/raw/pyosmium_venv` (pip `--timeout 15 --retries 1`), системно не установлен.
- bbox взят из `scripts/overpass_query.ql`: 52.9,158.5–53.2,158.8 (не из TASK_buildings.md).
- Один проход по PBF (`with_locations`): ноды — по координатам в bbox, веи — целиком, если любая точка в bbox (семантика complete ways); центроид и площадь — шнуровка в локальной equirectangular-проекции (м). Relations (landuse/port) — центроид и суммарная площадь по member ways (без вычитания дырок).

### Выход (UTF-8)
- `data/residential.csv` — 6 694 жилых здания; из них 1 889 с `building:levels`; Σ площадь контура 2 602 255 м²; Σ площадь×этажи (где levels есть) 4 380 278 м². Колонки: osm_id, lon, lat, building, levels, area_m2, addr.
- `data/attractors.csv` — 2 157 POI; 945 с площадью, Σ 8 013 558 м². По kind: shop 1 162, work 314, industry 254, service 210, school 96, health 60, port 61. Колонки: osm_id, lon, lat, kind, tag, area_m2.
- Проверка: строк 6 694 / 2 157 (совпадает со статистикой скрипта), битых строк 0, координаты в bbox у 99.7–99.9% (15 объектов — веи, пересекающие границу bbox, центроид чуть за краем; допустимо). Оценки населения не делались — только сырые данные.

## Скачать вручную пользователю (устарело — см. раздел «КСОДД и документы»)
1. КСОДД: https://pkgo.ru/upload/medialibrary/d1a/d1a47f280e3871b728a1a70a7508f5b5.pdf → положить в `data/ksodd.pdf`, затем:
   `pdftotext -layout data/ksodd.pdf data/ksodd_text.txt` и таблицы через pdfplumber в `data/ksodd_tables/`.
2. Перечень дорог местного значения / категории дорог, ПКРТИ: искать на https://npa.pkgo.ru/ и https://pkgo.ru/ (разделы НПА / дорожная деятельность) с локальной сети.
3. Если понадобится более свежая/полная выгрузка OSM: зеркала overpass-api.de недоступны из этой сети, использовать overpass.kumi.systems (запрос в `scripts/overpass_query.ql`).
