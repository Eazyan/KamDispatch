#!/usr/bin/env python3
"""Test & benchmark harness for the hgs solver CLI.  Usage: python3 run_tests.py [--hgs PATH] [--quick]"""
import argparse, copy, os, sys, time, platform
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen, baselines, exact
from common import Prob, run_solver, default_solver_path, load_json, dump_json
from validate import validate

DATA = os.path.join(HERE, "data")
GEN_VERSION = "v1"
fails, warns = [], []
md = []


def P(s=""):
    print(s, flush=True)
    md.append(s)


def table(header, rows):
    P("| " + " | ".join(header) + " |")
    P("|" + "|".join("---" for _ in header) + "|")
    for r in rows:
        P("| " + " | ".join(str(x) for x in r) + " |")
    P()


def get_inst(preset, seed, **kw):
    os.makedirs(DATA, exist_ok=True)
    key = "%s_%s_%s_%s" % (GEN_VERSION, preset, seed, "_".join("%s=%s" % kv for kv in sorted(kw.items())))
    path = os.path.join(DATA, key + ".json")
    if os.path.exists(path):
        return load_json(path)
    inst = gen.make(preset, seed, **kw)
    dump_json(inst, path)
    return inst


def with_params(inst, tl, seed=1, **extra):
    d = dict(inst)
    d["params"] = dict(inst.get("params", {}), time_limit_s=tl, seed=seed, **extra)
    return d


def solve(hgs, inst, tl, seed=1, **extra):
    """Run solver + validate. Returns dict(out, wall, err, val, total)."""
    inst = with_params(inst, tl, seed, **extra)
    out, wall, err = run_solver(hgs, inst, timeout=tl * 3 + 60)
    r = dict(out=out, wall=wall, err=err, val=None, total=None, inst=inst)
    if out is not None:
        try:
            r["val"] = validate(inst, out)
            r["total"] = r["val"]["recomputed"]["total"] if r["val"]["recomputed"] else None
        except Exception as e:  # noqa
            r["val"] = dict(ok=False, errors=["validator crashed on output: %r" % e], warnings=[])
    return r


def verdict(ok):
    return "PASS" if ok else "FAIL"


def status_of(r, tl):
    if r["err"]:
        return False, r["err"]
    if not r["val"]["ok"]:
        return False, "; ".join(r["val"]["errors"][:3])
    if r["wall"] > tl * 1.5 + 3:
        return False, "time limit exceeded: wall %.1fs for limit %ss" % (r["wall"], tl)
    return True, ""


def test_tiny(hgs, n):
    P("## (a) Tiny instances vs brute-force optimum\n")
    rows, gaps, ok_all = [], [], True
    for seed in range(1, n + 1):
        inst = get_inst("tiny", seed)
        pr = Prob(inst)
        t0 = time.time()
        opt, _ = exact.solve_exact(pr, time_limit=120)
        te = time.time() - t0
        r = solve(hgs, inst, 1, seed=seed)
        ok, why = status_of(r, 1)
        if opt is None:
            rows.append([seed, len(inst["tasks"]), len(inst["vehicles"]), "timeout", "-", "-", "SKIP"]); continue
        gap = None
        if ok:
            gap = (r["total"] - opt) / max(abs(opt), 1e-9)
            if gap < -1e-6:
                ok, why = False, "solver BETTER than brute-force optimum (%.6f < %.6f): evaluator/exact mismatch" % (r["total"], opt)
            elif gap > 1e-6:
                ok, why = False, "suboptimal gap %.4f%%" % (gap * 100)
            gaps.append(gap)
        ok_all &= ok
        if not ok:
            fails.append("tiny seed %d: %s" % (seed, why))
        rows.append([seed, len(inst["tasks"]), len(inst["vehicles"]), "%.4f" % opt,
                     "%.4f" % r["total"] if r["total"] is not None else "-",
                     "%.4f%%" % (gap * 100) if gap is not None else "-", "PASS" if ok else "FAIL: " + why])
    table(["seed", "tasks", "veh", "optimum", "solver", "gap", "result"], rows)
    if gaps:
        P("Mean gap %.5f%%, max gap %.5f%%, optimal on %d/%d.\n" % (100 * sum(gaps) / len(gaps), 100 * max(gaps),
                                                                  sum(1 for g in gaps if abs(g) <= 1e-6), len(gaps)))


