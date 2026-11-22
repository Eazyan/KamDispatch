"""CLI: python3 -m prep.build --zone 3 --out out/zone3_plows.json --map out/zone3_map.json

Собирает вход HGS (задачи-проходы, техника, матрицы перегона) и map-файл
для декодирования. Опционально запускает солвер (--solution путь).
"""
import argparse
import json
import os
import subprocess
import sys

from . import excel, formulas, graphio, tasks as taskmod


def expand_vehicles(machines, types_by_model, graph):
    """Машины -> ТС; кол-во>1 раскладывается с суффиксами. Только плуговые классы."""
    vehicles = []
    skipped = []
    for m in machines:
        tinfo = types_by_model.get(m["type"])
        if tinfo is None:
            skipped.append((m["id"], "тип не найден"))
            continue
        if tinfo["class"] not in formulas.PLOW_CLASSES:
            skipped.append((m["id"], f"класс {tinfo['class']} не плуговый"))
            continue
        for k in range(m["count"]):
            vid = m["id"] if m["count"] == 1 else f"{m['id']}-{k + 1}"
            s_node, s_d = graphio.snap_node(graph, m["start_lat"], m["start_lon"])
            e_node, e_d = graphio.snap_node(graph, m["finish_lat"], m["finish_lon"])
            vehicles.append({
                "id": vid, "machine_id": m["id"], "class": tinfo["class"],
                "type": tinfo["model"], "driver": m.get("driver"),
                "start_node": s_node, "end_node": e_node,
                "snap_start_dist_m": round(s_d, 1),
                "snap_finish_dist_m": round(e_d, 1),
                "start_addr": m["start_addr"], "finish_addr": m["finish_addr"],
                "shift_start": formulas.DEFAULT_SHIFT_START_S,
                "shift_end": formulas.DEFAULT_SHIFT_END_S,
            })
    return vehicles, skipped


