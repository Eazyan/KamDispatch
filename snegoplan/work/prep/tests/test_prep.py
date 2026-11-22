"""Тесты Снегоплана (plain asserts, запуск: python3 tests/test_prep.py)."""
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prep import excel, formulas, graphio, tasks as taskmod, decode
from prep.build import build_zone, expand_vehicles

INPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "input")


# ---------------------------------------------------------------------------
# helpers: маленький синтетический граф для матриц/decode
# ---------------------------------------------------------------------------
def tiny_graph():
    """4 узла, 3 двусторонних ребра: 0-1-2 и 0-3 (тупик для недостижимости)."""
    nodes = {i: {"id": i, "lon": 158.6 + 0.01 * i, "lat": 53.0 + 0.01 * i,
                 "elevation_m": 0.0, "zone": 1} for i in range(4)}
    def edge(i, u, v, length, width=10.0, slope=0.0, oneway=False, zone=1):
        return {"id": f"e{i}", "u": u, "v": v, "key": 0, "length_m": length,
                "slope_pct": slope, "geometry": [[nodes[u]["lon"], nodes[u]["lat"]],
                                                 [nodes[v]["lon"], nodes[v]["lat"]]],
                "highway": "residential", "name": f"street-{i}", "oneway": oneway,
                "reverse_edge_id": None, "lanes": 2, "width_m": width,
                "width_dir_m": width / 2, "priority": 2, "zone": zone,
                "maxspeed": 60}
    edges = [edge(0, 0, 1, 100.0), edge(1, 1, 2, 100.0),
             edge(2, 0, 3, 50.0, width=3.0)]  # e2 слишком узкое для грейдера
    return {"nodes": nodes, "edges": edges, "meta": {}}


def tiny_pmap_and_solution():
    """map + решение HGS по tiny_graph: 1 КДМ, задачи на e0 (туда) и e1 (обратно)."""
    graph = tiny_graph()
    node_list = [{"id": i, "lon": n["lon"], "lat": n["lat"]}
                 for i, n in graph["nodes"].items()]
    edge_list = [{k: e[k] for k in ("id", "u", "v", "length_m", "slope_pct",
                                    "oneway", "width_m", "name", "geometry")}
                 for e in graph["edges"]]
    tasks = [
        {"id": 0, "edge_id": "e0", "start_node": 0, "end_node": 1, "i": 0,
         "pass_index": 0, "passes_dir": 1, "street": "street-0", "priority": 2,
         "length_m": 100.0, "weight": 0.3, "geometry": edge_list[0]["geometry"],
         "service_time": {"КДМ": 10.0}},
        {"id": 1, "edge_id": "e1", "start_node": 2, "end_node": 1, "i": 1,
         "pass_index": 0, "passes_dir": 1, "street": "street-1", "priority": 2,
         "length_m": 100.0, "weight": 0.3,
         "geometry": list(reversed(edge_list[1]["geometry"])),
         "service_time": {"КДМ": 10.0}},
    ]
    pmap = {
        "meta": {"zone": 1,
                 "condition": {"name": "Центр", "snow_h_cm": 15.0,
                               "traffic_coef": 1.0},
                 "crs": "EPSG:4326"},
        "classes": {"КДМ": {"v_transit_kmh": 60.0, "gabarit_m": 2.5},
                    "грейдер": {"v_transit_kmh": 35.0, "gabarit_m": 2.5}},
        "nodes": [{"i": 0, "id": 0, "lon": 158.60, "lat": 53.00},
                  {"i": 1, "id": 1, "lon": 158.61, "lat": 53.01},
                  {"i": 2, "id": 2, "lon": 158.62, "lat": 53.02}],
        "tasks": tasks,
        "vehicles": [{"id": "V1", "class": "КДМ", "start_i": 0, "end_i": 0,
                      "start_node": 0, "end_node": 0}],
        "graph": {"nodes": node_list, "edges": edge_list},
    }
    solution = {
        "status": "ok",
        "routes": [{"vehicle": "V1", "return_time": 400.0, "deadhead": 280.0,
                    "wait": 0.0,
                    "visits": [
                        {"task": 0, "arrive": 100.0, "start": 100.0, "end": 110.0},
                        {"task": 1, "arrive": 210.0, "start": 210.0, "end": 220.0},
                    ]}],
        "unassigned": [],
        "objective": {"total": 0.0, "latency": 0.0, "deadhead": 280.0,
                      "wait": 0.0, "overtime": 0.0, "makespan": 400.0},
        "stats": {"iterations": 1, "time_s": 0.0, "best_found_at_s": 0.0, "seed": 1},
    }
    return pmap, solution


