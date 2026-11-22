# Задача: максимально выкачать данные карты Петропавловска-Камчатского (в /home/claude/mapdata)
Работай в этой папке. Скрипты клади в scripts/, данные в data/, отчёт в REPORT.md (по-русски, коротко).
Разрешены Bash(python3, curl). НЕ обходи прокси, TLS не отключай. При 403/407 или connection reset — 1 повтор, затем запиши хост в REPORT.md как заблокированный и иди дальше.

## 1. OSM через Overpass (bbox города ~ 52.9,158.5,53.2,158.8; уточни)
Попробуй зеркала по очереди: overpass-api.de, overpass.kumi.systems, overpass.private.coffee, maps.mail.ru/osm/tools/overpass.
Скачай JSON (out geom/tags) для всего города:
- highway=* (все теги: lanes, lanes:forward/backward, turn:lanes, width, maxspeed, surface, oneway, name, ref, bus, lit, incline)
- highway=traffic_signals, crossing, stop, give_way; направленные (traffic_signals:direction)
- public_transport / highway=bus_stop; relation route=bus/trolleybus
- amenity=parking, parking_lane tags; барьеры; mapped steps/bridge/tunnel
- natural/landuse нужен не будет.
Если Overpass недоступен — попробуй osmnx/pip (`pip install osmnx --break-system-packages`) или geofabrik/download.geofabrik.de russia/far-eastern-fed-district (extract только если размер разумный <500MB, иначе пропусти).
Сохрани data/osm_*.json, статистику (сколько ребёр с lanes/maxspeed/width, сколько светофоров) в REPORT.md.

## 2. КСОДД PDF
https://pkgo.ru/upload/medialibrary/d1a/d1a47f280e3871b728a1a70a7508f5b5.pdf  -> data/ksodd.pdf
Если скачалось: pdftotext -layout + pdfplumber таблиц -> data/ksodd_text.txt, data/ksodd_tables/*.csv. В REPORT.md перечисли, что нашлось (светофорные объекты, категории дорог, интенсивность, ширины, полосы, циклы светофоров).

## 3. Прочее (по возможности, 1 попытка на каждое)
Перечень дорог местного значения / категории дорог, ПКРТИ Петропавловска (npa.pkgo.ru, pkgo.ru). Ищи ссылки через kimi web search если есть.

## Критерии готовности
REPORT.md: что скачано (файлы, размеры), что заблокировано (хосты), что нужно скачать пользователю вручную (точные ссылки).
Ничего не придумывай; не печатай ключи.