def cases(quick):
    c = [("small30-plow", "small", 1, dict(mode="plow"), 2),
         ("small30-mixed+ready+dl", "small", 2, dict(mode="mixed", ready=True, deadlines=True), 2),
         ("m100-plow-rgg", "m100", 1, dict(mode="plow", graph="rgg"), 3),
         ("m100-mixed+ready+dl", "m100", 2, dict(mode="mixed", ready=True, deadlines=True), 3),
         ("m300-mixed+ready", "m300", 1, dict(mode="mixed", ready=True), 5)]
    if not quick:
        c += [("l500-plow", "l500", 1, dict(mode="plow"), 8),
              ("l1000-mixed+ready+dl", "l1000", 1, dict(mode="mixed", ready=True, deadlines=True), 10)]
    return c


def test_validity(hgs, quick):
    P("## (b) Validity and (c) improvement over baselines\n")
    rows = []
    for name, preset, seed, kw, tl in cases(quick):
        inst = get_inst(preset, seed, **kw)
        pr = Prob(inst)
        gr, _ = baselines.greedy(pr)
        g = pr.evaluate(gr)["total"]
        rr, _ = baselines.random_order(pr, seed)
        rnd = pr.evaluate(rr)["total"]
        r = solve(hgs, inst, tl, seed=seed)
        ok, why = status_of(r, tl)
        imp = imp_r = None
        if ok:
            imp = 100 * (g - r["total"]) / g if g else 0.0
            imp_r = 100 * (rnd - r["total"]) / rnd if rnd else 0.0
            if r["total"] > g * (1 + 1e-9) + 1e-9:
                ok, why = False, "worse than greedy baseline"
            for w in r["val"]["warnings"]:
                warns.append("%s: %s" % (name, w))
        if not ok:
            fails.append("validity %s: %s" % (name, why))
        rows.append([name, len(inst["tasks"]), len(inst["vehicles"]), tl, "%.1f" % r["wall"],
                     "%.1f" % r["total"] if r["total"] is not None else "-", "%.1f" % g, "%.1f" % rnd,
                     "%.2f%%" % imp if imp is not None else "-", "%.2f%%" % imp_r if imp_r is not None else "-",
                     "PASS" if ok else "FAIL: " + why])
    table(["case", "tasks", "veh", "tl(s)", "wall(s)", "solver", "greedy", "random", "vs greedy", "vs random", "result"], rows)


def canon(out):
    return dict(status=out["status"], unassigned=out["unassigned"], objective=out["objective"],
                routes=[(r["vehicle"], [(v["task"], v["arrive"], v["start"], v["end"]) for v in r["visits"]],
                         r["return_time"]) for r in out["routes"]])


def test_determinism(hgs):
    P("## (d) Determinism (same seed twice)\n")
    inst = get_inst("small", 2, mode="mixed", ready=True, deadlines=True)
    a = solve(hgs, inst, 3, seed=7, max_iter_no_improve=300)
    b = solve(hgs, inst, 3, seed=7, max_iter_no_improve=300)
    c = solve(hgs, inst, 3, seed=8, max_iter_no_improve=300)
    if a["err"] or b["err"]:
        fails.append("determinism: run failed: %s" % (a["err"] or b["err"]))
        P("FAIL: solver run failed\n"); return
    same = canon(a["out"]) == canon(b["out"])
    P("| run | total | result |\n|---|---|---|")
    P("| seed 7 #1 | %.4f | |\n| seed 7 #2 | %.4f | %s |" % (a["total"], b["total"], "IDENTICAL" if same else "DIFFERENT"))
    if c["total"] is not None:
        P("| seed 8 | %.4f | (info: different seed) |" % c["total"])
    P()
    if not same:
        fails.append("determinism: same seed produced different output (note: time-limit-bound termination may cause this)")
    P("Result: %s\n" % verdict(same))


