"""Brute-force optimal solver for tiny instances (DFS vehicle-by-vehicle with branch & bound)."""
import math, time
from common import Prob
import baselines


def solve_exact(prob, time_limit=120.0):
    """Returns (best_total, routes) or (None, None) on timeout / infeasible. Uses the same objective."""
    tids = prob.task_ids
    n = len(tids)
    idx = {t: i for i, t in enumerate(tids)}
    V = prob.veh
    nv = len(V)
    if any(not prob.servable(t) for t in tids):
        raise ValueError("infeasible instance (unservable task)")
    suffix = [0] * (nv + 1)
    for vi in range(nv - 1, -1, -1):
        m = suffix[vi + 1]
        for t in tids:
            if prob.compat(V[vi], t):
                m |= 1 << idx[t]
        suffix[vi] = m
    min_shift = [min(float(v["shift_start"]) for v in V[vi:]) if vi < nv else 0.0 for vi in range(nv + 1)]
    lb_task = []  # per task latency lower bound at any vehicle
    for t in tids:
        best = min(float(v["shift_start"]) + prob.svc[t][v["class"]] for v in V if prob.compat(v, t))
        lb_task.append(prob.w_lat * prob.tasks[t]["weight"] * max(best, prob.ready[t] + min(
            prob.svc[t][v["class"]] for v in V if prob.compat(v, t))))
    g_routes, g_un = baselines.greedy(prob)
    best = [prob.evaluate(g_routes)["total"] * (1 + 1e-9) + 1e-9, None]
    t0 = time.time()
    timed_out = [False]
    cur = [[] for _ in range(nv)]
    full = (1 << n) - 1
    cnt = [0]

    def rem_lb(rem):
        s, i = 0.0, 0
        while rem:
            if rem & 1:
                s += lb_task[i]
            rem >>= 1
            i += 1
        return s

    def dfs(vi, node, t, rem, cost, ms):
        cnt[0] += 1
        if (cnt[0] & 0x3FFF) == 0 and time.time() - t0 > time_limit:
            timed_out[0] = True
        if timed_out[0]:
            return
        if rem & ~suffix[vi]:
            return
        if cost + prob.w_ms * ms + rem_lb(rem) >= best[0]:
            return
        v = V[vi]
        M = prob.M[v["class"]]
        cls = v["class"]
        seq = cur[vi]
        # extend
        r = rem
        while r:
            low = r & -r
            j = low.bit_length() - 1
            r ^= low
            tid = tids[j]
            if cls not in prob.svc[tid]:
                continue
            tk = prob.tasks[tid]
            d = M[node][tk["start_node"]]
            if d < 0:
                continue
            arr = t + d
            st = max(arr, prob.ready[tid])
            end = st + prob.svc[tid][cls]
            c = cost + prob.w_dh * d + prob.w_wait * (st - arr) + prob.w_lat * tk["weight"] * end
            dl = prob.deadline[tid]
            if dl is not None:
                c += prob.dw[tid] * max(0.0, end - dl)
            seq.append(tid)
            dfs(vi, tk["end_node"], end, rem & ~low, c, ms)
            seq.pop()
        # close this vehicle
        if seq:
            d = M[node][v["end_node"]]
            if d < 0:
                return
            ret = t + d
            c = cost + prob.w_dh * d + prob.w_ot * max(0.0, ret - v["shift_end"])
            ms2 = max(ms, ret)
        else:
            c, ms2 = cost, ms
        if vi + 1 == nv:
            if rem == 0:
                tot = c + prob.w_ms * ms2
                if tot < best[0]:
                    best[0] = tot
                    best[1] = {V[i]["id"]: list(cur[i]) for i in range(nv)}
        elif True:
            nxt = V[vi + 1]
            dfs(vi + 1, nxt["start_node"], float(nxt["shift_start"]), rem, c, ms2)

    first = V[0]
    dfs(0, first["start_node"], float(first["shift_start"]), full, 0.0, 0.0)
    if timed_out[0]:
        return None, None
    if best[1] is None:  # greedy was already optimal (bound pruned everything)
        best[1] = g_routes
    return prob.evaluate(best[1])["total"], best[1]


if __name__ == "__main__":
    import sys
    from common import load_json
    p = Prob(load_json(sys.argv[1]))
    tot, r = solve_exact(p)
    print(tot, r)
