#!/usr/bin/env python3
"""Independent validator: validate.py input.json output.json"""
import math, sys
from common import Prob, Unreachable, load_json

REL = 1e-6


def close(a, b, rel=REL, abs_=1e-6):
    return abs(a - b) <= abs_ + rel * max(abs(a), abs(b))


def tclose(a, b):  # time values: allow tiny rounding in solver's printed times
    return abs(a - b) <= 1e-3 + 1e-6 * max(abs(a), abs(b))


def validate(inst, out):
    """Returns dict(ok, errors, warnings, recomputed(components), total)."""
    P = Prob(inst)
    errs, warns = [], []
    if out.get("status") not in ("ok", "infeasible"):
        errs.append("bad status %r" % out.get("status"))
    routes = {}
    served = {}
    seen_v = set()
    for r in out.get("routes", []):
        vid = r.get("vehicle")
        if vid not in P.vmap:
            errs.append("unknown vehicle %r" % vid); continue
        if vid in seen_v:
            errs.append("vehicle %r listed twice" % vid); continue
        seen_v.add(vid)
        v = P.vmap[vid]
        seq = []
        for vis in r.get("visits", []):
            tid = vis.get("task")
            if tid not in P.tasks:
                errs.append("unknown task %r" % tid); continue
            if tid in served:
                errs.append("task %r served twice (%s and %s)" % (tid, served[tid], vid))
            served[tid] = vid
            if not P.compat(v, tid):
                errs.append("task %r incompatible with vehicle %s (class %s)" % (tid, vid, v["class"]))
            seq.append(tid)
        routes[vid] = seq
    for v in P.veh:
        routes.setdefault(v["id"], [])
    un = out.get("unassigned", [])
    if len(set(un)) != len(un):
        errs.append("duplicate ids in unassigned")
    unset = set(un)
    expected_un = {t for t in P.task_ids if not P.servable(t)}
    for t in P.task_ids:
        if t not in served and t not in unset:
            errs.append("task %r neither served nor unassigned" % t)
        if t in served and t in unset:
            errs.append("task %r both served and unassigned" % t)
    for t in unset - expected_un:
        if t not in P.tasks:
            errs.append("unassigned unknown task %r" % t)
        elif t not in served:
            errs.append("task %r reported unassigned but a compatible vehicle exists" % t)
    for t in expected_un - unset:
        if t not in served:
            pass  # already reported as neither served nor unassigned
    if (out.get("status") == "ok") != (len(un) == 0):
        warns.append("status %r inconsistent with unassigned count %d" % (out.get("status"), len(un)))
    # timing
    rec = None
    if not errs or all("incompatible" not in e for e in errs):
        try:
            for r in out.get("routes", []):
                vid = r["vehicle"]
                v = P.vmap.get(vid)
                if v is None:
                    continue
                M = P.M[v["class"]] if v["class"] in P.M else None
                node, t = v["start_node"], float(v["shift_start"])
                dh = wait = 0.0
                for vis in r.get("visits", []):
                    tid = vis["task"]
                    if tid not in P.tasks or M is None or v["class"] not in P.svc[tid]:
                        continue
                    tk = P.tasks[tid]
                    d = M[node][tk["start_node"]]
                    if d < 0:
                        errs.append("vehicle %s traverses unreachable pair %s->%s" % (vid, node, tk["start_node"]))
                        break
                    arr = t + d
                    st = max(arr, P.ready[tid])
                    end = st + P.svc[tid][v["class"]]
                    for nm, got, exp in (("arrive", vis["arrive"], arr), ("start", vis["start"], st), ("end", vis["end"], end)):
                        if not tclose(got, exp):
                            errs.append("vehicle %s task %s %s=%.6f expected %.6f" % (vid, tid, nm, got, exp))
                    dh += d
                    wait += st - arr
                    node, t = tk["end_node"], end
                    # continue from solver values? no: use recomputed (authoritative)
                if r.get("visits") and M is not None:
                    d = M[node][v["end_node"]]
                    if d < 0:
                        errs.append("vehicle %s cannot reach end node" % vid)
                    else:
                        dh += d
                        if not tclose(r.get("return_time", float("nan")), t + d):
                            errs.append("vehicle %s return_time=%s expected %.6f" % (vid, r.get("return_time"), t + d))
                if "deadhead" in r and not tclose(r["deadhead"], dh):
                    errs.append("vehicle %s deadhead=%s expected %.6f" % (vid, r["deadhead"], dh))
                if "wait" in r and not tclose(r["wait"], wait):
                    errs.append("vehicle %s wait=%s expected %.6f" % (vid, r["wait"], wait))
        except (KeyError, TypeError) as e:
            errs.append("malformed route entry: %r" % e)
    # objective recompute
    if not errs:
        try:
            rec = P.evaluate(routes)
        except Unreachable as e:
            errs.append(str(e))
    if rec is not None:
        o = out.get("objective", {})
        for k in ("latency", "deadhead", "wait", "overtime", "makespan"):
            if k not in o:
                errs.append("objective.%s missing" % k)
            elif not close(o[k], rec[k]):
                errs.append("objective.%s=%.9g recomputed %.9g" % (k, o[k], rec[k]))
        if "lateness" in o and not close(o["lateness"], rec["lateness"]):
            errs.append("objective.lateness=%.9g recomputed %.9g" % (o["lateness"], rec["lateness"]))
        if "total" not in o:
            errs.append("objective.total missing")
        else:
            full = rec["total"]
            nolate = P.total_of(rec, with_lateness=False)
            if not close(o["total"], full):
                if close(o["total"], nolate):
                    warns.append("total excludes deadline lateness (%.6g) -- recomputed with lateness %.6g" % (nolate, full))
                else:
                    errs.append("objective.total=%.9g recomputed %.9g" % (o["total"], full))
    return {"ok": not errs, "errors": errs, "warnings": warns,
            "recomputed": rec, "total": rec["total"] if rec else None}


def main():
    inst, out = load_json(sys.argv[1]), load_json(sys.argv[2])
    r = validate(inst, out)
    for e in r["errors"][:50]:
        print("ERROR:", e)
    for w in r["warnings"]:
        print("WARN:", w)
    print("VALID" if r["ok"] else "INVALID", "total=%s" % r["total"])
    sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
