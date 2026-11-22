#!/usr/bin/env python3
"""Deterministic instance generator for the hgs solver contract (see ../SPEC.md).

Road-like graph (perturbed grid or random geometric graph), some one-way streets, uphill-penalised
asymmetric travel times, tasks = streets (edges), per-class service times, priority-folded weights,
optional ready times / deadlines, heterogeneous vehicles (plow / loader).
"""
import argparse, heapq, json, math, random, sys

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None
try:
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra as sp_dijkstra
except Exception:  # pragma: no cover
    sp_dijkstra = None

PRESETS = {  # name: (tasks, vehicles)
    "tiny": (None, None),  # 5..9 tasks, 1..2 vehicles chosen from seed
    "small": (30, 3),
    "m100": (100, 5),
    "m300": (300, 10),
    "l500": (500, 15),
    "l1000": (1000, 25),
}
TRAVEL_SPEED = {"plow": 6.0, "loader": 8.0}      # m/s deadhead
SERVICE_SPEED = {"plow": 2.5, "loader": 1.6}     # m/s while working
UPHILL_K = {"plow": 5.0, "loader": 8.0}          # uphill slowdown factor per unit slope


def _connected(n, adj, radj):
    for a in (adj, radj):
        seen = {0}
        st = [0]
        while st:
            u = st.pop()
            for v in a[u]:
                if v not in seen:
                    seen.add(v)
                    st.append(v)
        if len(seen) != n:
            return False
    return True


