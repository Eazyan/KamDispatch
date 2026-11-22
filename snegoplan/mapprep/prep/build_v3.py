#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Снегоплан: сборка graph_v3.json из свежего OSM (mapdata/data/osm_petropavlovsk.json).

Офлайн: высоты берутся из out/graph_v2.json + input/elevation_cache.json (source=srtm),
для остальных узлов - IDW по 8 ближайшим известным (source=estimate), кеш в input/elevation_cache_v3.json.

Запуск (из корня mapprep):  python -X utf8 prep/build_v3.py
Выход: out/graph_v3.json, out/zones_v3.json, out/prep_stats_v3.json,
       out/edge_weights.csv, out/mean_weights.json, input/elevation_cache_v3.json
Пункты TASK 9-12 (КСОДД/светофоры/остановки/POI) пропущены: CSV mapdata не готовы.
Детерминирован: сортировки по id, никакого random.
"""
import json, math, os, sys, collections, datetime
import numpy as np
import networkx as nx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_prep import (parse_lanes, parse_width, parse_maxspeed, default_lanes,
                      priority_of, zones_partition, convex_hull,
                      LANE_W, SHOULDER, base_hw)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OSM = os.path.normpath(os.path.join(ROOT, "..", "mapdata", "data", "osm_petropavlovsk.json"))
IN = os.path.join(ROOT, "input")
OUT = os.path.join(ROOT, "out")
N_ZONES = 9

KEEP_HW = {"motorway", "trunk", "primary", "secondary", "tertiary",
           "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
           "unclassified", "residential", "living_street", "service"}
DROP_SERVICE = {"parking_aisle", "driveway"}
ONEWAY_YES = {"yes", "true", "1"}

def hav(lon1, lat1, lon2, lat2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1; dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

def path_len(pts):
    return round(sum(hav(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])), 1)

# ---------------------------------------------------------------- load OSM
def load_osm():
    o = json.load(open(OSM, encoding="utf-8"))
    els = o["elements"]
    node_el = {}          # osm node id -> element (tags, lon/lat)
    ways, rels = [], []
    for e in els:
        t = e["type"]
        if t == "node":
            node_el[e["id"]] = e
        elif t == "way":
            ways.append(e)
        elif t == "relation":
            rels.append(e)
    return node_el, ways, rels

def keep_way(w):
    hw = w.get("tags", {}).get("highway")
    if hw not in KEEP_HW:
        return False
    if hw == "service" and w.get("tags", {}).get("service") in DROP_SERVICE:
        return False
    return True

def way_coords(w, node_el):
    """[(lon,lat), ...] по порядку nodes; geometry из overpass совпадает с nodes."""
    geom = w.get("geometry")
    nodes = w.get("nodes", [])
    if geom and len(geom) == len(nodes):
        return [(g["lon"], g["lat"]) for g in geom]
    pts = []
    for nid in nodes:
        e = node_el.get(nid)
        pts.append((e["lon"], e["lat"]) if e and "lon" in e else None)
    if any(p is None for p in pts):
        return None
    return pts

# ---------------------------------------------------------------- build edges
def build_edges(node_el, ways):
    kept = [w for w in ways if keep_way(w)]
    # узлы-разрезы: общие у >=2 way или конец way
    cnt = collections.Counter()
    endpoints = set()
    for w in kept:
        ns = w.get("nodes", [])
        cnt.update(ns)
        if ns:
            endpoints.add(ns[0]); endpoints.add(ns[-1])
    splitset = {n for n, c in cnt.items() if c >= 2} | endpoints

    edges = []            # dict: u,v,geometry,tags,oneway,osm_way_id
    skipped_geom = 0
    for w in sorted(kept, key=lambda w: w["id"]):
        ns = w.get("nodes", [])
        if len(ns) < 2:
            continue
        coords = way_coords(w, node_el)
        if coords is None:
            skipped_geom += 1
            continue
        tags = w.get("tags", {})
        ow = tags.get("oneway", "").strip().lower()
        oneway = 1 if ow in ONEWAY_YES else (-1 if ow == "-1" else 0)
        # точки разреза
        idxs = [i for i, nid in enumerate(ns) if nid in splitset]
        if idxs[0] != 0:
            idxs.insert(0, 0)
        if idxs[-1] != len(ns) - 1:
            idxs.append(len(ns) - 1)
        for a, b in zip(idxs, idxs[1:]):
            if a == b:
                continue
            u, v = ns[a], ns[b]
            if u == v:
                continue
            geom = coords[a:b + 1]
            edges.append(dict(u=u, v=v, geometry=[[round(x, 7), round(y, 7)] for x, y in geom],
                              tags=tags, oneway=oneway, way_id=w["id"]))
    return edges, skipped_geom, len(kept)

def make_directed(raw_edges):
    """Односторонние - как есть (oneway=-1 разворачиваем), двусторонние - пара с reverse_edge_id."""
    dedges = []
    for e in raw_edges:
        u, v = e["u"], e["v"]
        if e["oneway"] == -1:
            u, v = v, u
        dedges.append(dict(u=u, v=v, geometry=e["geometry"], tags=e["tags"],
                           oneway=e["oneway"] != 0, way_id=e["way_id"]))
        if e["oneway"] == 0:
            dedges.append(dict(u=v, v=u, geometry=list(reversed(e["geometry"])), tags=e["tags"],
                               oneway=False, way_id=e["way_id"]))
    # ключи на (u,v) с множественными рёбрами
    byuv = collections.Counter((e["u"], e["v"]) for e in dedges)
    seen = collections.Counter()
    out = []
    for e in dedges:
        k = seen[(e["u"], e["v"])]
        seen[(e["u"], e["v"])] += 1
        e["key"] = k
        e["id"] = "%d-%d-%d" % (e["u"], e["v"], k)
        out.append(e)
    # reverse_edge_id
    idof = {(e["u"], e["v"], e["key"]): e["id"] for e in out}
    for e in out:
        e["reverse_edge_id"] = idof.get((e["v"], e["u"], e["key"]))
    return out

def largest_scc(dedges):
    D = nx.DiGraph()
    D.add_edges_from((e["u"], e["v"]) for e in dedges)
    comps = list(nx.strongly_connected_components(D))
    comps.sort(key=len, reverse=True)
    keep = comps[0]
    kept = [e for e in dedges if e["u"] in keep and e["v"] in keep]
    dropped_nodes = sum(len(c) for c in comps[1:])
    return kept, len(comps), dropped_nodes

# ---------------------------------------------------------------- attrs
def build_attrs(edges, bus_ways):
    """lanes/width/maxspeed/priority + источники; недостающее у парного обратного ребра."""
    E = {e["id"]: e for e in edges}
    raw = {}
    for e in edges:
        t = e["tags"]
        raw[e["id"]] = dict(
            lanes=parse_lanes(t.get("lanes")),
            width=parse_width(t.get("width")),
            maxspeed=parse_maxspeed(t.get("maxspeed")),
        )
    for e in edges:
        r = raw[e["id"]]
        rev = E.get(e.get("reverse_edge_id"))
        for f in ("lanes", "width", "maxspeed"):
            if r[f] is None and rev is not None and raw[rev["id"]][f] is not None:
                r[f] = raw[rev["id"]][f]
    stats = collections.Counter()
    for e in edges:
        r = raw[e["id"]]
        rev = E.get(e.get("reverse_edge_id"))
        twoway = rev is not None
        hw = base_hw(e["tags"].get("highway", ""))
        lw = LANE_W.get(hw, 3.0)
        if r["lanes"] is not None:
            lanes, lsrc = r["lanes"], "osm"
        else:
            lanes, lsrc = default_lanes(e["tags"].get("highway", ""), twoway), "estimate"
        if r["width"] is not None:
            wm, wsrc = r["width"], "osm"
        else:
            wm, wsrc = lanes * lw + SHOULDER, "estimate"
        e["highway"] = e["tags"].get("highway")
        e["name"] = e["tags"].get("name")
        e["lanes"] = lanes
        e["lanes_source"] = lsrc
        e["width_m"] = round(wm, 2)
        e["width_dir_m"] = round(wm / 2 if twoway else wm, 2)
        e["width_source"] = wsrc
        e["maxspeed"] = r["maxspeed"]
        e["maxspeed_source"] = "osm" if r["maxspeed"] is not None else "estimate"
        e["osm_way_ids"] = [str(e["way_id"])]
        e["bus_route"] = str(e["way_id"]) in bus_ways
        e["priority"] = priority_of(e, e["bus_route"])
        stats["lanes_" + lsrc] += 1
        stats["width_" + wsrc] += 1
        stats["maxspeed_osm" if r["maxspeed"] is not None else "maxspeed_estimate"] += 1
        stats["bus_route"] += e["bus_route"]
        stats["prio_%d" % e["priority"]] += 1
        stats["oneway"] += e["oneway"]
    return stats

# ---------------------------------------------------------------- elevation
def load_known_elev():
    known = {}
    v2p = os.path.join(OUT, "graph_v2.json")
    if os.path.exists(v2p):
        v2 = json.load(open(v2p, encoding="utf-8"))
        for n in v2["nodes"]:
            if n.get("elevation_m") is not None:
                known[(round(n["lon"], 6), round(n["lat"], 6))] = float(n["elevation_m"])
    cp = os.path.join(IN, "elevation_cache.json")
    if os.path.exists(cp):
        c = json.load(open(cp, encoding="utf-8"))
        for k, v in c.items():
            lon, lat = k.split(",")
            known[(round(float(lon), 6), round(float(lat), 6))] = float(v)
    return known

def idw_fill(nodes, known):
    """nodes: list of dict c lon/lat/id. Возвращает {id: (elev, source)}."""
    res = {}
    miss = []
    for n in nodes:
        k = (round(n["lon"], 6), round(n["lat"], 6))
        if k in known:
            res[n["id"]] = (known[k], "srtm")
        else:
            miss.append(n)
    if miss:
        kl = np.array([[k[0], k[1]] for k in known])
        kv = np.array(list(known.values()))
        # метрический масштаб: 1 град широты ~ 111 км
        m = np.array([math.cos(math.radians(53.05)) * 111.32, 110.57])
        for n in miss:
            p = np.array([n["lon"], n["lat"]])
            d = np.sqrt((((kl - p) * m) ** 2).sum(1))
            idx = np.argsort(d, kind="stable")[:8]
            w = 1.0 / np.maximum(d[idx], 1e-6)
            res[n["id"]] = (float((kv[idx] * w).sum() / w.sum()), "estimate")
    return res

# ---------------------------------------------------------------- main
def main():
    node_el, ways, rels = load_osm()
    bus_ways = set()
    for r in rels:
        if r.get("tags", {}).get("route") in ("bus", "trolleybus"):
            for m in r.get("members", []):
                if m.get("type") == "way":
                    bus_ways.add(str(m.get("ref")))
    print("OSM: ways %d, relations %d, автобусных way %d" % (len(ways), len(rels), len(bus_ways)))

    raw_edges, skipped_geom, n_kept_ways = build_edges(node_el, ways)
    print("way в графе: %d, сырых направленных сегментов: %d (geometry пропущено: %d)"
          % (n_kept_ways, len(raw_edges), skipped_geom))
    dedges = make_directed(raw_edges)
    print("направленных рёбер до SCC: %d" % len(dedges))
    dedges, n_comps, dropped_nodes = largest_scc(dedges)
    print("после largest SCC: рёбер %d, компонент было %d, узлов выкинуто %d"
          % (len(dedges), n_comps, dropped_nodes))

    # --- финальные узлы
    nids = sorted({e["u"] for e in dedges} | {e["v"] for e in dedges})
    coord_of = {}
    for e in raw_edges:
        pass
    # координаты из геометрий рёбер (первый/последний пункт)
    for e in dedges:
        coord_of.setdefault(e["u"], tuple(e["geometry"][0]))
        coord_of.setdefault(e["v"], tuple(e["geometry"][-1]))
    # точные координаты узла из OSM node element, если есть
    nodes = []
    for nid in nids:
        e = node_el.get(nid)
        if e and "lon" in e:
            lon, lat = e["lon"], e["lat"]
        else:
            lon, lat = coord_of[nid]
        nodes.append(dict(id=nid, lon=lon, lat=lat))

    stats = build_attrs(dedges, bus_ways)

    # --- kind узлов
    used = collections.Counter()
    ends = set()
    for w in ways:
        if not keep_way(w):
            continue
        ns = w.get("nodes", [])
        cnt_u = used.update(ns)
    for w in ways:
        if not keep_way(w):
            continue
        ns = w.get("nodes", [])
        if ns:
            ends.add(ns[0]); ends.add(ns[-1])
    shared = {n for n, c in used.items() if c >= 2}
    node_tags = {nid: node_el.get(nid, {}).get("tags", {}) for nid in nids}
    # светофоры OSM, не на узле графа: в 25 м от junction
    sig_nodes = {nid for nid in nids if node_tags.get(nid, {}).get("highway") == "traffic_signals"}
    extra_sig = set()
    for eid, e in node_el.items():
        if e.get("tags", {}).get("highway") == "traffic_signals" and eid not in nids:
            best, bd = None, 25.0
            for n in nodes:
                if n["id"] not in shared:
                    continue
                d = hav(e["lon"], e["lat"], n["lon"], n["lat"])
                if d < bd:
                    best, bd = n["id"], d
            if best is not None:
                extra_sig.add(best)
    for n in nodes:
        nid, tg = n["id"], node_tags.get(n["id"], {})
        if nid in sig_nodes or nid in extra_sig:
            n["kind"] = "traffic_signals"
            n["traffic_signals_source"] = "osm"
        elif tg.get("highway") == "crossing" or "crossing" in tg:
            n["kind"] = "crossing"
        elif nid in shared:
            n["kind"] = "junction"
        else:
            n["kind"] = "end"
    stats["nodes_traffic_signals"] = sum(1 for n in nodes if n["kind"] == "traffic_signals")
    stats["nodes_crossing"] = sum(1 for n in nodes if n["kind"] == "crossing")

    # --- высоты
    known = load_known_elev()
    print("известных высот (v2 + кэш): %d" % len(known))
    elev = idw_fill(nodes, known)
    cache_v3 = {}
    for n in nodes:
        ev, src = elev[n["id"]]
        n["elevation_m"] = round(ev, 1)
        n["elevation_source"] = src
        cache_v3["%.6f,%.6f" % (n["lon"], n["lat"])] = round(ev, 1)
    json.dump(cache_v3, open(os.path.join(IN, "elevation_cache_v3.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    n_srtm = sum(1 for n in nodes if n["elevation_source"] == "srtm")
    stats["elev_srtm"] = n_srtm
    stats["elev_estimate"] = len(nodes) - n_srtm

    # --- рёбра: схема v2
    edges_out = []
    total_km = 0.0
    for e in sorted(dedges, key=lambda e: e["id"]):
        length = path_len(e["geometry"])
        total_km += length / 1000.0
        eu, ev = elev[e["u"]][0], elev[e["v"]][0]
        slope = round((ev - eu) / length * 100, 2) if length > 0 else 0.0
        edges_out.append(dict(
            id=e["id"], u=e["u"], v=e["v"], key=e["key"], length_m=length, slope_pct=slope,
            geometry=e["geometry"], name=e["name"], highway=e["highway"], oneway=e["oneway"],
            required=True, street_id="S-%d-%d-%d" % (min(e["u"], e["v"]), max(e["u"], e["v"]), e["key"]),
            reverse_edge_id=e["reverse_edge_id"], task_id=None,
            lanes=e["lanes"], lanes_source=e["lanes_source"],
            width_m=e["width_m"], width_dir_m=e["width_dir_m"], width_source=e["width_source"],
            maxspeed=e["maxspeed"], maxspeed_source=e["maxspeed_source"],
            osm_way_ids=e["osm_way_ids"], bus_route=e["bus_route"], priority=e["priority"], zone=None))
    for n in nodes:
        n.pop("tags", None)

    g = dict(
        meta=dict(
            created=datetime.date.today().isoformat(),
            source="mapdata/data/osm_petropavlovsk.json (Overpass, out geom)",
            bbox=[min(n["lon"] for n in nodes), min(n["lat"] for n in nodes),
                  max(n["lon"] for n in nodes), max(n["lat"] for n in nodes)],
            crs="EPSG:4326", depot_node=None, dump_node=None,
            prep=dict(zones=N_ZONES, lane_width_m=LANE_W, default_lane_w=3.0, shoulder_m=SHOULDER,
                      width_m="полная ширина проезжей части (оба направления); width_dir_m - на одно направление",
                      elevation="srtm: совпадение с v2/кэшем по координатам (6 знаков); estimate: IDW по 8 ближайшим",
                      elevation_sources=["srtm", "estimate"],
                      value_sources="osm | ksodd | estimate (ksodd - место зарезервировано, CSV пока нет)")),
        nodes=nodes, edges=edges_out)

    zone_of_node, owner = zones_partition(g)
    for n in g["nodes"]:
        n["zone"] = int(zone_of_node[n["id"]])
    for e in g["edges"]:
        e["zone"] = int(owner[e["id"]])
    stats["nodes"] = len(nodes)
    stats["edges"] = len(edges_out)
    stats["total_km"] = round(total_km, 3)
    stats["bus_relations"] = sum(1 for r in rels if r.get("tags", {}).get("route") in ("bus", "trolleybus"))

    json.dump(g, open(os.path.join(OUT, "graph_v3.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))

    # --- zones_v3.json (как v2)
    zones = []
    for k in range(1, N_ZONES + 1):
        nids_k = sorted(n["id"] for n in g["nodes"] if n["zone"] == k)
        es = sorted((e for e in g["edges"] if e["zone"] == k), key=lambda e: e["id"])
        km = {p: round(sum(e["length_m"] for e in es if e["priority"] == p) / 1000, 3) for p in (1, 2, 3)}
        km["total"] = round(sum(km[p] for p in (1, 2, 3)), 3)
        pts = [(x, y) for e in es for x, y in e["geometry"]] + [(n["lon"], n["lat"]) for n in g["nodes"] if n["zone"] == k]
        lons = [p[0] for p in pts]; lats = [p[1] for p in pts]
        hull = convex_hull(pts)
        hull.append(hull[0])
        zones.append(dict(id=k, name="Зона %d" % k, node_ids=nids_k, edge_ids=[e["id"] for e in es],
                          stats=dict(nodes=len(nids_k), edges=len(es), required_km=km,
                                     bbox=[min(lons), min(lats), max(lons), max(lats)]),
                          hull_geojson=dict(type="Feature", properties=dict(zone=k, name="Зона %d" % k, kind="convex_hull"),
                                            geometry=dict(type="Polygon", coordinates=[[list(p) for p in hull]]))))
    json.dump(dict(zones=zones), open(os.path.join(OUT, "zones_v3.json"), "w", encoding="utf-8"),
              ensure_ascii=False)

    # --- edge_weights.csv + mean_weights.json (пункт 13; tl/bus_stop/poi - без CSV mapdata)
    VW_BASE = {"trunk": 20, "primary": 20, "secondary": 20, "tertiary": 15}
    VD_BASE = {"trunk": 40, "primary": 40, "secondary": 40, "tertiary": 30}
    def v_work(hw):
        return min(20, VW_BASE.get(base_hw(hw), 10))
    def v_dead(hw, ms):
        b = base_hw(hw)
        if b == "service":
            return 10
        v = VD_BASE.get(b, 25)
        if ms:
            v = min(v, ms)
        return v
    import csv
    agg = collections.defaultdict(lambda: [0.0, 0.0, 0, 0, 0])
    with open(os.path.join(OUT, "edge_weights.csv"), "w", encoding="utf-8", newline="") as f:
        wcsv = csv.writer(f)
        wcsv.writerow(["edge_id", "zone", "highway", "lanes", "width_dir_m", "maxspeed",
                       "tl_delay_s", "bus_stop", "near_poi", "v_work_kmh", "v_dead_kmh"])
        for e in g["edges"]:
            vw, vd = v_work(e["highway"]), v_dead(e["highway"], e["maxspeed"])
            wcsv.writerow([e["id"], e["zone"], e["highway"], e["lanes"], e["width_dir_m"],
                           e["maxspeed"] or "", "", int(e["bus_route"]), "", vw, vd])
            a = agg[(e["zone"], base_hw(e["highway"]))]
            a[0] += vw; a[1] += vd; a[2] += 1; a[3] += e["bus_route"]; a[4] += 1
    mean = {}
    for (z, hw), (s0, s1, n, nb, _) in sorted(agg.items()):
        mean["%d|%s" % (z, hw)] = dict(zone=z, highway=hw, n=n,
                                       v_work_kmh=round(s0 / n, 2), v_dead_kmh=round(s1 / n, 2),
                                       share_tl_delay=0.0, share_bus_stop=round(nb / n, 4))
    json.dump(dict(formulas=dict(
                    v_work="min(20, базовая): trunk/primary/secondary 20, tertiary 15, прочее 10; service=10",
                    v_dead="trunk/primary/secondary 40, tertiary 30, прочее 25, min(maxspeed); service=10",
                    note="tl_delay_s/bus_stop/near_poi пустые: traffic_lights_match.csv, bus_stops.csv, poi.csv отсутствуют"),
                   groups=mean),
              open(os.path.join(OUT, "mean_weights.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    stats["zones"] = [dict(id=z["id"], **z["stats"]) for z in zones]
    json.dump(stats, open(os.path.join(OUT, "prep_stats_v3.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # --- проверки
    Gu = nx.Graph()
    for e in g["edges"]:
        Gu.add_edge(e["u"], e["v"])
    for k in range(1, N_ZONES + 1):
        ids = [n["id"] for n in g["nodes"] if n["zone"] == k]
        assert nx.is_connected(Gu.subgraph(ids)), "зона %d несвязна" % k
    for e in g["edges"]:
        if e.get("reverse_edge_id"):
            r = next(x for x in g["edges"] if x["id"] == e["reverse_edge_id"])
            assert r["zone"] == e["zone"]
    # сильная связность
    Dg = nx.DiGraph()
    Dg.add_edges_from((e["u"], e["v"]) for e in g["edges"])
    assert nx.is_strongly_connected(Dg)
    # полнота полей
    req = ["id", "u", "v", "key", "length_m", "slope_pct", "geometry", "name", "highway", "oneway",
           "required", "street_id", "reverse_edge_id", "task_id", "lanes", "lanes_source",
           "width_m", "width_dir_m", "width_source", "maxspeed", "maxspeed_source",
           "osm_way_ids", "bus_route", "priority", "zone"]
    for e in g["edges"]:
        assert all(k in e for k in req), e["id"]
    print(json.dumps({k: v for k, v in stats.items() if k != "zones"}, ensure_ascii=False, indent=1))
    print("зона | узлов | рёбер | П1 км | П2 км | П3 км | всего км")
    for z in zones:
        s = z["stats"]; r = s["required_km"]
        print("%4d | %5d | %5d | %5.2f | %5.2f | %5.2f | %6.2f" % (z["id"], s["nodes"], s["edges"], r[1], r[2], r[3], r["total"]))
    print("OK: 9 зон связны, граф сильно связный, обратные рёбра в той же зоне, все поля на месте")

if __name__ == "__main__":
    main()
