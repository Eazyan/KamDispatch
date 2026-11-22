"""CLI: python3 -m prep.decode --map m.json --solution s.json --out routes.geojson

Превращает ответ HGS в GeoJSON: LineString на визит (kind=service) и
перегонные плечи kind=deadhead, восстановленные как кратчайшие пути по графу.
"""
import argparse
import json

import networkx as nx

from . import formulas


def _hhmm(sec):
    sec = int(round(sec)) % 86400
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}"


def _load(path):
    return json.load(open(path, encoding="utf-8"))


def transit_graph_from_map(pmap):
    """{class: DiGraph} по map-файлу (трафик из условий зоны)."""
    traffic = pmap["meta"]["condition"]["traffic_coef"]
    graphs = {}
    for cname, cinfo in pmap["classes"].items():
        G = nx.DiGraph()
        G.add_nodes_from(n["id"] for n in pmap["graph"]["nodes"])
        gab = cinfo["gabarit_m"]
        for e in pmap["graph"]["edges"]:
            if not formulas.class_fits_width(gab, e["width_m"]):
                continue
            v = cinfo["v_transit_kmh"]
            G.add_edge(e["u"], e["v"], weight=formulas.transit_time_s(
                e["length_m"], v, traffic, e["slope_pct"]), edge=e)
            if not e["oneway"]:
                G.add_edge(e["v"], e["u"], weight=formulas.transit_time_s(
                    e["length_m"], v, traffic, -e["slope_pct"]), edge=e)
        graphs[cname] = G
    return graphs


def _edge_geometries(G, node_path):
    """node path -> объединённая geometry [[lon,lat],...] без дублирования точек.

    Каждая дуга ориентируется по направлению движения (edge.u == a -> вперёд)."""
    coords = []
    for a, b in zip(node_path, node_path[1:]):
        edge = G[a][b]["edge"]
        geo = edge["geometry"] if edge["u"] == a else list(reversed(edge["geometry"]))
        if coords and geo[0] == coords[-1]:
            coords.extend(geo[1:])
        else:
            coords.extend(geo)
    return coords


def decode(pmap, solution):
    """-> (features, per_vehicle_stats)."""
    graphs = transit_graph_from_map(pmap)
    by_i = {n["i"]: n["id"] for n in pmap["nodes"]}
    tasks = {t["id"]: t for t in pmap["tasks"]}
    vehicles = {v["id"]: v for v in pmap["vehicles"]}

    features, stats = [], {}
    for route in solution.get("routes", []):
        veh = vehicles[route["vehicle"]]
        G = graphs[veh["class"]]
        prev_node = by_i[veh["start_i"]]
        svc_km, dh_km = 0.0, 0.0

        def deadhead(dst_graph_node, arrive):
            nonlocal prev_node, dh_km
            if dst_graph_node == prev_node:
                return  # нулевой перегон — не рисуем пустой leg
            try:
                node_path = nx.shortest_path(G, prev_node, dst_graph_node,
                                             weight="weight")
            except nx.NetworkXNoPath:
                node_path = [prev_node, dst_graph_node]  # аварийный прямой отрезок
            coords = _edge_geometries(G, node_path)
            length, duration = 0.0, 0.0
            for a, b in zip(node_path, node_path[1:]):
                length += G[a][b]["edge"]["length_m"]
                duration += G[a][b]["weight"]
            dh_km += length / 1000.0
            features.append({
                "type": "Feature",
                "properties": {
                    "vehicle": veh["id"], "task": None, "street": None,
                    "start_time": _hhmm(max(0.0, arrive - duration)),
                    "end_time": _hhmm(arrive),
                    "kind": "deadhead",
                },
                "geometry": {"type": "LineString", "coordinates": coords},
            })

        for vis in route["visits"]:
            t = tasks[vis["task"]]
            deadhead(by_i[t["start_node"]], vis["arrive"])
            features.append({
                "type": "Feature",
                "properties": {
                    "vehicle": veh["id"], "task": t["id"], "street": t["street"],
                    "start_time": _hhmm(vis["start"]), "end_time": _hhmm(vis["end"]),
                    "kind": "service",
                    "edge_id": t["edge_id"], "pass_index": t["pass_index"],
                    "priority": t["priority"],
                },
                "geometry": {"type": "LineString",
                             "coordinates": t["geometry"]},
            })
            svc_km += t["length_m"] / 1000.0
            prev_node = by_i[t["end_node"]]
        deadhead(by_i[veh["end_i"]], route["return_time"])
        stats[veh["id"]] = {"service_km": round(svc_km, 3),
                            "deadhead_km": round(dh_km, 3),
                            "return_hhmm": _hhmm(route["return_time"])}
    return features, stats


def summarize(pmap, solution):
    print("\n=== objective (raw sums) ===")
    for k, v in solution["objective"].items():
        print(f"  {k:10s} {v:12.1f}")
    print(f"  status={solution['status']}  unassigned={solution.get('unassigned')}")
    features, stats = decode(pmap, solution)
    print("=== per-vehicle km ===")
    for vid, s in stats.items():
        print(f"  {vid:8s} service {s['service_km']:8.2f} km   "
              f"deadhead {s['deadhead_km']:7.2f} km   return {s['return_hhmm']}")
    return features, stats


def main(argv=None):
    ap = argparse.ArgumentParser(prog="prep.decode")
    ap.add_argument("--map", required=True)
    ap.add_argument("--solution", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    pmap, sol = _load(args.map), _load(args.solution)
    features, _ = decode(pmap, sol)
    fc = {"type": "FeatureCollection",
          "crs": {"type": "name",
                  "properties": {"name": pmap["meta"].get("crs",
                                                         "EPSG:4326")}},
          "features": features}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False)
    print(f"wrote {args.out}: {len(features)} features")


if __name__ == "__main__":
    main()
