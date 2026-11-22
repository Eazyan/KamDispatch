#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Загрузка автобусных маршрутов Петропавловска-Камчатского из Overpass API.

Запускать на Windows с интернетом:   python scripts\\fetch_bus_routes.py
Результат: out/bus_routes.json  {"way_ids": [...], "names": [...], "routes": [...], "street_names": []}
Подготовка карты (prep/run_prep.py) поднимает до приоритета 1 рёбра, чьи OSM-way входят в way_ids.
Поле street_names - необязательный ручной список названий улиц (тоже -> приоритет 1).
Только стандартная библиотека.
"""
import json, os, sys, time, urllib.parse, urllib.request

BBOX = (52.98, 158.57, 53.11, 158.73)   # south, west, north, east
SERVERS = ["https://overpass-api.de/api/interpreter",
           "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.private.coffee/api/interpreter"]
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "bus_routes.json")

QUERY = """[out:json][timeout:180];
rel["route"="bus"](%s,%s,%s,%s)->.r;
.r out body;
way(r.r);
out tags;""" % BBOX

def fetch():
    data = urllib.parse.urlencode({"data": QUERY}).encode()
    last = None
    for url in SERVERS:
        for attempt in range(2):
            try:
                print("Запрос к %s (попытка %d)..." % (url, attempt + 1))
                req = urllib.request.Request(url, data=data, headers={"User-Agent": "snegoplan-mapprep/1.0"})
                with urllib.request.urlopen(req, timeout=200) as r:
                    return json.loads(r.read().decode("utf-8"))
            except Exception as ex:
                last = ex
                print("  ошибка: %s" % ex)
                time.sleep(3)
    raise SystemExit("Не удалось получить данные Overpass: %s\nПроверьте интернет/прокси и повторите." % last)

def main():
    js = fetch()
    rels = [e for e in js["elements"] if e["type"] == "relation"]
    ways = {e["id"]: e for e in js["elements"] if e["type"] == "way"}
    routes, way_ids = [], set()
    for r in sorted(rels, key=lambda r: r["id"]):
        ids = sorted({m["ref"] for m in r.get("members", []) if m["type"] == "way" and m["ref"] in ways})
        way_ids.update(ids)
        t = r.get("tags", {})
        routes.append({"relation_id": r["id"], "ref": t.get("ref"), "name": t.get("name"), "way_ids": ids})
    names = sorted({w["tags"]["name"] for i, w in ways.items() if i in way_ids and "name" in w.get("tags", {})})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"way_ids": sorted(way_ids), "names": names, "street_names": [], "routes": routes},
                  f, ensure_ascii=False, indent=1)
    print("Готово: маршрутов %d, участков дорог %d, названий улиц %d" % (len(routes), len(way_ids), len(names)))
    print("Файл: %s" % OUT)
    print("Теперь перезапустите:  python prep\\run_prep.py  и  python prep\\build_check_map.py")

if __name__ == "__main__":
    main()