def build_zone(input_dir, zone_id, time_limit_s=10, seed=1):
    graph = graphio.load_graph(os.path.join(input_dir, "graph_v2.json"))
    xlsx = excel.load_excel(os.path.join(input_dir, "snegoplan_input.xlsx"))

    cond_name = formulas.ZONE_CONDITION_NAME.get(zone_id)
    if cond_name not in xlsx["conditions"]:
        raise SystemExit(f"нет условий для зоны {zone_id} ({cond_name!r})")
    cond = dict(xlsx["conditions"][cond_name])
    cond["name"] = cond_name
    snow_h = cond["snow_h_cm"]
    traffic = cond["traffic_coef"]

    plow_types = [t for t in xlsx["types"] if t["class"] in formulas.PLOW_CLASSES]
    types_by_model = {t["model"]: t for t in xlsx["types"]}
    vehicles, skipped = expand_vehicles(xlsx["machines"], types_by_model, graph)
    if not vehicles:
        raise SystemExit("нет плуговой техники в листе «Машины»")
    if not plow_types:
        raise SystemExit("нет плуговых типов в листе «Типы техники»")

    zone_tasks, warnings = taskmod.build_tasks(graph, zone_id, plow_types, snow_h)

    # сжатое множество узлов: старт/финиш задач + старт/финиш техники
    node_ids = sorted({t["start_node"] for t in zone_tasks}
                      | {t["end_node"] for t in zone_tasks}
                      | {v["start_node"] for v in vehicles}
                      | {v["end_node"] for v in vehicles})
    index = {n: i for i, n in enumerate(node_ids)}

    matrices = taskmod.build_matrices(graph, plow_types, traffic, node_ids)

    plows = {
        "n_nodes": len(node_ids),
        "classes": {c: {"matrix": m} for c, m in matrices.items()},
        "tasks": [{
            "id": t["id"],
            "start_node": index[t["start_node"]],
            "end_node": index[t["end_node"]],
            "service_time": t["service_time"],
            "weight": t["weight"],
            "ready_time": t["ready_time"],
            "deadline": t["deadline"],
            "deadline_weight": t["deadline_weight"],
        } for t in zone_tasks],
        "vehicles": [{
            "id": v["id"], "class": v["class"],
            "start_node": index[v["start_node"]],
            "end_node": index[v["end_node"]],
            "shift_start": v["shift_start"], "shift_end": v["shift_end"],
        } for v in vehicles],
        "objective": dict(formulas.DEFAULT_OBJECTIVE),
        "params": {**formulas.DEFAULT_PARAMS,
                   "time_limit_s": time_limit_s, "seed": seed},
    }

    pmap = {
        "meta": {
            "zone": zone_id,
            "condition": cond,
            "input_dir": input_dir,
            "warnings": warnings,
            "skipped_machines": [f"{i}: {r}" for i, r in skipped],
            "objective": plows["objective"],
            "params": plows["params"],
        },
        "classes": {t["class"]: {
            "v_transit_kmh": t["v_transit_kmh"],
            "gabarit_m": t["gabarit_m"],
        } for t in plow_types},
        "nodes": [{"i": i, "id": n,
                   "lon": graph["nodes"][n]["lon"],
                   "lat": graph["nodes"][n]["lat"]}
                  for i, n in enumerate(node_ids)],
        "tasks": [{k: t[k] for k in
                   ("id", "edge_id", "pass_index",
                    "passes_dir", "street", "priority", "length_m",
                    "weight", "geometry")}
                  | {"service_time": t["service_time"],
                     # сжатые индексы (как в plows["tasks"]) — decode ходит по ним
                     "start_node": index[t["start_node"]],
                     "end_node": index[t["end_node"]],
                     # исходные id узлов графа — для справки
                     "start_graph_node": t["start_node"],
                     "end_graph_node": t["end_node"]}
                  for t in zone_tasks],
        "vehicles": [dict(v, start_i=index[v["start_node"]],
                          end_i=index[v["end_node"]]) for v in vehicles],
        "graph": {
            "nodes": [{"id": n["id"], "lon": n["lon"], "lat": n["lat"]}
                      for n in graph["nodes"].values()],
            "edges": [{k: e[k] for k in
                       ("id", "u", "v", "length_m", "slope_pct", "oneway",
                        "width_m", "name", "geometry")} | {"highway": e["highway"]}
                      for e in graph["edges"]],
        },
    }
    return plows, pmap, {"zone_tasks": len(zone_tasks),
                         "vehicles": len(vehicles),
                         "warnings": warnings, "skipped": skipped,
                         "cond": cond, "node_count": len(node_ids)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="prep.build")
    ap.add_argument("--zone", type=int, required=True)
    ap.add_argument("--out", required=True, help="plows JSON (вход HGS)")
    ap.add_argument("--map", required=True, help="map JSON для decode")
    ap.add_argument("--input-dir", default="input")
    ap.add_argument("--time-limit-s", type=float, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--solution", default=None,
                    help="если задан — запустить hgs и сохранить ответ сюда")
    args = ap.parse_args(argv)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(args.map) or ".", exist_ok=True)

    plows, pmap, info = build_zone(args.input_dir, args.zone,
                                   args.time_limit_s, args.seed)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(plows, f, ensure_ascii=False)
    with open(args.map, "w", encoding="utf-8") as f:
        json.dump(pmap, f, ensure_ascii=False)

    print(f"zone {args.zone}: tasks={info['zone_tasks']} "
          f"nodes={info['node_count']} vehicles={info['vehicles']} "
          f"condition={info['cond']['name']} (h={info['cond']['snow_h_cm']}cm, "
          f"traffic={info['cond']['traffic_coef']})")
    for w in info["warnings"]:
        print("WARNING:", w, file=sys.stderr)

    if args.solution:
        hgs = os.path.join(args.input_dir, "hgs")
        with open(args.solution, "w", encoding="utf-8") as out:
            r = subprocess.run([hgs, args.out, args.solution],
                               capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"hgs failed: {r.stderr or r.stdout}")
        sol = json.load(open(args.solution, encoding="utf-8"))
        print(f"solver status={sol['status']} "
              f"objective_total={sol['objective']['total']:.1f} "
              f"unassigned={sol.get('unassigned')}")
        from .decode import summarize
        summarize(pmap, sol)


if __name__ == "__main__":
    main()