# ---------------------------------------------------------------------------
# formulas
# ---------------------------------------------------------------------------
def test_formulas():
    assert formulas.snow_k(25.0, 20.0) == 0.8
    assert formulas.snow_k(15.0, 20.0) == 1.0
    assert formulas.slope_service_k(10.0) == 1.0 / 1.3
    assert formulas.slope_service_k(-10.0) == 1.0
    # время уборки: 100 м при 36 км/ч (=10 м/с), снег 20/25, подъём 10%
    st = formulas.service_time_s(100.0, 36.0, 25.0, 20.0, 10.0)
    expect = 100.0 / (10.0 * 0.8 * (1.0 / 1.3))
    assert abs(st - expect) < 1e-9, (st, expect)
    # транзит: 100 м при 36 км/ч, трафик 1.15, подъём 10%
    tt = formulas.transit_time_s(100.0, 36.0, 1.15, 10.0)
    assert abs(tt - 100.0 / (10.0 * 1.15) * 1.2) < 1e-9
    assert formulas.class_fits_width(2.5, 3.0)
    assert not formulas.class_fits_width(2.5, 2.9)  # 2.5 + 0.5 > 2.9
    print("ok test_formulas")


# ---------------------------------------------------------------------------
# excel / vehicles
# ---------------------------------------------------------------------------
def test_excel_and_vehicles():
    x = excel.load_excel(os.path.join(INPUT_DIR, "snegoplan_input.xlsx"))
    assert x["machines"] and all(m["start_lat"] for m in x["machines"])
    assert x["points"] and x["conditions"], "points/conditions should parse"
    assert x["density"]["слежавшийся"] == 400.0
    plow_types = [t for t in x["types"] if t["class"] in formulas.PLOW_CLASSES]
    assert {t["class"] for t in plow_types} == {"КДМ", "грейдер"}

    graph = graphio.load_graph(os.path.join(INPUT_DIR, "graph_v2.json"))
    types_by_model = {t["model"]: t for t in x["types"]}
    vehicles, skipped = expand_vehicles(x["machines"], types_by_model, graph)
    # M01..M03 КДМ + M04, M05 грейдер = 5 плугов; остальные 7 пропущены
    assert len(vehicles) == 5, [v["id"] for v in vehicles]
    assert len(skipped) == 7
    assert {v["class"] for v in vehicles} == {"КДМ", "грейдер"}
    for v in vehicles:  # снап внутри графа
        assert v["start_node"] in graph["nodes"]
        assert v["end_node"] in graph["nodes"]
        assert 0 <= v["shift_start"] < v["shift_end"] <= 28800.1
    print("ok test_excel_and_vehicles")


# ---------------------------------------------------------------------------
# pass counts (п.2) и способные классы (п.3)
# ---------------------------------------------------------------------------
def test_tasks_zone3():
    graph = graphio.load_graph(os.path.join(INPUT_DIR, "graph_v2.json"))
    x = excel.load_excel(os.path.join(INPUT_DIR, "snegoplan_input.xlsx"))
    plow_types = [t for t in x["types"] if t["class"] in formulas.PLOW_CLASSES]
    snow_h = x["conditions"]["Северные районы"]["snow_h_cm"]
    zone_tasks, warnings = taskmod.build_tasks(graph, 3, plow_types, snow_h)

    edges = graphio.zone_edges(graph, 3)
    max_ww = max(t["work_width_max_m"] for t in plow_types)
    expect_passes = sum(max(1, math.ceil(e["width_dir_m"] / max_ww))
                        for e in edges)
    assert len(zone_tasks) == expect_passes, (len(zone_tasks), expect_passes)
    assert max_ww == 3.55  # грейдер ДЗ-98

    by_edge = {}
    for t in zone_tasks:
        assert t["start_node"] != t["end_node"]
        by_edge.setdefault(t["edge_id"], []).append(t)
    edge_by_id = {e["id"]: e for e in edges}
    for eid, ts in by_edge.items():
        e = edge_by_id[eid]
        # направление фиксировано u->v, индексы проходов 0..n-1
        assert all(t["start_node"] == e["u"] and t["end_node"] == e["v"] for t in ts)
        assert sorted(t["pass_index"] for t in ts) == list(range(len(ts)))
        # вес по приоритету (п.4)
        for t in ts:
            assert abs(t["weight"]
                       - formulas.PRIORITY_WEIGHT[e["priority"]] * e["length_m"] / 1000) < 1e-9
            if e["priority"] == 1:
                assert t["deadline"] == 3 * 3600.0 and t["deadline_weight"] == 50.0
            else:
                assert t["deadline"] is None and t["deadline_weight"] == 0.0
        # ширина: ни один класс с габарит+0.5 > width_m не должен быть в service_time
        for cls, st in ts[0]["service_time"].items():
            tinfo = next(t for t in plow_types if t["class"] == cls)
            assert formulas.class_fits_width(tinfo["gabarit_m"], e["width_m"])
            assert st > 0
        # двусторонняя улица: у обоих направлений свои проходы
        if not e["oneway"] and e.get("reverse_edge_id") in by_edge:
            rev = edge_by_id[e["reverse_edge_id"]]
            assert len(by_edge[e["reverse_edge_id"]]) == max(
                1, math.ceil(rev["width_dir_m"] / max_ww))

    # нет задачи без способного класса, ЛИБО ребро в warnings (п.10)
    warned = set()
    for w in warnings:
        eid = w.split()[1]
        warned.add(eid)
    for t in zone_tasks:
        if not t["service_time"]:
            assert t["edge_id"] in warned, f"task {t['id']} без класса и не в warnings"
    print(f"ok test_tasks_zone3 (tasks={len(zone_tasks)}, warnings={len(warnings)})")
    return graph, x, zone_tasks, warnings


