"""Построение задач-проходов и сжатых матриц перегона по классам."""
import math

from . import formulas
from . import graphio


def build_tasks(graph, zone_id, plow_types, snow_h_cm):
    """Задачи-проходы для всех рёбер зоны.

    Число проходов — по максимальной рабочей ширине среди плуговых классов;
    время обслуживания хранится поклассово (dict class -> секунды).
    Возвращает (tasks, warnings).
    """
    max_ww = max(t["work_width_max_m"] for t in plow_types)
    tasks, warnings = [], []
    tid = 0
    for e in graphio.zone_edges(graph, zone_id):
        passes_dir = max(1, math.ceil(e["width_dir_m"] / max_ww))
        service = {}
        for t in plow_types:
            if not formulas.class_fits_width(t["gabarit_m"], e["width_m"]):
                continue  # класс не влезает по ширине
            st = formulas.service_time_s(
                e["length_m"], t["v_work_kmh"],
                snow_h_cm, t["h_ref_cm"], e["slope_pct"])
            if st is None:
                continue
            service[t["class"]] = st  # одинаковые классы -> последний тип, ок
        if not service:
            warnings.append(
                f"edge {e['id']} ({e.get('name')}): нет ни одного способного "
                f"класса (width_m={e['width_m']})")
        pr = e["priority"]
        for p in range(passes_dir):
            task = {
                "id": tid,
                "start_node": e["u"],     # направление фиксировано u->v
                "end_node": e["v"],
                "service_time": service,
                "weight": formulas.PRIORITY_WEIGHT[pr] * e["length_m"] / 1000.0,
                "ready_time": 0.0,
                "deadline": (formulas.PRIORITY1_DEADLINE_S if pr == 1 else None),
                "deadline_weight": (formulas.PRIORITY1_DEADLINE_WEIGHT
                                    if pr == 1 else 0.0),
                # --- для map-файла ---
                "edge_id": e["id"],
                "pass_index": p,
                "passes_dir": passes_dir,
                "street": e.get("name"),
                "priority": pr,
                "length_m": e["length_m"],
                "geometry": e["geometry"],
            }
            tasks.append(task)
            tid += 1
    return tasks, warnings


def build_matrices(graph, plow_types, traffic_coef, node_ids):
    """Матрицы перегона по классам, сжатые до node_ids (в том же порядке).

    Возвращает {class: [[sec]]}, недостижимое = -1.
    """
    classes = {t["class"]: t for t in plow_types}
    matrices = {}
    for cname, tinfo in classes.items():
        G = graphio.transit_graph(graph, tinfo, traffic_coef)
        dist = graphio.class_shortest_paths(G, node_ids)
        m = []
        for s in node_ids:
            row = []
            for d in node_ids:
                row.append(dist[s].get(d, -1.0))
            m.append(row)
        matrices[cname] = m
    return matrices