def test_scaling(hgs, quick):
    P("## (e) Scaling: objective vs time limit\n")
    tls = [1, 3] if quick else [1, 5, 20]
    sizes = [("m300", 1)] if quick else [("m300", 1), ("l1000", 1)]
    rows = []
    for preset, seed in sizes:
        inst = get_inst(preset, seed, mode="mixed", ready=True)
        pr = Prob(inst)
        g = pr.evaluate(baselines.greedy(pr)[0])["total"]
        prev = None
        for tl in tls:
            r = solve(hgs, inst, tl, seed=seed)
            ok, why = status_of(r, tl)
            note = ""
            if ok:
                if prev is not None and r["total"] > prev * 1.005:
                    note = "non-monotone (+%.2f%% vs shorter limit)" % (100 * (r["total"] / prev - 1))
                    warns.append("scaling %s tl=%s: %s" % (preset, tl, note))
                prev = r["total"] if prev is None else min(prev, r["total"])
            else:
                fails.append("scaling %s tl=%s: %s" % (preset, tl, why))
            rows.append([preset, len(inst["tasks"]), tl, "%.1f" % r["wall"],
                         "%.1f" % r["total"] if r["total"] is not None else "-",
                         "%.2f%%" % (100 * (g - r["total"]) / g) if r["total"] is not None else "-",
                         "PASS" + (" (%s)" % note if note else "") if ok else "FAIL: " + why])
    table(["size", "tasks", "tl(s)", "wall(s)", "objective", "vs greedy", "result"], rows)


def test_infeasible(hgs):
    P("## (f) Infeasible case (tasks with no compatible class)\n")
    inst = copy.deepcopy(get_inst("small", 5, mode="mixed"))
    base = inst["tasks"][0]
    nid = max(t["id"] for t in inst["tasks"]) + 1
    bad = []
    for k, svc in enumerate(({"zamboni": 60.0}, {})):
        t = copy.deepcopy(base)
        t["id"] = nid + k
        t["service_time"] = svc
        inst["tasks"].append(t)
        bad.append(t["id"])
    r = solve(hgs, inst, 2)
    ok, why = status_of(r, 2)
    if ok:
        if sorted(r["out"]["unassigned"]) != sorted(bad):
            ok, why = False, "unassigned=%s expected %s" % (r["out"]["unassigned"], bad)
        elif r["out"]["status"] != "infeasible":
            ok, why = False, "status %r, expected 'infeasible'" % r["out"]["status"]
        else:
            served = sum(len(x["visits"]) for x in r["out"]["routes"])
            if served != len(inst["tasks"]) - 2:
                ok, why = False, "served %d of %d feasible tasks" % (served, len(inst["tasks"]) - 2)
    if not ok:
        fails.append("infeasible: %s" % why)
    P("Added tasks %s with no compatible class: unassigned=%s status=%s -> **%s** %s\n" % (
        bad, r["out"]["unassigned"] if r["out"] else "n/a", r["out"]["status"] if r["out"] else "n/a",
        verdict(ok), why))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hgs", default=default_solver_path(), help="solver binary (default core/build/hgs[.exe]); .py scripts allowed")
    ap.add_argument("--quick", action="store_true", help="smaller/faster run (skips 500/1000 validity, shorter scaling)")
    ap.add_argument("--only", default="abcdef", help="subset of tests to run, e.g. 'ad'")
    ap.add_argument("--tiny-count", type=int, default=30)
    ap.add_argument("--report", default=os.path.join(HERE, "report.md"))
    a = ap.parse_args()
    P("# hgs test & benchmark report\n")
    P("- date: %s, python %s, %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), platform.python_version(), platform.platform()))
    P("- solver: `%s`\n" % a.hgs)
    if not os.path.exists(a.hgs):
        P("**SKIPPED**: solver binary not found at `%s` (build core first, or pass --hgs)." % a.hgs)
        open(a.report, "w", encoding="utf-8").write("\n".join(md) + "\n")
        return 0
    only = a.only
    if "a" in only: test_tiny(a.hgs, 10 if a.quick and a.tiny_count == 30 else a.tiny_count)
    if "b" in only or "c" in only: test_validity(a.hgs, a.quick)
    if "d" in only: test_determinism(a.hgs)
    if "e" in only: test_scaling(a.hgs, a.quick)
    if "f" in only: test_infeasible(a.hgs)
    P("## Summary\n")
    if warns:
        P("Warnings:")
        for w in warns: P("- " + w)
        P()
    if fails:
        P("**%d FAILURE(S)**" % len(fails))
        for f in fails: P("- " + f)
    else:
        P("**ALL TESTS PASSED**")
    open(a.report, "w", encoding="utf-8").write("\n".join(md) + "\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
