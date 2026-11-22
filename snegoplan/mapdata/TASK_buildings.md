# Задача: скачать здания и точки притяжения Петропавловска-Камчатского (для модели потоков)

Рабочая папка: `mapdata/`. Скрипты в `scripts/`, данные в `data/`, отчёт — дописать в `REPORT.md` раздел «## Здания и POI». Windows, Python с `-I`. Можно переиспользовать `scripts/fetch_overpass.py`, `scripts/curl_get.py`. TLS не отключать. Зеркала Overpass по очереди: overpass.kumi.systems (раньше работало), overpass-api.de, overpass.private.coffee. При 429/504 — пауза 30 с, 2 повтора, затем следующее зеркало. bbox города: 52.95,158.45,53.15,158.80 (тот же, что в `scripts/overpass_query.ql`, если там другой — бери оттуда).

## 1. Скачать (out center tags — геометрия не нужна, только центр)
- Жильё: building ∈ {apartments, residential, house, detached, dormitory, semidetached_house, terrace, yes с building:levels и addr:*}. Нужны теги building, building:levels, addr:street, addr:housenumber + площадь контура → для площади один раз `out geom` только для жилых и посчитать площадь в м² (проекция UTM 57N или локальная equirectangular).
- Притяжение: office=*, shop=*, amenity ∈ {school, kindergarten, university, college, hospital, clinic, marketplace, bank, cafe, restaurant, fuel, place_of_worship, townhall}, building ∈ {commercial, retail, office, industrial, warehouse, public}, landuse ∈ {industrial, commercial, retail, port} (центр полигона + площадь), man_made=pier / industrial=port.

## 2. Выход
- `data/residential.csv`: osm_id, lon, lat, building, levels (пусто если нет), area_m2, addr.
- `data/attractors.csv`: osm_id, lon, lat, kind (work | school | shop | health | service | industry | port), tag (исходный тег), area_m2 (если есть).
- Никаких оценок населения здесь не делать — только сырые данные.

## Готово, когда
- оба CSV есть, в UTF-8;
- в REPORT.md: число жилых зданий (и сколько с levels), сумма жилой площади (м² × этажи, где есть), число POI по kind, какое зеркало сработало.
