# Диагноз доступности Overpass (проверка 2026-11-22, повтор после таймаута Kimi)

Метод: тестовый запрос `node(53.00,158.60,53.03,158.66)[amenity=cafe];out count;`
через curl.exe (schannel) и Python urllib (OpenSSL), `--noproxy '*'`, TLS не отключался.
Домашние конфиги не читались; переменных прокси в среде нет.
Ранее в логе встречался мёртвый локальный прокси `127.0.0.1:10809` —
вероятно, обычный выход в сеть шёл через локальный прокси/VPN, который сейчас выключен.

## Проверенные endpoints

| Endpoint | curl (schannel) | Python (OpenSSL) | Итог |
|---|---|---|---|
| https://overpass.private.coffee/api/interpreter | timeout 20/60 с, 0 байт | timeout ~47 с ×2 | недоступен |
| https://overpass-api.de/api/interpreter | SEC_E_ILLEGAL_MESSAGE (TLS alert) | 502 Bad Gateway / timeout ~46 с | недоступен |
| https://overpass.kumi.systems/api/interpreter | timeout 15/60 с, 0 байт | timeout ~46 с ×2 (ранее: 504) | недоступен |
| https://maps.mail.ru/osm/tools/overpass/api/interpreter | 504 Gateway Time-out ×2 (90 с) | timeout ~46–50 с ×2 | фронт отдаёт HTML, бэкенд Overpass не отвечает |
| https://overpass.openstreetmap.ru/api/interpreter | — | TLS handshake timeout ~50 с | недоступен |
| https://overpass.nchc.org.tw/api/interpreter | — | SSL UNEXPECTED_EOF ~26 с | недоступен |
| https://overpass.feste-ip.net/api/interpreter | — | SSL UNEXPECTED_EOF ~26 с | недоступен |

## Диагноз
- Ни одно из 7 публичных зеркал Overpass не отвечает на запросы с этого ПК:
  типичные картины — TCP timeout с 0 байт, обрыв TLS handshake (schannel alert /
  UNEXPECTED_EOF), редко 502/504 от промежуточного nginx.
- Сеть режет/обрывает длительные ответы внешних API; раньше трафик, судя по логу,
  шёл через локальный прокси 127.0.0.1:10809, который сейчас не слушается.
- Скачивание зданий/POI в этом сеансе невозможно. Рекомендации:
  1) включить привычный VPN/прокси и повторить `scripts/fetch_buildings.py`
     (он уже ходит по зеркалам с паузами);
  2) либо скачать выгрузку с другого устройства/сети и положить
     `data/osm_residential.json`, `data/osm_attract_center.json`,
     `data/osm_attract_poly.json` — дальше `scripts/build_buildings.py`
     отработает офлайн.
