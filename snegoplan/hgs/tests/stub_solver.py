#!/usr/bin/env python3
"""Fake solver implementing the hgs CLI contract (for harness self-test only).
Tiny instances: exact. Otherwise: greedy + seeded relocate hill-climb (iteration bounded => deterministic)."""
import json, random, sys, time
import baselines, exact
from common import Prob, make_output


def main():
    ip, op = sys.argv[1], sys.argv[2]
    t0 = time.time()
    inst = json.load(sys.stdin) if ip == "-" else json.load(open(ip, encoding="utf-8"))
    par = inst.get("params", {})
    tl, seed = float(par.get("time_limit_s", 10)), int(par.get("seed", 1))
    P = Prob(inst)
    routes, un = baselines.greedy(P)
    iters = 0
    done = False
    if len(P.task_ids) <= 9 and not un:
        tot, r = exact.solve_exact(P, time_limit=max(tl, 20))
        if r is not None:
            routes, done = r, True
    if not done:
        rng = random.Random(seed)
        cost = {v["id"]: P.route_cost(v, routes[v["id"]]) for v in P.veh}
        maxit = int(3000 * tl)
        vids = [v["id"] for v in P.veh]
        while iters < maxit and time.time() - t0 < 0.8 * tl and P.task_ids:
            iters += 1
            a = rng.choice(vids)
            if not routes[a]:
                continue
            i = rng.randrange(len(routes[a]))
            tid = routes[a][i]
            cands = [v for v in vids if P.compat(P.vmap[v], tid)]
            b = rng.choice(cands)
            sa = routes[a][:i] + routes[a][i + 1:]
            if a == b:
                j = rng.randrange(len(sa) + 1)
                sb = sa[:j] + [tid] + sa[j:]
                ca = P.route_cost(P.vmap[a], sb)
                if ca < cost[a] - 1e-12:
                    routes[a], cost[a] = sb, ca
            else:
                sb = routes[b]
                j = rng.randrange(len(sb) + 1)
                sb = sb[:j] + [tid] + sb[j:]
                ca, cb = P.route_cost(P.vmap[a], sa), P.route_cost(P.vmap[b], sb)
                if ca + cb < cost[a] + cost[b] - 1e-12:
                    routes[a], routes[b], cost[a], cost[b] = sa, sb, ca, cb
    out = make_output(P, routes, un, {"iterations": iters, "time_s": time.time() - t0,
                                      "best_found_at_s": time.time() - t0, "seed": seed})
    if op == "-":
        json.dump(out, sys.stdout, ensure_ascii=False)
    else:
        json.dump(out, open(op, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
