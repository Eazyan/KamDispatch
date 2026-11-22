"""Greedy nearest-task and random-order baselines using the common objective evaluator."""
import random
from common import Prob


def greedy(prob):
    """Earliest-available vehicle takes the task minimising weighted arrival cost.
    Returns (routes, unassigned)."""
    routes = {v["id"]: [] for v in prob.veh}
    state = {v["id"]: [v["start_node"], float(v["shift_start"])] for v in prob.veh}
    remaining = [t for t in prob.task_ids if prob.servable(t)]
    unassigned = [t for t in prob.task_ids if not prob.servable(t)]
    dead = set()
    while remaining:
        cands = [v for v in prob.veh if v["id"] not in dead]
        if not cands:
            unassigned += remaining
            break
        v = min(cands, key=lambda x: state[x["id"]][1])
        node, t = state[v["id"]]
        M = prob.M[v["class"]]
        best, bc = None, None
        for tid in remaining:
            if not prob.compat(v, tid):
                continue
            tk = prob.tasks[tid]
            d = M[node][tk["start_node"]]
            if d < 0:
                continue
            arr = t + d
            st = max(arr, prob.ready[tid])
            end = st + prob.svc[tid][v["class"]]
            c = prob.w_lat * tk["weight"] * end + prob.w_dh * d + prob.w_wait * (st - arr)
            if bc is None or c < bc:
                best, bc, bend = tid, c, end
        if best is None:
            dead.add(v["id"])
            continue
        routes[v["id"]].append(best)
        remaining.remove(best)
        state[v["id"]] = [prob.tasks[best]["end_node"], bend]
    return routes, sorted(unassigned)


def random_order(prob, seed=0):
    rng = random.Random(seed)
    routes = {v["id"]: [] for v in prob.veh}
    order = list(prob.task_ids)
    rng.shuffle(order)
    unassigned = []
    for tid in order:
        vs = [v for v in prob.veh if prob.compat(v, tid)]
        if not vs:
            unassigned.append(tid)
            continue
        routes[rng.choice(vs)["id"]].append(tid)
    return routes, sorted(unassigned)


def total(prob, routes):
    return prob.evaluate(routes)["total"]
