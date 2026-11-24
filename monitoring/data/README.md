# Городской слой поиска Петропавловска-Камчатского

© OpenStreetMap contributors, [ODbL 1.0](https://www.openstreetmap.org/copyright).
Границы поиска определяет [отношение OSM 4139922](https://www.openstreetmap.org/relation/4139922).
Слой содержит 8 147 отобранных объектов с адресами, названиями мест или улиц и
узлы, нужные для восстановления их геометрии. OSM ID сохранены.

| Источник | Версия / SHA-256 |
| --- | --- |
| [Региональный PBF Geofabrik](https://download.geofabrik.de/russia/far-eastern-fed-district.html) | OSM replication 4016 от 23.11.2026 21:21:17 UTC |
| Региональный PBF | `d014d66c4f047354faa809608189ef6d535d716f85b16c604bd412296b4a5f14` |
| Граница `city-sim/weather_coverage/osm/city-boundary.osm.gz` | `f157a0bea01282664497b8a462387ca78348f51e23d6fa90d00144193963fad4` |
| `search-city.osm.gz` | `030c27caa28448f2c24d58806e8cbb512460f33d8c18533fe129b0c93aa78508` |

Для обновления скачайте региональный PBF и сверяйте его контрольную сумму с
публикацией Geofabrik. Из корня репозитория запустите эти команды и обновите
версию и хеши в таблице. Извлечение использует Shapely и osmium 4.3.2.

```sh
python -m scripts.extract_search_osm /path/to/far-eastern-fed-district.osm.pbf monitoring/data/search-city.osm.gz
python -m pytest tests/test_map_search.py
```

Сервис читает снимок с диска при первом запросе. Для обновления запущенного
контейнера пересоберите образ и перезапустите его обычным способом.