# ---------------------------------------------------------------------------
# матрицы перегона: сжатие, недостижимость, треугольник (п.5)
# ---------------------------------------------------------------------------
def test_matrices():
    graph = tiny_graph()
    kdm = {"model": "КДМ", "class": "КДМ", "gabarit_m": 2.5, "v_transit_kmh": 36.0}
    gr = {"model": "грейдер", "class": "грейдер", "gabarit_m": 2.5,
          "v_transit_kmh": 36.0}
    # e2 (0-3) width 3.0 < 2.5+0.5? нет, ровно впритык => разрешено; сделаем 2.9
    graph["edges"][2]["width_m"] = 2.9
    node_ids = [0, 1, 2, 3]
    mats = taskmod.build_matrices(graph, [kdm, gr], 1.0, node_ids)
    for cls, m in mats.items():
        assert len(m) == 4 and all(len(r) == 4 for r in m)
        for i in range(4):
            assert m[i][i] == 0.0
        # узел 3 недостижим (тупик e2 запрещён для обоих классов)
        for i in range(3):
            assert m[i][3] == -1.0 and m[3][i] == -1.0
        # треугольник: m[i][k] <= m[i][j] + m[j][k]
        for i in range(3):
            for j in range(3):
                for k in range(3):
                    assert m[i][k] <= m[i][j] + m[j][k] + 1e-9
        # oneway/двусторонность: 0<->1 обе стороны
        assert m[0][1] > 0 and m[1][0] > 0
        # транзит 0->1: 100 м при 36 км/ч = 10 с
        assert abs(m[0][1] - 10.0) < 1e-6

    # реальный граф, зона 3: матрица КДМ по сжатым узлам
    big = graphio.load_graph(os.path.join(INPUT_DIR, "graph_v2.json"))
    x = excel.load_excel(os.path.join(INPUT_DIR, "snegoplan_input.xlsx"))
    plow_types = [t for t in x["types"] if t["class"] in formulas.PLOW_CLASSES]
    zone_tasks, _ = taskmod.build_tasks(big, 3, plow_types, 25.0)
    node_ids = sorted({t["start_node"] for t in zone_tasks}
                      | {t["end_node"] for t in zone_tasks})
    mats = taskmod.build_matrices(big, plow_types, 1.15, node_ids)
    assert set(mats) == {"КДМ", "грейдер"}
    m = mats["КДМ"]
    n = len(node_ids)
    assert len(m) == n
    neg = sum(1 for r in m for v in r if v == -1.0)
    total = n * n
    assert neg / total < 0.05, f"too much unreachable: {neg}/{total}"
    import random
    random.seed(1)
    for _ in range(300):
        i, j, k = (random.randrange(n) for _ in range(3))
        if m[i][j] >= 0 and m[j][k] >= 0:
            assert m[i][k] >= 0, "reachability must be transitive"
            assert m[i][k] <= m[i][j] + m[j][k] + 1e-6, "triangle violated"
    print(f"ok test_matrices (n={n}, unreachable={neg}/{total})")


