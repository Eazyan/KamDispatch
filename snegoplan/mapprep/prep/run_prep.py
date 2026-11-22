#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Снегоплан: подготовка карты (полосы/ширина, приоритеты, зоны).

Запуск:  python3 prep/run_prep.py            (из /home/claude/mapprep)
Вход:    input/graph.json, input/osm_drive.graphml, [out/bus_routes.json]
Выход:   out/graph_v2.json, out/zones.json, out/prep_stats.json
Детерминирован: никакого random, везде сортировка по id.
"""
import ast, json, math, os, re, sys, collections
import numpy as np
import networkx as nx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN = os.path.join(ROOT, "input")
OUT = os.path.join(ROOT, "out")
N_ZONES = 9

# ---------------------------------------------------------------- parsing
def listify(v):
    """graphml хранит списки как строку "['a', 'b']" -> python list."""
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            try:
                return [str(x) for x in ast.literal_eval(s)]
            except Exception:
                pass
        return [s]
    return [str(v)]

def pick(nums):
    """самое частое значение; при равенстве - максимальное."""
    if not nums:
        return None
    c = collections.Counter(nums)
    top = max(c.values())
    return max(k for k, n in c.items() if n == top)

def parse_lanes(raw):
    vals = []
    for item in listify(raw):
        parts = [p for p in re.split(r"[;,|]", item) if p.strip()]
        nums = []
        for p in parts:
            m = re.match(r"^\s*(\d+)", p)
            if m:
                nums.append(int(m.group(1)))
        if nums:
            vals.append(max(nums))          # "2;3" -> 3
    return pick(vals)                        # список путей -> самое частое/макс

def parse_width(raw):
    vals = []
    for item in listify(raw):
        for p in re.split(r";", item):
            m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*(m|м|ft|')?\s*$", p.strip(), re.I)
            if m:
                x = float(m.group(1).replace(",", "."))
                if m.group(2) and m.group(2).lower() in ("ft", "'"):
                    x *= 0.3048
                vals.append(round(x, 2))
    return pick(vals)

DEFAULT_SPEED = {"RU:urban": 60, "RU:rural": 90, "RU:motorway": 110}
def parse_maxspeed(raw):
    vals = []
    for item in listify(raw):
        for p in item.split(";"):
            p = p.strip()
            m = re.match(r"^(\d+)(\s*km/?h)?$", p)
            if m:
                vals.append(int(m.group(1)))
            elif p in DEFAULT_SPEED:
                vals.append(DEFAULT_SPEED[p])
    return pick(vals)

def parse_wkt(w):
    m = re.match(r"LINESTRING\s*\((.*)\)", w or "")
    if not m:
        return []
    return [tuple(float(t) for t in p.split()) for p in m.group(1).split(",")]

# ---------------------------------------------------------------- matching
def match_edges(g, G):
    """graph.json-узлы не хранят osmid -> сопоставляем по координатам (1:1, точное совпадение
    до 6 знаков), далее ребро (u,v) -> кандидаты (a,b,k) и (b,a,k) в graphml (для параллельных
    рёбер и разворота выбираем по средней дистанции вершин геометрии)."""
    coord = {}
    for n, d in G.nodes(data=True):
        coord[(round(float(d["x"]), 6), round(float(d["y"]), 6))] = n
    nmap = {}
    for n in g["nodes"]:
        nmap[n["id"]] = coord[(round(n["lon"], 6), round(n["lat"], 6))]
    res = {}
    for e in g["edges"]:
        a, b = nmap[e["u"]], nmap[e["v"]]
        cands = []
        for (s, t, rev) in ((a, b, 0), (b, a, 1)):
            if G.has_edge(s, t):
                for k, d in G[s][t].items():
                    cands.append((s, t, k, d, rev))
        if not cands:
            res[e["id"]] = None
            continue
        pts = np.array(e["geometry"])
        best = None
        for (s, t, k, d, rev) in cands:
            gl = parse_wkt(d.get("geometry"))
            if not gl:
                gl = [(float(G.nodes[s]["x"]), float(G.nodes[s]["y"])), (float(G.nodes[t]["x"]), float(G.nodes[t]["y"]))]
            gl = np.array(gl)
            dist = np.sqrt(((pts[:, None, :] - gl[None, :, :]) ** 2).sum(-1)).min(1).mean() * 1e5
            # 1e-5 град ~ 1 м; штраф за обратное направление, чтобы при ничьей брать прямое
            score = dist + (0.001 if rev else 0)
            if best is None or score < best[0]:
                best = (score, d, rev)
        res[e["id"]] = (best[1], best[0])
    return res

# ---------------------------------------------------------------- attributes
LANE_W = {"trunk": 3.5, "primary": 3.5, "secondary": 3.5, "tertiary": 3.25}
SHOULDER = 0.5
def base_hw(h):
    return h.replace("_link", "")

def default_lanes(h, twoway):
    b = base_hw(h)
    if b in ("trunk", "primary", "secondary"):
        return 4 if twoway else 2
    if b in ("living_street", "service"):
        return 1
    return 2    # tertiary, residential, unclassified, прочее

def priority_of(e, bus_hit):
    b = base_hw(e["highway"])
    if bus_hit:
        return 1
    if b in ("trunk", "primary", "secondary"):
        return 1
    if b in ("living_street", "service"):
        return 3
    if not e.get("name"):
        return 3
    return 2    # tertiary/residential/unclassified с именем

def load_bus(path):
    if not os.path.exists(path):
        return set(), set()
    d = json.load(open(path, encoding="utf-8"))
    ways = {str(x) for x in d.get("way_ids", [])}
    names = {s.strip().lower() for s in d.get("street_names", [])}   # вручную заполняемый список
    return ways, names

def build_attrs(g, G, match):
    bus_ways, bus_names = load_bus(os.path.join(OUT, "bus_routes.json"))
    E = {e["id"]: e for e in g["edges"]}
    raw = {}
    for e in g["edges"]:
        m = match[e["id"]]
        d = m[0] if m else {}
        raw[e["id"]] = dict(lanes=parse_lanes(d.get("lanes")), width=parse_width(d.get("width")),
                            maxspeed=parse_maxspeed(d.get("maxspeed")), maxspeed_raw=d.get("maxspeed"),
                            ways=[w for w in listify(d.get("osmid")) if w],
                            matched=bool(m), dist=(m[1] if m else None))
    stats = collections.Counter()
    for e in g["edges"]:
        r = raw[e["id"]]
        rev = E.get(e.get("reverse_edge_id"))
        twoway = rev is not None
        # тот же участок улицы: если у этого направления тега нет, берём от парного
        for f in ("lanes", "width", "maxspeed"):
            if r[f] is None and rev is not None and raw[rev["id"]][f] is not None:
                r[f] = raw[rev["id"]][f]
                r[f + "_from_pair"] = True
        hw = e["highway"]
        b = base_hw(hw)
        lw = LANE_W.get(b, 3.0)
        if r["lanes"] is not None:
            lanes, lsrc = r["lanes"], "osm"
        else:
            lanes, lsrc = default_lanes(hw, twoway), "default"
        if r["width"] is not None:
            wm, wsrc = r["width"], "osm_width"
        elif lsrc == "osm":
            wm, wsrc = lanes * lw + SHOULDER, "lanes"
        else:
            wm, wsrc = lanes * lw + SHOULDER, "default"
        e["lanes"] = lanes
        e["lanes_source"] = lsrc
        e["width_m"] = round(wm, 2)
        e["width_dir_m"] = round(wm / 2 if twoway else wm, 2)
        e["width_source"] = wsrc
        e["maxspeed"] = r["maxspeed"]
        e["osm_way_ids"] = r["ways"]
        bus_hit = bool(set(r["ways"]) & bus_ways) or (e.get("name") or "").strip().lower() in bus_names
        e["bus_route"] = bus_hit
        e["priority"] = priority_of(e, bus_hit)
        stats["edges"] += 1
        stats["matched"] += r["matched"]
        stats["lanes_" + lsrc] += 1
        stats["lanes_osm_from_pair"] += bool(r.get("lanes_from_pair"))
        stats["width_" + wsrc] += 1
        stats["maxspeed_osm"] += r["maxspeed"] is not None
        stats["bus_route"] += bus_hit
        stats["prio_%d" % e["priority"]] += 1
    return stats

# ---------------------------------------------------------------- zones
def zones_partition(g):
    nodes = sorted(n["id"] for n in g["nodes"])
    NI = {n: i for i, n in enumerate(nodes)}
    nd = {n["id"]: n for n in g["nodes"]}
    lat0 = np.mean([n["lat"] for n in g["nodes"]])
    kx = math.cos(math.radians(lat0)) * 111.32
    xy = np.array([[nd[n]["lon"] * kx, nd[n]["lat"] * 110.57] for n in nodes])   # км
    N = len(nodes)
    Gu = nx.Graph()
    Gu.add_nodes_from(range(N))
    pairs = {}                         # (min,max) -> list of edges (одна улица, оба направления)
    for e in sorted(g["edges"], key=lambda e: e["id"]):
        a, b = NI[e["u"]], NI[e["v"]]
        Gu.add_edge(a, b)
        rid = e.get("reverse_edge_id")
        key = tuple(sorted([e["id"], rid])) if rid else (e["id"],)
        pairs.setdefault(key, []).append(e)
    comps = list(nx.connected_components(Gu))
    if len(comps) > 1:
        print("ВНИМАНИЕ: граф несвязный, компонент:", len(comps), sorted(len(c) for c in comps)[:5])
    # единицы работы: улица (пара направлений) = (a,b, length)
    units = []
    for key, es in sorted(pairs.items()):
        e0 = es[0]
        units.append((NI[e0["u"]], NI[e0["v"]], sum(e["length_m"] for e in es), key))
    ua = np.array([u[0] for u in units]); ub = np.array([u[1] for u in units])
    ul = np.array([u[2] for u in units]) / 1000.0
    # вес узла - половина длины инцидентных единиц (для начального разбиения)
    nw = np.zeros(N)
    np.add.at(nw, ua, ul / 2); np.add.at(nw, ub, ul / 2)

    def loads_of(z):
        L = np.zeros(N_ZONES)
        same = z[ua] == z[ub]
        np.add.at(L, z[ua][same], ul[same])
        # межзонные: по убыванию длины, в зону с меньшей текущей нагрузкой (ничья -> меньший id)
        for i in sorted(np.where(~same)[0], key=lambda i: (-ul[i], i)):
            za, zb = z[ua[i]], z[ub[i]]
            t = za if (L[za], za) <= (L[zb], zb) else zb
            L[t] += ul[i]
        return L

    # --- 1. начальное разбиение: взвешенный k-means, старт по квантилям вдоль главной оси
    c = xy - np.average(xy, axis=0, weights=nw)
    ev, evec = np.linalg.eigh(np.cov(c.T, aweights=nw))
    axis = evec[:, -1]
    t = c @ axis
    order = np.argsort(t, kind="stable")
    cum = np.cumsum(nw[order]) / nw.sum()
    cent = np.array([xy[order[np.searchsorted(cum, (k + 0.5) / N_ZONES)]] for k in range(N_ZONES)])
    for _ in range(100):
        d = ((xy[:, None, :] - cent[None, :, :]) ** 2).sum(-1)
        lab = d.argmin(1)
        new = np.array([np.average(xy[lab == k], axis=0, weights=nw[lab == k] + 1e-9) if (lab == k).any() else cent[k] for k in range(N_ZONES)])
        if np.allclose(new, cent):
            break
        cent = new
    z = lab.copy()

    # --- 2. ремонт связности (по неориентированному графу)
    def repair(z):
        for _ in range(50):
            changed = False
            for k in range(N_ZONES):
                idx = [i for i in range(N) if z[i] == k]
                if not idx:
                    continue
                cc = sorted(nx.connected_components(Gu.subgraph(idx)), key=lambda c: (-sum(nw[list(c)]), min(c)))
                for comp in cc[1:]:
                    cnt = collections.Counter(z[nb] for n in comp for nb in Gu[n] if z[nb] != k)
                    if cnt:
                        tgt = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                        for n in comp:
                            z[n] = tgt
                        changed = True
            if not changed:
                return
    repair(z)
    # пустые зоны (маловероятно): забираем узел у самой нагруженной
    # --- 3. локальный поиск: баланс + компактность
    area = np.ptp(xy[:, 0]) * np.ptp(xy[:, 1])
    ref = area / N_ZONES
    W_BAL, W_COMP = 2000.0, 1.0

    def cost(z):
        L = loads_of(z)
        m = L.mean()
        bal = (((L - m) / m) ** 2).sum()
        comp = 0.0
        for k in range(N_ZONES):
            p = xy[z == k]
            if len(p):
                comp += ((p - p.mean(0)) ** 2).sum()
        comp = comp / N / ref
        return W_BAL * bal + W_COMP * comp * 20

    def connected_without(z, n, k):
        idx = [i for i in range(N) if z[i] == k and i != n]
        return len(idx) > 0 and nx.is_connected(Gu.subgraph(idx))

    cur = cost(z)
    for it in range(60):
        moved = 0
        for n in range(N):
            nb_z = sorted({z[nb] for nb in Gu[n]} - {z[n]})
            if not nb_z:
                continue
            k = z[n]
            best = (cur, None)
            for k2 in nb_z:
                z[n] = k2
                c2 = cost(z)
                z[n] = k
                if c2 < best[0] - 1e-9:
                    best = (c2, k2)
            if best[1] is not None and connected_without(z, n, k):
                z[n] = best[1]; cur = best[0]; moved += 1
        if moved == 0:
            break
    # --- 4. нумерация с севера на юг по взвешенной широте центроида
    lat_c = {k: np.average(xy[z == k][:, 1], weights=nw[z == k] + 1e-9) for k in range(N_ZONES)}
    rank = {k: r + 1 for r, k in enumerate(sorted(lat_c, key=lambda k: -lat_c[k]))}
    zone_of_node = {nodes[i]: rank[z[i]] for i in range(N)}
    # --- 5. владение рёбрами
    zz = np.array([rank[z[i]] - 1 for i in range(N)])
    L = np.zeros(N_ZONES)
    owner = {}
    for i in range(len(units)):
        if zz[ua[i]] == zz[ub[i]]:
            L[zz[ua[i]]] += ul[i]
            for e in pairs[units[i][3]]:
                owner[e["id"]] = zz[ua[i]] + 1
    for i in sorted([i for i in range(len(units)) if zz[ua[i]] != zz[ub[i]]], key=lambda i: (-ul[i], i)):
        za, zb = zz[ua[i]], zz[ub[i]]
        tz = za if (L[za], za) <= (L[zb], zb) else zb
        L[tz] += ul[i]
        for e in pairs[units[i][3]]:
            owner[e["id"]] = tz + 1
    return zone_of_node, owner

# ---------------------------------------------------------------- hull
def convex_hull(pts):
    pts = sorted(set(map(tuple, pts)))
    def cr(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    lo = []
    for p in pts:
        while len(lo) >= 2 and cr(lo[-2], lo[-1], p) <= 0: lo.pop()
        lo.append(p)
    up = []
    for p in reversed(pts):
        while len(up) >= 2 and cr(up[-2], up[-1], p) <= 0: up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]

# ---------------------------------------------------------------- main
def main():
    g = json.load(open(os.path.join(IN, "graph.json"), encoding="utf-8"))
    G = nx.read_graphml(os.path.join(IN, "osm_drive.graphml"))
    match = match_edges(g, G)
    stats = build_attrs(g, G, match)
    zone_of_node, owner = zones_partition(g)
    for n in g["nodes"]:
        n["zone"] = int(zone_of_node[n["id"]])
    for e in g["edges"]:
        e["zone"] = int(owner[e["id"]])
    g["meta"]["prep"] = dict(zones=N_ZONES, lane_width_m=LANE_W, default_lane_w=3.0, shoulder_m=SHOULDER,
                             width_m="полная ширина проезжей части (оба направления); width_dir_m - на одно направление")
    json.dump(g, open(os.path.join(OUT, "graph_v2.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    # zones.json
    zones = []
    zsum = []
    for k in range(1, N_ZONES + 1):
        nids = sorted(n["id"] for n in g["nodes"] if n["zone"] == k)
        es = sorted((e for e in g["edges"] if e["zone"] == k), key=lambda e: e["id"])
        km = {p: round(sum(e["length_m"] for e in es if e["priority"] == p) / 1000, 3) for p in (1, 2, 3)}
        km["total"] = round(sum(km[p] for p in (1, 2, 3)), 3)
        pts = [(x, y) for e in es for x, y in e["geometry"]] + [(n["lon"], n["lat"]) for n in g["nodes"] if n["zone"] == k]
        lons = [p[0] for p in pts]; lats = [p[1] for p in pts]
        hull = convex_hull(pts)
        hull.append(hull[0])
        z = dict(id=k, name="Зона %d" % k, node_ids=nids, edge_ids=[e["id"] for e in es],
                 stats=dict(nodes=len(nids), edges=len(es), required_km=km,
                            bbox=[min(lons), min(lats), max(lons), max(lats)]),
                 hull_geojson=dict(type="Feature", properties=dict(zone=k, name="Зона %d" % k, kind="convex_hull"),
                                   geometry=dict(type="Polygon", coordinates=[[list(p) for p in hull]])))
        zones.append(z)
    json.dump(dict(zones=zones), open(os.path.join(OUT, "zones.json"), "w", encoding="utf-8"), ensure_ascii=False)
    stats = dict(stats)
    stats["zones"] = [dict(id=z["id"], **z["stats"]) for z in zones]
    json.dump(stats, open(os.path.join(OUT, "prep_stats.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in stats.items() if k != "zones"}, ensure_ascii=False))
    print("зона | узлов | рёбер | П1 км | П2 км | П3 км | всего км")
    for z in zones:
        s = z["stats"]; r = s["required_km"]
        print("%4d | %5d | %5d | %5.2f | %5.2f | %5.2f | %6.2f" % (z["id"], s["nodes"], s["edges"], r[1], r[2], r[3], r["total"]))
    # проверка связности зон
    Gu = nx.Graph()
    for e in g["edges"]: Gu.add_edge(e["u"], e["v"])
    for k in range(1, N_ZONES + 1):
        ids = [n["id"] for n in g["nodes"] if n["zone"] == k]
        assert nx.is_connected(Gu.subgraph(ids)), "зона %d несвязна" % k
    for e in g["edges"]:
        if e.get("reverse_edge_id"):
            r = next(x for x in g["edges"] if x["id"] == e["reverse_edge_id"])
            assert r["zone"] == e["zone"]
    print("OK: 9 зон связны, обратные рёбра в той же зоне")

if __name__ == "__main__":
    main()
