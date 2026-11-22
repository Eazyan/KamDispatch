#include "instance.hpp"
#include "json.hpp"
#include <cmath>
#include <stdexcept>

using json::Value;

static void need(bool c, const std::string& m) {
    if (!c) throw std::runtime_error("invalid input: " + m);
}
static int nodeIdx(const Value& v, const char* key, int N, int def) {
    const Value* f = v.find(key);
    int r = def;
    if (f && f->isNumber()) r = (int)f->num;
    need(r >= 0 && r < N, std::string(key) + " out of range [0,n_nodes)");
    return r;
}

Instance loadInstance(const std::string& text) {
    Value root = json::parse(text);
    need(root.isObject(), "root must be an object");
    Instance in;
    const Value* cls = root.find("classes");
    need(cls && cls->isObject(), "missing 'classes'");
    in.N = (int)root.getNum("n_nodes", -1);
    // infer N from first matrix if needed
    for (size_t c = 0; c < cls->keys.size(); ++c) {
        const Value* m = cls->vals[c].find("matrix");
        need(m && m->isArray(), "class '" + cls->keys[c] + "' has no matrix");
        if (in.N < 0) in.N = (int)m->size();
    }
    need(in.N >= 0, "n_nodes unknown");
    const int N = in.N;
    for (size_t c = 0; c < cls->keys.size(); ++c) {
        in.classNames.push_back(cls->keys[c]);
        const Value* m = cls->vals[c].find("matrix");
        need((int)m->size() == N, "matrix of '" + cls->keys[c] + "' is not N x N");
        std::vector<double> flat((size_t)N * N);
        for (int i = 0; i < N; ++i) {
            const Value& row = m->at((size_t)i);
            need(row.isArray() && (int)row.size() == N, "matrix row has wrong length");
            for (int j = 0; j < N; ++j) {
                double d = row.numAt((size_t)j);
                flat[(size_t)i * N + j] = (d < 0 || !std::isfinite(d)) ? kUnreachable : d;
            }
        }
        in.mat.push_back(std::move(flat));
    }
    const int C = (int)in.classNames.size();
    auto classIndex = [&](const std::string& n) {
        for (int c = 0; c < C; ++c) if (in.classNames[c] == n) return c;
        return -1;
    };

    const Value* tasks = root.find("tasks");
    need(tasks && tasks->isArray(), "missing 'tasks'");
    for (size_t i = 0; i < tasks->size(); ++i) {
        const Value& tv = tasks->at(i);
        need(tv.isObject(), "task must be an object");
        Task t;
        t.id = (long long)tv.getNum("id", (double)i);
        t.sn = nodeIdx(tv, "start_node", N, -1);
        t.en = nodeIdx(tv, "end_node", N, t.sn);
        t.svc.assign(C, -1.0);
        const Value* st = tv.find("service_time");
        if (st && st->isObject())
            for (size_t k = 0; k < st->keys.size(); ++k) {
                int c = classIndex(st->keys[k]);
                if (c >= 0 && st->vals[k].isNumber() && st->vals[k].num >= 0) t.svc[c] = st->vals[k].num;
            }
        t.weight = tv.getNum("weight", 1.0);
        t.ready = tv.getNum("ready_time", 0.0);
        const Value* dl = tv.find("deadline");
        if (dl && dl->isNumber()) { t.hasDeadline = true; t.deadline = dl->num; }
        t.dw = tv.getNum("deadline_weight", 0.0);
        in.tasks.push_back(std::move(t));
    }
    const Value* veh = root.find("vehicles");
    need(veh && veh->isArray(), "missing 'vehicles'");
    for (size_t i = 0; i < veh->size(); ++i) {
        const Value& vv = veh->at(i);
        need(vv.isObject(), "vehicle must be an object");
        Vehicle v;
        const Value* id = vv.find("id");
        if (id && id->isString()) v.id = id->str;
        else if (id && id->isNumber()) { char b[40]; std::snprintf(b, sizeof b, "%.0f", id->num); v.id = b; }
        else v.id = std::to_string(i);
        v.cls = classIndex(vv.getStr("class", ""));
        v.sn = nodeIdx(vv, "start_node", N, -1);
        v.en = nodeIdx(vv, "end_node", N, v.sn);
        v.ss = vv.getNum("shift_start", 0.0);
        v.se = vv.getNum("shift_end", 1e18);
        in.vehicles.push_back(std::move(v));
    }
    if (const Value* o = root.find("objective")) {
        in.obj.wLatency = o->getNum("w_latency", in.obj.wLatency);
        in.obj.wDeadhead = o->getNum("w_deadhead", in.obj.wDeadhead);
        in.obj.wWait = o->getNum("w_wait", in.obj.wWait);
        in.obj.wOvertime = o->getNum("w_overtime", in.obj.wOvertime);
        in.obj.wMakespan = o->getNum("w_makespan", in.obj.wMakespan);
    }
    if (const Value* p = root.find("params")) {
        Params& q = in.par;
        q.timeLimit = p->getNum("time_limit_s", q.timeLimit);
        q.seed = (long long)p->getNum("seed", (double)q.seed);
        q.popMin = (int)p->getNum("pop_min", q.popMin);
        q.popGen = (int)p->getNum("pop_gen", q.popGen);
        q.maxIterNoImprove = (int)p->getNum("max_iter_no_improve", q.maxIterNoImprove);
        q.stopOnStagnation = p->find("max_iter_no_improve") != nullptr;
        q.granularK = (int)p->getNum("granular_k", q.granularK);
        q.nbElite = (int)p->getNum("nb_elite", q.nbElite);
        q.nbClose = (int)p->getNum("nb_close", q.nbClose);
        q.popInit = (int)p->getNum("pop_init", q.popInit);
        q.maxIter = (long long)p->getNum("max_iter", (double)q.maxIter);
        q.verbose = p->getBool("verbose", false);
        if (q.popMin < 2) q.popMin = 2;
        if (q.popGen < 1) q.popGen = 1;
        if (q.granularK < 1) q.granularK = 1;
    }
    return in;
}
