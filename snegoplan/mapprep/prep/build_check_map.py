#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Собирает out/check_map.html (один файл, без сети) из out/graph_v2.json и out/zones.json.

Необязательные аргументы (не ломают старый вызов):
  python prep/build_check_map.py [graph.json [zones.json [out.html]]]
Названия источников ширины/полос передаются в данные (ws/lsn); светофоры (kind=traffic_signals)
- отдельным слоем, если есть в графе."""
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
a = sys.argv[1:]
GP = a[0] if len(a) > 0 else os.path.join(ROOT, "out", "graph_v2.json")
ZP = a[1] if len(a) > 1 else os.path.join(ROOT, "out", "zones.json")
OP = a[2] if len(a) > 2 else os.path.join(ROOT, "out", "check_map.html")
g = json.load(open(GP, encoding="utf-8"))
zj = json.load(open(ZP, encoding="utf-8"))

WS_NAMES = {"osm_width": "ширина из OSM (width)", "lanes": "из числа полос (lanes)",
            "default": "по умолчанию", "osm": "OSM", "estimate": "оценка"}
LS_NAMES = {"osm": "OSM", "default": "по умолчанию", "estimate": "оценка"}

ws_names, ls_names = [], []
edges = []
for e in g["edges"]:
    ws, ls = e["width_source"], e.get("lanes_source", "osm" if e["width_source"] == "osm_width" else "default")
    if ws not in ws_names:
        ws_names.append(ws)
    if ls not in ls_names:
        ls_names.append(ls)
    edges.append([e["name"] or "", e["highway"], e["lanes"], e["width_m"], ws_names.index(ws),
                  ls_names.index(ls), e["zone"], e["priority"], 1 if e.get("reverse_edge_id") else 0,
                  e["length_m"], [round(c, 5) for p in e["geometry"] for c in p]])

signals = [[round(n["lon"], 6), round(n["lat"], 6)]
           for n in g["nodes"] if n.get("kind") == "traffic_signals"]
data = dict(edges=edges, ws=ws_names, wsn=[WS_NAMES.get(w, w) for w in ws_names],
            lsn=[LS_NAMES.get(l, l) for l in ls_names],
            hulls=[z["hull_geojson"]["geometry"]["coordinates"][0] for z in zj["zones"]],
            nodes=[[round(n["lon"], 5), round(n["lat"], 5), n["zone"]] for n in g["nodes"]],
            signals=signals,
            depot=g["meta"].get("depot_node"), dump=g["meta"].get("dump_node"))
html = open(os.path.join(ROOT, "prep", "check_map.template.html"), encoding="utf-8").read()
html = html.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
open(OP, "w", encoding="utf-8").write(html)
print("check_map: %s, %.2f МБ, светофоров: %d" % (os.path.basename(OP), len(html.encode()) / 1e6, len(signals)))