# ---------------------------------------------------------------------------
# build_zone: структура входа HGS + map-файла
# ---------------------------------------------------------------------------
def test_build_zone_contract():
    plows, pmap, info = build_zone(INPUT_DIR, 3, time_limit_s=10, seed=1)
    # контракт SPEC.md
    assert plows["n_nodes"] == len(pmap["nodes"])
    for cls, c in plows["classes"].items():
        m = c["matrix"]
        assert len(m) == plows["n_nodes"]
        assert all(len(r) == plows["n_nodes"] for r in m)
    ids = [t["id"] for t in plows["tasks"]]
    assert len(ids) == len(set(ids)) == len(pmap["tasks"])
    for t in plows["tasks"]:
        assert 0 <= t["start_node"] < plows["n_nodes"]
        assert 0 <= t["end_node"] < plows["n_nodes"]
        assert t["service_time"], f"task {t['id']} без способного класса"
        assert t["weight"] > 0
    for v in plows["vehicles"]:
        assert v["class"] in plows["classes"]
        assert 0 <= v["start_node"] < plows["n_nodes"]
        assert v["shift_end"] == 28800.0
    assert plows["objective"] == {"w_latency": 1.0, "w_deadhead": 0.2,
                                  "w_wait": 0.0, "w_overtime": 10.0,
                                  "w_makespan": 0.0}
    assert plows["params"]["time_limit_s"] == 10 and plows["params"]["seed"] == 1
    # map-файл декодируем: i <-> id
    graph_node_ids = {n["id"] for n in pmap["graph"]["nodes"]}
    for i, n in enumerate(pmap["nodes"]):
        assert n["i"] == i and n["id"] in graph_node_ids
    by_i = {n["i"]: n["id"] for n in pmap["nodes"]}
    for t in pmap["tasks"]:
        assert t["start_node"] in by_i and t["end_node"] in by_i  # сжатые индексы
        assert t["start_graph_node"] in graph_node_ids
        assert by_i[t["start_node"]] == t["start_graph_node"]
        assert t["geometry"] and t["street"] is not None
    # всё сериализуется
    json.dumps(plows, ensure_ascii=False)
    json.dumps(pmap, ensure_ascii=False)
    print(f"ok test_build_zone_contract (tasks={len(plows['tasks'])}, "
          f"vehicles={len(plows['vehicles'])})")


# ---------------------------------------------------------------------------
# round-trip decode: map + solution -> GeoJSON -> проверки
# ---------------------------------------------------------------------------
def test_decode_roundtrip():
    pmap, solution = tiny_pmap_and_solution()
    features, stats = decode.decode(pmap, solution)

    kinds = [f["properties"]["kind"] for f in features]
    # 2 сервиса + 2 перегона (между задачами и домой; 0->0 в начале нулевой)
    assert kinds == ["service", "deadhead", "service", "deadhead"], kinds

    node_coord = {n["id"]: [n["lon"], n["lat"]] for n in pmap["graph"]["nodes"]}
    t_by_id = {t["id"]: t for t in pmap["tasks"]}

    prev_end = pmap["vehicles"][0]["start_node"]
    visits_seen = []
    for f in features:
        coords = f["geometry"]["coordinates"]
        assert len(coords) >= 2, "каждый leg — LineString с >=2 точками"
        p = f["properties"]
        if p["kind"] == "service":
            t = t_by_id[p["task"]]
            assert p["street"] == t["street"]
            vis = next(v for v in solution["routes"][0]["visits"]
                       if v["task"] == p["task"])
            assert p["start_time"] == decode._hhmm(vis["start"])
            assert p["end_time"] == decode._hhmm(vis["end"])
            assert coords[0] == node_coord[t["start_node"]], "сервис стартует в start_node"
            assert coords[-1] == node_coord[t["end_node"]], "сервис кончается в end_node"
            visits_seen.append(p["task"])
            prev_end = t["end_node"]
        else:
            # deadhead соединяет prev_end со start_node следующей задачи / финиша
            dst = t_by_id[features[features.index(f) + 1]["properties"]["task"]]["start_node"] \
                if p is not features[-1]["properties"] else pmap["vehicles"][0]["end_node"]
            assert coords[0] == node_coord[prev_end], "перегон начинается там, где был конец"
            assert coords[-1] == node_coord[dst], "перегон приходит в start_node задачи"
            assert p["start_time"] <= p["end_time"]
    assert sorted(visits_seen) == [0, 1], "каждая задача встречается ровно раз"
    s = stats["V1"]
    assert abs(s["service_km"] - 0.2) < 1e-9, s
    assert s["deadhead_km"] > 0
    print("ok test_decode_roundtrip")

    # CLI decode пишет валидный GeoJSON
    with tempfile.TemporaryDirectory() as td:
        mp = os.path.join(td, "m.json"); sp = os.path.join(td, "s.json")
        op = os.path.join(td, "o.geojson")
        json.dump(pmap, open(mp, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(solution, open(sp, "w", encoding="utf-8"), ensure_ascii=False)
        decode.main(["--map", mp, "--solution", sp, "--out", op])
        fc = json.load(open(op, encoding="utf-8"))
        assert fc["type"] == "FeatureCollection"
        assert len(fc["features"]) == len(features)
    print("ok test_decode_cli")


if __name__ == "__main__":
    test_formulas()
    test_excel_and_vehicles()
    test_tasks_zone3()
    test_matrices()
    test_build_zone_contract()
    test_decode_roundtrip()
    print("\nALL TESTS PASSED")
