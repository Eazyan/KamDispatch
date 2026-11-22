"""Shared problem model + objective evaluator (the single source of truth for the harness)."""
import json, math, os, subprocess, sys, tempfile, time


class Unreachable(Exception):
    pass


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


class Prob:
    def __init__(self, inst):
        self.inst = inst
        self.tasks = {t["id"]: t for t in inst["tasks"]}
        self.task_ids = [t["id"] for t in inst["tasks"]]
        self.veh = inst["vehicles"]
        self.vmap = {v["id"]: v for v in self.veh}
        self.M = {c: d["matrix"] for c, d in inst["classes"].items()}
        o = inst.get("objective", {})
        self.w_lat = o.get("w_latency", 1.0)
        self.w_dh = o.get("w_deadhead", 1.0)
        self.w_wait = o.get("w_wait", 0.5)
        self.w_ot = o.get("w_overtime", 1000.0)
        self.w_ms = o.get("w_makespan", 0.0)
        self.svc = {t["id"]: t.get("service_time") or {} for t in inst["tasks"]}
        self.ready = {t["id"]: float(t.get("ready_time") or 0.0) for t in inst["tasks"]}
        self.deadline = {t["id"]: t.get("deadline") for t in inst["tasks"]}
        self.dw = {t["id"]: float(t.get("deadline_weight") or 0.0) for t in inst["tasks"]}

    def compat(self, v, tid):
        return v["class"] in self.svc[tid] and v["class"] in self.M

    def servable(self, tid):
        return any(self.compat(v, tid) for v in self.veh)

    def sim(self, v, seq):
        """Simulate one vehicle route. Returns dict; raises Unreachable on negative travel."""
        M = self.M[v["class"]]
        cls = v["class"]
        node = v["start_node"]
        t = float(v["shift_start"])
        dh = wait = lat = late = 0.0
        visits = []
        for tid in seq:
            tk = self.tasks[tid]
            d = M[node][tk["start_node"]]
            if d < 0:
                raise Unreachable("unreachable %s->%s" % (node, tk["start_node"]))
            arr = t + d
            st = max(arr, self.ready[tid])
            end = st + self.svc[tid][cls]
            visits.append((tid, arr, st, end))
            dh += d
            wait += st - arr
            lat += tk["weight"] * end
            dl = self.deadline[tid]
            if dl is not None:
                late += self.dw[tid] * max(0.0, end - dl)
            node = tk["end_node"]
            t = end
        if seq:
            d = M[node][v["end_node"]]
            if d < 0:
                raise Unreachable("unreachable to end node")
            dh += d
            ret = t + d
            ot = max(0.0, ret - v["shift_end"])
        else:
            ret = float(v["shift_start"])
            ot = 0.0
        return dict(visits=visits, ret=ret, deadhead=dh, wait=wait, latency=lat,
                    lateness=late, overtime=ot, used=bool(seq))

    def route_cost(self, v, seq):
        """Weighted cost of one route excluding the makespan term (inf if unreachable)."""
        try:
            s = self.sim(v, seq)
        except Unreachable:
            return math.inf
        return (self.w_lat * s["latency"] + self.w_dh * s["deadhead"] + self.w_wait * s["wait"]
                + self.w_ot * s["overtime"] + s["lateness"])

    def evaluate(self, routes):
        """routes: dict vehicle_id -> list of task ids. Returns component dict incl. 'total'."""
        c = dict(latency=0.0, deadhead=0.0, wait=0.0, overtime=0.0, makespan=0.0, lateness=0.0)
        sims = {}
        for vid, seq in routes.items():
            s = self.sim(self.vmap[vid], seq)
            sims[vid] = s
            c["latency"] += s["latency"]
            c["deadhead"] += s["deadhead"]
            c["wait"] += s["wait"]
            c["overtime"] += s["overtime"]
            c["lateness"] += s["lateness"]
            if s["used"]:
                c["makespan"] = max(c["makespan"], s["ret"])
        c["total"] = self.total_of(c)
        c["sims"] = sims
        return c

    def total_of(self, c, with_lateness=True):
        t = (self.w_lat * c["latency"] + self.w_dh * c["deadhead"] + self.w_wait * c["wait"]
             + self.w_ot * c["overtime"] + self.w_ms * c["makespan"])
        return t + (c["lateness"] if with_lateness else 0.0)

    def has_deadline_penalty(self):
        return any(self.deadline[t] is not None and self.dw[t] > 0 for t in self.task_ids)


def make_output(prob, routes, unassigned, stats=None):
    ev = prob.evaluate(routes)
    out_routes = []
    for v in prob.veh:
        seq = routes.get(v["id"], [])
        s = ev["sims"][v["id"]]
        out_routes.append({"vehicle": v["id"],
                           "visits": [{"task": a, "arrive": b, "start": c, "end": d} for a, b, c, d in s["visits"]],
                           "return_time": s["ret"], "deadhead": s["deadhead"], "wait": s["wait"]})
    obj = {k: ev[k] for k in ("total", "latency", "deadhead", "wait", "overtime", "makespan")}
    if prob.has_deadline_penalty():
        obj["lateness"] = ev["lateness"]
    return {"status": "infeasible" if unassigned else "ok", "routes": out_routes,
            "unassigned": sorted(unassigned), "objective": obj, "stats": stats or {}}


def default_solver_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    name = "hgs.exe" if os.name == "nt" else "hgs"
    return os.path.join(root, "core", "build", name)


def solver_cmd(path):
    if path.endswith(".py"):
        return [sys.executable, path]
    return [path]


def run_solver(path, inst, timeout=600, workdir=None):
    """Run solver on instance dict. Returns (output_dict_or_None, wall_seconds, error_string_or_None)."""
    with tempfile.TemporaryDirectory(dir=workdir) as d:
        ip, op = os.path.join(d, "in.json"), os.path.join(d, "out.json")
        dump_json(inst, ip)
        t0 = time.time()
        try:
            r = subprocess.run(solver_cmd(path) + [ip, op], capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, time.time() - t0, "timeout after %ss" % timeout
        wall = time.time() - t0
        if r.returncode != 0:
            return None, wall, "exit code %d: %s" % (r.returncode, r.stderr.decode("utf-8", "replace")[-300:])
        try:
            return load_json(op), wall, None
        except Exception as e:  # noqa
            return None, wall, "cannot read output: %s" % e


def routes_from_output(out):
    return {r["vehicle"]: [v["task"] for v in r["visits"]] for r in out.get("routes", [])}