def build_graph(rng, n_nodes, kind, oneway_frac=0.12, drop_frac=0.08):
    """Returns coords, z, directed edge set {(u,v)} (strongly connected)."""
    sp = 100.0
    if kind == "rgg":
        side = math.sqrt(n_nodes) * sp
        pts = [(rng.uniform(0, side), rng.uniform(0, side)) for _ in range(n_nodes)]
        und = set()
        for i in range(n_nodes):
            ds = sorted(range(n_nodes), key=lambda j: (pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2)
            for j in ds[1:4]:
                und.add((min(i, j), max(i, j)))
        # connect components
        parent = list(range(n_nodes))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for a, b in und:
            parent[find(a)] = find(b)
        while len({find(i) for i in range(n_nodes)}) > 1:
            best = None
            for i in range(n_nodes):
                for j in range(i + 1, n_nodes):
                    if find(i) != find(j):
                        d = (pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2
                        if best is None or d < best[0]:
                            best = (d, i, j)
            _, i, j = best
            und.add((i, j))
            parent[find(i)] = find(j)
        coords = pts
    else:
        w = max(3, int(math.ceil(math.sqrt(n_nodes))))
        h = max(3, int(math.ceil(n_nodes / w)))
        n_nodes = w * h
        coords = [((i % w) * sp + rng.uniform(-0.25, 0.25) * sp, (i // w) * sp + rng.uniform(-0.25, 0.25) * sp)
                  for i in range(n_nodes)]
        und = set()
        for i in range(n_nodes):
            if i % w < w - 1:
                und.add((i, i + 1))
            if i // w < h - 1:
                und.add((i, i + w))
    amp = 15.0
    ph = [rng.uniform(0, 6.28) for _ in range(4)]
    z = [amp * (math.sin(x / 400 + ph[0]) + math.cos(y / 350 + ph[1]) + 0.5 * math.sin((x + y) / 200 + ph[2]))
         for x, y in coords]
    directed = set()
    for a, b in und:
        directed.add((a, b))
        directed.add((b, a))

    def adjs():
        adj = [[] for _ in range(n_nodes)]
        radj = [[] for _ in range(n_nodes)]
        for a, b in directed:
            adj[a].append(b)
            radj[b].append(a)
        return adj, radj
    ulist = sorted(und)
    rng.shuffle(ulist)
    # drop some streets (keeping connectivity), then make some one-way
    for a, b in ulist[: int(len(ulist) * drop_frac)]:
        directed.discard((a, b)); directed.discard((b, a))
        adj, radj = adjs()
        if not _connected(n_nodes, adj, radj):
            directed.add((a, b)); directed.add((b, a))
    two = sorted((a, b) for a, b in directed if a < b and (b, a) in directed)
    rng.shuffle(two)
    for a, b in two[: int(len(two) * oneway_frac)]:
        drop = (a, b) if rng.random() < 0.5 else (b, a)
        directed.discard(drop)
        adj, radj = adjs()
        if not _connected(n_nodes, adj, radj):
            directed.add(drop)
    return coords, z, directed


def edge_len(coords, a, b):
    return max(30.0, math.hypot(coords[a][0] - coords[b][0], coords[a][1] - coords[b][1]))


def travel_cost(coords, z, a, b, cls):
    L = edge_len(coords, a, b)
    slope = (z[b] - z[a]) / L
    return L / TRAVEL_SPEED[cls] * (1.0 + UPHILL_K[cls] * max(0.0, slope))


def all_pairs(n, directed, coords, z, cls, sources):
    """Shortest travel time from each source (graph node) to all graph nodes: dict src -> list."""
    rows, cols, vals = [], [], []
    adj = [[] for _ in range(n)]
    for a, b in directed:
        c = travel_cost(coords, z, a, b, cls)
        adj[a].append((b, c))
        rows.append(a); cols.append(b); vals.append(c)
    if sp_dijkstra is not None and np is not None:
        g = csr_matrix((vals, (rows, cols)), shape=(n, n))
        D = sp_dijkstra(g, directed=True, indices=sources)
        return {s: D[i] for i, s in enumerate(sources)}
    out = {}
    for s in sources:
        dist = [math.inf] * n
        dist[s] = 0.0
        pq = [(0.0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            for v, c in adj[u]:
                nd = d + c
                if nd < dist[v]:
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        out[s] = dist
    return out


def make(preset="small", seed=1, mode="mixed", ready=False, deadlines=False, graph="grid",
         n_tasks=None, n_vehicles=None, time_limit=10.0):
    """Build an instance dict. preset in PRESETS or an int number of tasks."""
    rng = random.Random("hgs-gen-v1-%s-%s" % (preset, seed))
    if isinstance(preset, int):
        K, V = preset, max(1, preset // 30 + 1)
    elif preset == "tiny":
        K, V = rng.randint(5, 9), rng.randint(1, 2)
    else:
        K, V = PRESETS[preset]
    K = n_tasks or K
    V = n_vehicles or V
    tiny = K <= 9
    n_graph = max(25, int(K * 0.9)) if not tiny else 25
    coords, z, directed = build_graph(rng, n_graph, graph)
    n = len(coords)

    classes = ["plow"] if mode == "plow" else ["plow", "loader"]
    # vehicles
    depots = rng.sample(range(n), min(n, 3))
    vclasses = []
    for i in range(V):
        vclasses.append("plow" if (mode == "plow" or i % 2 == 0) else "loader")
    if mode != "plow" and "loader" not in vclasses:
        vclasses[-1] = "loader"
    if mode != "plow" and "plow" not in vclasses:
        vclasses[0] = "plow"
    if mode != "plow" and V == 1:
        vclasses = ["plow"]; classes = ["plow"]
    # tasks (streets)
    und = sorted({(min(a, b), max(a, b)) for a, b in directed})
    rng.shuffle(und)
    tasks = []
    for a, b in und:
        if len(tasks) >= K:
            break
        both = (a, b) in directed and (b, a) in directed
        if both and rng.random() < 0.35 and len(tasks) + 2 <= K:
            dirs = [(a, b), (b, a)]
        elif both:
            dirs = [(a, b) if rng.random() < 0.5 else (b, a)]
        else:
            dirs = [(a, b) if (a, b) in directed else (b, a)]
        for (s, e) in dirs:
            tasks.append((s, e))
    avail = set(classes)
    pr = [10, 3, 1]
    tl = []
    for i, (s, e) in enumerate(tasks):
        L = edge_len(coords, s, e)
        prio = rng.choices(pr, weights=[0.2, 0.3, 0.5])[0]
        if mode == "plow" or "loader" not in avail:
            kinds = ["plow"]
        else:
            r = rng.random()
            kinds = ["plow"] if r < 0.7 else (["loader"] if r < 0.85 else ["plow", "loader"])
        svc = {}
        for c in kinds:
            svc[c] = round(max(10.0, L / SERVICE_SPEED[c]) + (30.0 if c == "loader" else 0.0), 1)
        tl.append(dict(id=i, start_node=s, end_node=e, service_time=svc,
                       weight=round(prio * L / 100.0, 3), _len=L, _kinds=kinds))
    tot_service = sum(min(t["service_time"].values()) for t in tl)
    H = tot_service / V * 1.4 + 1.0  # rough per-vehicle horizon incl. deadhead
    for t in tl:
        rd = 0.0
        if "loader" in t["_kinds"] and t["_kinds"] == ["loader"] and mode != "plow":
            rd = rng.uniform(0.05, 0.6) * H  # loader work becomes available after plows pass
        elif ready and rng.random() < 0.4:
            rd = rng.uniform(0.0, 0.7) * H
        t["ready_time"] = round(rd, 1)
        if deadlines and rng.random() < 0.3:
            t["deadline"] = round(rd + min(t["service_time"].values()) + rng.uniform(0.1, 0.8) * H, 1)
            t["deadline_weight"] = round(rng.choice([1.0, 5.0, 20.0]) * 1.0, 1)
        else:
            t["deadline"] = None
            t["deadline_weight"] = 0.0
    # vehicles
    veh = []
    cnt = {"plow": 0, "loader": 0}
    for c in vclasses:
        cnt[c] += 1
        vid = ("П-%02d" if c == "plow" else "Л-%02d") % cnt[c]
        s = rng.choice(depots)
        e = s if rng.random() < 0.5 else rng.choice(depots)
        shift_end = round(max(300.0, H * rng.uniform(0.7, 1.5)), 1)
        veh.append(dict(id=vid, **{"class": c}, start_node=s, end_node=e,
                        shift_start=round(rng.uniform(0, 0.05) * H, 1) if c == "loader" else 0.0, shift_end=shift_end))
    # ensure every task has at least one existing class
    vcls = {v["class"] for v in veh}
    for t in tl:
        if not (set(t["service_time"]) & vcls):
            c = sorted(vcls)[0]
            t["service_time"][c] = round(max(10.0, t["_len"] / SERVICE_SPEED[c]), 1)
    # compressed node set
    used = sorted({t["start_node"] for t in tl} | {t["end_node"] for t in tl}
                  | {v["start_node"] for v in veh} | {v["end_node"] for v in veh})
    remap = {g: i for i, g in enumerate(used)}
    cmat = {}
    for c in sorted(vcls):
        D = all_pairs(n, directed, coords, z, c, used)
        cmat[c] = {"matrix": [[round(float(D[s][g]), 2) for g in used] for s in used]}
    tasks_out = []
    for t in tl:
        tasks_out.append(dict(id=t["id"], start_node=remap[t["start_node"]], end_node=remap[t["end_node"]],
                              service_time=t["service_time"], weight=t["weight"], ready_time=t["ready_time"],
                              deadline=t["deadline"], deadline_weight=t["deadline_weight"]))
    for v in veh:
        v["start_node"] = remap[v["start_node"]]
        v["end_node"] = remap[v["end_node"]]
    obj = {"w_latency": 1.0, "w_deadhead": 1.0, "w_wait": 0.5, "w_overtime": 1000.0, "w_makespan": 0.0}
    if tiny:
        obj["w_wait"] = rng.choice([0.5, 1.0])
        obj["w_makespan"] = rng.choice([0.0, 0.0, 0.2])
        obj["w_latency"] = rng.choice([1.0, 1.0, 2.0])
        obj["w_overtime"] = rng.choice([1000.0, 5.0])
    return {"n_nodes": len(used), "classes": cmat, "tasks": tasks_out, "vehicles": veh, "objective": obj,
            "params": {"time_limit_s": time_limit, "seed": seed, "pop_min": 25, "pop_gen": 40,
                       "max_iter_no_improve": 20000}}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preset", default="small", help="tiny|small|m100|m300|l500|l1000 or integer task count")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mode", default="mixed", choices=["plow", "mixed"])
    ap.add_argument("--graph", default="grid", choices=["grid", "rgg"])
    ap.add_argument("--ready", action="store_true")
    ap.add_argument("--deadlines", action="store_true")
    ap.add_argument("--tasks", type=int)
    ap.add_argument("--vehicles", type=int)
    ap.add_argument("--time-limit", type=float, default=10.0)
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    preset = int(a.preset) if a.preset.isdigit() else a.preset
    inst = make(preset, a.seed, a.mode, a.ready, a.deadlines, a.graph, a.tasks, a.vehicles, a.time_limit)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(inst, f, ensure_ascii=False, separators=(",", ":"))
    print("wrote %s: %d tasks, %d vehicles, %d nodes" % (a.out, len(inst["tasks"]), len(inst["vehicles"]), inst["n_nodes"]))


if __name__ == "__main__":
    main()
