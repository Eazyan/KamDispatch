"""Загрузка graph_v2.json, zones.json; граф перегона по классам; снап координат."""
import json
import math

import networkx as nx

from . import formulas


def load_graph(path):
    g = json.load(open(path, encoding="utf-8"))
    nodes = {n["id"]: n for n in g["nodes"]}
    edges = g["edges"]
    return {"nodes": nodes, "edges": edges, "meta": g.get("meta", {})}


def load_zones(path):
    return {z["id"]: z for z in json.load(open(path, encoding="utf-8"))["zones"]}


def zone_edges(graph, zone_id):
    """Все рёбра графа с edge.zone == zone_id."""
    return [e for e in graph["edges"] if e["zone"] == zone_id]


def snap_node(graph, lat, lon):
    """Ближайший узел графа (эквидистантная метрика вокруг lat)."""
    cos_lat = math.cos(math.radians(lat))
    best, best_d = None, math.inf
    for n in graph["nodes"].values():
        d = math.hypot((n["lat"] - lat) * 111320.0,
                       (n["lon"] - lon) * 111320.0 * cos_lat)
        if d < best_d:
            best, best_d = n, d
    return best["id"], best_d


def transit_graph(graph, type_info, traffic_coef):
    """Орграф перегона для класса: вес = transit_time_s по направлению.

    Уважает oneway; рёбра, слишком узкие для класса, запрещены.
    """
    gab = type_info["gabarit_m"]
    G = nx.DiGraph()
    G.add_nodes_from(graph["nodes"].keys())
    for e in graph["edges"]:
        if not formulas.class_fits_width(gab, e["width_m"]):
            continue
        v_tr = type_info["v_transit_kmh"]
        G.add_edge(e["u"], e["v"],
                   weight=formulas.transit_time_s(
                       e["length_m"], v_tr, traffic_coef, e["slope_pct"]),
                   edge_id=e["id"])
        if not e["oneway"]:
            G.add_edge(e["v"], e["u"],
                       weight=formulas.transit_time_s(
                           e["length_m"], v_tr, traffic_coef, -e["slope_pct"]),
                       edge_id=e["id"])
    return G


def class_shortest_paths(G, sources):
    """Dijkstra от каждого source по всему графу -> {src: {dst: time_s}}."""
    out = {}
    for s in sources:
        out[s] = nx.single_source_dijkstra_path_length(G, s, weight="weight")
    return out


def reconstruct_path(G, src, dst):
    """Кратчайший путь src->dst как список (edge_id, u, v); None если нет."""
    if src == dst:
        return []
    try:
        return nx.shortest_path(G, src, dst, weight="weight")
    except nx.NetworkXNoPath:
        return None


def path_edges(G, node_path):
    """node path -> список edge_id (по атрибуту edge_id дуг)."""
    ids = []
    for a, b in zip(node_path, node_path[1:]):
        ids.append(G[a][b]["edge_id"])
    return ids
