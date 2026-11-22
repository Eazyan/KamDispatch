// Hybrid Genetic Search for heterogeneous-fleet open routing over tasks.
#include "hgs.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <numeric>

namespace hgs {
namespace {

typedef std::chrono::steady_clock Clock;
const double INF = 1e300;
const double EPS = 1e-6;

struct Rng {
    uint64_t s;
    explicit Rng(uint64_t seed) : s(seed * 0x9E3779B97F4A7C15ULL + 0x2545F4914F6CDD1DULL) {}
    uint64_t next() {
        uint64_t z = (s += 0x9E3779B97F4A7C15ULL);
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
        z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
        return z ^ (z >> 31);
    }
    int range(int n) { return (int)(next() % (uint64_t)n); }
    double uni() { return (double)(next() >> 11) * (1.0 / 9007199254740992.0); }
    template <class It> void shuffle(It b, It e) {
        for (long long i = (long long)(e - b) - 1; i > 0; --i) std::swap(b[i], b[range((int)i + 1)]);
    }
};

struct Ev { double cost, ret; };

// Per-position data of a route. Node: what an exact re-walk of the suffix needs (task constants copied for
// sequential access). Head: Phi_i(arr) = cost of tasks i.. (excluding arc into i, return leg and overtime) as a
// piecewise-linear function of the arrival time at task i: sc = Phi_i(arr), gR/gL = right/left slope, uR/dL = how far
// arr may increase/decrease with the slope still exact, sR/sL = slope of the end-of-last-task time wrt arr.
struct Node { double arr, e, arcc, r, lw, dl, dw, s; };
struct Head { double sc, gR, gL, uR, dL, sR, sL; };

struct Route {
    std::vector<int> seq;
    std::vector<double> tm, pc; // time / partial weighted cost after i tasks (size len+1)
    std::vector<Node> nd;
    std::vector<Head> hd;
    std::vector<int> lastBad;   // per class: last position of a task the class cannot serve (-1: none)
    double cost = 0, ret = -1;  // ret < 0 => empty route
};

struct Model {
    const Instance& in;
    int T, V, N, C;
    double wl, wd, ww, wo, wm;
    std::vector<int> tsn, ten;
    std::vector<double> tw, tr, tdl, tdw;
    std::vector<int> vcls, vsn, ven;
    std::vector<double> vss, vse;
    std::vector<double> svcV; // V*T service time or -1
    std::vector<int> assigned, unassignedT;
    std::vector<std::vector<int>> ctask;       // usable classes per task
    std::vector<std::vector<int>> vehOfClass;  // vehicles per class (ascending)
    std::vector<int> vorder;                   // vehicles ordered by class then index
    std::vector<int> twin;                     // identical-vehicle group id
    std::vector<int> sgrp;                     // suffix-compatibility group (= class)
    double wm_dummy = 0;
    std::vector<std::vector<int>> nbr;         // granular neighbours (task ids)

    explicit Model(const Instance& i) : in(i) {
        T = (int)in.tasks.size(); V = (int)in.vehicles.size(); N = in.N; C = (int)in.classNames.size();
        wl = in.obj.wLatency; wd = in.obj.wDeadhead; ww = in.obj.wWait; wo = in.obj.wOvertime; wm = in.obj.wMakespan;
        tsn.resize(T); ten.resize(T); tw.resize(T); tr.resize(T); tdl.resize(T); tdw.resize(T);
        for (int t = 0; t < T; ++t) {
            const Task& k = in.tasks[t];
            tsn[t] = k.sn; ten[t] = k.en; tw[t] = k.weight; tr[t] = k.ready;
            tdl[t] = k.hasDeadline ? k.deadline : INF; tdw[t] = k.dw;
        }
        vcls.resize(V); vsn.resize(V); ven.resize(V); vss.resize(V); vse.resize(V);
        vehOfClass.assign(C, {});
        svcV.assign((size_t)V * T, -1.0);
        for (int v = 0; v < V; ++v) {
            const Vehicle& h = in.vehicles[v];
            vcls[v] = h.cls; vsn[v] = h.sn; ven[v] = h.en; vss[v] = h.ss; vse[v] = h.se;
            if (h.cls >= 0) {
                vehOfClass[h.cls].push_back(v);
                for (int t = 0; t < T; ++t) svcV[(size_t)v * T + t] = in.tasks[t].svc[h.cls];
            }
        }
        for (int c = 0; c < C; ++c) for (int v : vehOfClass[c]) vorder.push_back(v);
        twin.assign(V, -1);
        int g = 0;
        for (int a = 0; a < V; ++a) {
            if (twin[a] >= 0) continue;
            twin[a] = g;
            for (int b = a + 1; b < V; ++b)
                if (twin[b] < 0 && vcls[a] == vcls[b] && vsn[a] == vsn[b] && ven[a] == ven[b] && vss[a] == vss[b] && vse[a] == vse[b])
                    twin[b] = g;
            ++g;
        }
        sgrp.assign(V, -1);
        {
            int gg = 0;
            for (int a = 0; a < V; ++a) {
                if (sgrp[a] >= 0) continue;
                sgrp[a] = gg;
                for (int b = a + 1; b < V; ++b)
                    if (sgrp[b] < 0 && vcls[a] == vcls[b]) sgrp[b] = gg;
                ++gg;
            }
        }
        ctask.assign(T, {});
        for (int t = 0; t < T; ++t) {
            for (int c = 0; c < C; ++c)
                if (!vehOfClass[c].empty() && in.tasks[t].svc[c] >= 0) ctask[t].push_back(c);
            (ctask[t].empty() ? unassignedT : assigned).push_back(t);
        }
        buildNeighbours();
    }

    inline bool ok(int v, int t) const { return svcV[(size_t)v * T + t] >= 0; }

    Ev simulate(int v, const int* seq, int len, int k, const Route& cur) const {
        if (len == 0) return Ev{0.0, -1.0};
        const double* Dm = in.mat[vcls[v]].data();
        const double* sv = &svcV[(size_t)v * T];
        double t, c; int pos;
        if (k == 0) { t = vss[v]; c = 0; pos = vsn[v]; }
        else { t = cur.tm[k]; c = cur.pc[k]; pos = ten[seq[k - 1]]; }
        for (int i = k; i < len; ++i) {
            int x = seq[i];
            double arr = t + Dm[(size_t)pos * N + tsn[x]];
            double st = arr < tr[x] ? tr[x] : arr;
            double e = st + sv[x];
            c += wd * (arr - t) + ww * (st - arr) + wl * tw[x] * e;
            if (e > tdl[x]) c += tdw[x] * (e - tdl[x]);
            t = e; pos = ten[x];
        }
        double leg = Dm[(size_t)pos * N + ven[v]];
        t += leg; c += wd * leg;
        double ot = t - vse[v];
        if (ot > 0) c += wo * ot;
        return Ev{c, t};
    }
    void fill(int v, Route& r) const {
        int len = (int)r.seq.size();
        r.tm.assign(len + 1, 0.0); r.pc.assign(len + 1, 0.0);
        if (len == 0) { r.cost = 0; r.ret = -1; r.nd.clear(); r.hd.clear(); return; }
        r.nd.resize(len); r.hd.resize(len);
        const double* Dm = in.mat[vcls[v]].data();
        const double* sv = &svcV[(size_t)v * T];
        double t = vss[v], c = 0; int pos = vsn[v];
        r.tm[0] = t;
        for (int i = 0; i < len; ++i) {
            int x = r.seq[i];
            double arr = t + Dm[(size_t)pos * N + tsn[x]];
            double st = arr < tr[x] ? tr[x] : arr;
            double e = st + sv[x];
            Node& nd = r.nd[i];
            nd.arr = arr; nd.e = e; nd.arcc = wd * (arr - t); nd.r = tr[x]; nd.lw = wl * tw[x]; nd.dl = tdl[x]; nd.dw = tdw[x]; nd.s = sv[x];
            c += wd * (arr - t) + ww * (st - arr) + wl * tw[x] * e;
            if (e > tdl[x]) c += tdw[x] * (e - tdl[x]);
            t = e; pos = ten[x];
            r.tm[i + 1] = t; r.pc[i + 1] = c;
        }
        double leg = Dm[(size_t)pos * N + ven[v]];
        double tr_ = t + leg, cc = c + wd * leg;
        double ot = tr_ - vse[v];
        if (ot > 0) cc += wo * ot;
        r.cost = cc; r.ret = tr_;
        // backward pass: suffix slopes and linearity ranges
        const double BIG = 1e300;
        double gR = 0.0, uR = BIG, gL = 0.0, dL = BIG, sR = 1.0, sL = 1.0;
        for (int i = len - 1; i >= 0; --i) {
            const Node& nd = r.nd[i];
            Head& h = r.hd[i];
            double arr = nd.arr, e = nd.e;
            if (arr < nd.r) { gR = -ww; uR = nd.r - arr; sR = 0.0; }
            else {
                double dlk = e < nd.dl ? nd.dl - e : BIG;
                gR = nd.lw + (e >= nd.dl ? nd.dw : 0.0) + gR;
                uR = std::min(dlk, uR);
            }
            if (arr <= nd.r) { gL = -ww; dL = BIG; sL = 0.0; }
            else {
                double dk = e > nd.dl ? e - nd.dl : BIG;
                dL = std::min(std::min(arr - nd.r, dk), dL);
                gL = nd.lw + (e > nd.dl ? nd.dw : 0.0) + gL;
            }
            h.gR = gR; h.uR = uR; h.gL = gL; h.dL = dL; h.sR = sR; h.sL = sL;
            h.sc = r.pc[len] - r.pc[i] - nd.arcc;
        }
    }

    double corr(int u, int v) const { // task proximity (smaller = closer)
        double best = kUnreachable * 2;
        for (int c = 0; c < C; ++c) {
            if (vehOfClass[c].empty() || in.tasks[u].svc[c] < 0 || in.tasks[v].svc[c] < 0) continue;
            const double* Dm = in.mat[c].data();
            double a = Dm[(size_t)ten[u] * N + tsn[v]], b = Dm[(size_t)ten[v] * N + tsn[u]];
            best = std::min(best, std::min(a, b));
        }
        return best;
    }
    void buildNeighbours() {
        nbr.assign(T, {});
        int M = (int)assigned.size();
        int K = std::min(in.par.granularK, M - 1);
        if (K <= 0) return;
        std::vector<std::pair<double, int>> tmp;
        for (int u : assigned) {
            tmp.clear();
            for (int v : assigned) if (v != u) tmp.push_back(std::make_pair(corr(u, v), v));
            std::partial_sort(tmp.begin(), tmp.begin() + K, tmp.end());
            for (int i = 0; i < K; ++i) nbr[u].push_back(tmp[i].second);
        }
    }

    // ---------------- Split ----------------
    void split(const std::vector<int>& tour, std::vector<std::vector<int>>& out) const {
        out.assign(V, {});
        int M = (int)tour.size();
        if (M == 0) return;
        std::vector<int> cl(M, -1);
        auto has = [&](const std::vector<int>& cs, int c) { return std::find(cs.begin(), cs.end(), c) != cs.end(); };
        int cur = -1;
        for (int p = 0; p < M; ++p) {
            const std::vector<int>& cs = ctask[tour[p]];
            if (cs.size() == 1) { cl[p] = cs[0]; cur = cs[0]; }
            else if (cur >= 0 && has(cs, cur)) cl[p] = cur;
        }
        cur = -1;
        for (int p = M - 1; p >= 0; --p) {
            const std::vector<int>& cs = ctask[tour[p]];
            if (cl[p] >= 0) { cur = cl[p]; continue; }
            if (cur >= 0 && has(cs, cur)) cl[p] = cur;
        }
        for (int p = 0; p < M; ++p)
            if (cl[p] < 0) {
                const std::vector<int>& cs = ctask[tour[p]];
                int i = (int)(((double)p + 0.5) / M * cs.size());
                cl[p] = cs[std::min(i, (int)cs.size() - 1)];
            }
        std::vector<int> sub;
        std::vector<double> f, g;
        std::vector<int> from;
        for (int c = 0; c < C; ++c) {
            const std::vector<int>& vs = vehOfClass[c];
            if (vs.empty()) continue;
            sub.clear();
            for (int p = 0; p < M; ++p) if (cl[p] == c) sub.push_back(tour[p]);
            int m = (int)sub.size();
            if (m == 0) continue;
            int nv = (int)vs.size();
            int L = std::min(m, std::max(40, 3 * ((m + nv - 1) / nv)));
            std::vector<double> F((size_t)(nv + 1) * (m + 1), INF);
            std::vector<int> FR((size_t)(nv + 1) * (m + 1), -1);
            auto at = [&](int j, int i) { return (size_t)j * (m + 1) + i; };
            F[at(0, 0)] = 0;
            for (int j = 1; j <= nv; ++j) {
                int v = vs[j - 1];
                const double* Dm = in.mat[c].data();
                const double* sv = &svcV[(size_t)v * T];
                for (int i = 0; i <= m; ++i) F[at(j, i)] = F[at(j - 1, i)];
                for (int k = 0; k < m; ++k) {
                    double base = F[at(j - 1, k)];
                    if (base >= INF) continue;
                    double t = vss[v], cc = 0; int pos = vsn[v];
                    int lim = std::min(m, k + L);
                    for (int i = k; i < lim; ++i) {
                        int x = sub[i];
                        double arr = t + Dm[(size_t)pos * N + tsn[x]];
                        double st = arr < tr[x] ? tr[x] : arr;
                        double e = st + sv[x];
                        cc += wd * (arr - t) + ww * (st - arr) + wl * tw[x] * e;
                        if (e > tdl[x]) cc += tdw[x] * (e - tdl[x]);
                        t = e; pos = ten[x];
                        double leg = Dm[(size_t)pos * N + ven[v]];
                        double tot = cc + wd * leg;
                        double ot = t + leg - vse[v];
                        if (ot > 0) tot += wo * ot;
                        double cand = base + tot;
                        size_t id = at(j, i + 1);
                        if (cand < F[id]) { F[id] = cand; FR[id] = k; }
                    }
                }
            }
            int i = m, j = nv;
            while (i > 0 && j > 0) {
                int k = FR[at(j, i)];
                if (k < 0) { --j; continue; }
                out[vs[j - 1]].assign(sub.begin() + k, sub.begin() + i);
                i = k; --j;
            }
        }
    }

    double totalCost(const std::vector<Route>& R) const {
        double s = 0, mx = 0;
        for (const Route& r : R) { s += r.cost; if (r.ret > mx) mx = r.ret; }
        return s + wm * mx;
    }
};
// ---------------- Local search ----------------
// A candidate route is described without materialising it:
//   own-route prefix [0,p) + up to 3 chunks of tasks + suffix R[s].seq[q..).
struct Chunk { const int* p; int n; int step; };
struct Spec {
    int v, p;
    Chunk ch[3];
    int nch;
    int s, q; // suffix source route (-1: none) and first suffix position
};

struct LS {
    const Model& m;
    Rng& rng;
    Clock::time_point deadline;
    std::vector<Route> R;
    std::vector<int> rid, idx;
    double curMax = 0;
    std::vector<int> t1, t2, order, stamp;
    int stampCtr = 0;
    long long ctr = 0;
    std::vector<long long> modAt, lastTest;
    bool timeUp = false;
    size_t kLimit = 1000000;
    bool useIntraAll = false;
    int remU = -1; long long remMod = -1; bool remOk[3] = {false, false, false}; Ev remEv[3];
    std::vector<long long> lastIntra;

    LS(const Model& mm, Rng& r) : m(mm), rng(r) {
        rid.assign(m.T, -1); idx.assign(m.T, 0); stamp.assign(m.V, 0); lastIntra.assign(m.T, -1); lastTest.assign(m.T, -1); modAt.assign(m.V, 0);
        R.resize(m.V);
    }

    void recomputeMax() {
        curMax = 0;
        for (const Route& r : R) if (r.ret > curMax) curMax = r.ret;
    }
    double maxOthers(int a, int b) const {
        double mx = 0;
        for (int i = 0; i < (int)R.size(); ++i) if (i != a && i != b && R[i].ret > mx) mx = R[i].ret;
        return mx;
    }
    void setRoute(int v, const std::vector<int>& s) {
        R[v].seq = s;
        modAt[v] = ++ctr;
        m.fill(v, R[v]);
        R[v].lastBad.assign(m.C, -1);
        for (int i = 0; i < (int)s.size(); ++i)
            for (int c = 0; c < m.C; ++c) if (m.in.tasks[s[i]].svc[c] < 0) R[v].lastBad[c] = i;
        for (int i = 0; i < (int)s.size(); ++i) { rid[s[i]] = v; idx[s[i]] = i; }
    }
    void load(const std::vector<std::vector<int>>& routes, Clock::time_point dl) {
        remU = -1; deadline = dl; timeUp = false; ctr = 0; stampCtr = 0;
        std::fill(lastTest.begin(), lastTest.end(), -1LL);
        std::fill(lastIntra.begin(), lastIntra.end(), -1LL);
        std::fill(modAt.begin(), modAt.end(), 0LL);
        std::fill(stamp.begin(), stamp.end(), 0);
        for (int v = 0; v < m.V; ++v) setRoute(v, routes[v]);
        recomputeMax();
    }
    Ev sim(int v, const std::vector<int>& s, int k) const { return m.simulate(v, s.data(), (int)s.size(), k, R[v]); }

    // ---- fast move evaluation ----
    void materialize(const Spec& sp, std::vector<int>& out) const {
        const std::vector<int>& P = R[sp.v].seq;
        out.assign(P.begin(), P.begin() + sp.p);
        for (int c = 0; c < sp.nch; ++c) {
            const Chunk& ch = sp.ch[c];
            for (int k = 0; k < ch.n; ++k) out.push_back(ch.p[(long)k * ch.step]);
        }
        if (sp.s >= 0) { const std::vector<int>& S = R[sp.s].seq; out.insert(out.end(), S.begin() + sp.q, S.end()); }
    }

    Ev evalSpec(const Spec& sp) const {
        const int v = sp.v;
        const Route& P = R[v];
        const int N = m.N;
        const double* Dm = m.in.mat[m.vcls[v]].data();
        const double* sv = &m.svcV[(size_t)v * m.T];
        const double wd = m.wd, ww = m.ww, wl = m.wl;
        int sufLen = 0;
        if (sp.s >= 0) sufLen = (int)R[sp.s].seq.size() - sp.q;
        int tot = sp.p + sufLen;
        for (int c = 0; c < sp.nch; ++c) tot += sp.ch[c].n;
        if (tot == 0) return Ev{0.0, -1.0};
        double t, c; int pos;
        if (sp.p == 0) { t = m.vss[v]; c = 0; pos = m.vsn[v]; }
        else { t = P.tm[sp.p]; c = P.pc[sp.p]; pos = m.ten[P.seq[sp.p - 1]]; }
        for (int ci = 0; ci < sp.nch; ++ci) {
            const Chunk& ch = sp.ch[ci];
            for (int k = 0; k < ch.n; ++k) {
                int x = ch.p[(long)k * ch.step];
                double arr = t + Dm[(size_t)pos * N + m.tsn[x]];
                double st = arr < m.tr[x] ? m.tr[x] : arr;
                double e = st + sv[x];
                c += wd * (arr - t) + ww * (st - arr) + wl * m.tw[x] * e;
                if (e > m.tdl[x]) c += m.tdw[x] * (e - m.tdl[x]);
                t = e; pos = m.ten[x];
            }
        }
        if (sufLen > 0) {
            const Route& S = R[sp.s];
            const int len = (int)S.seq.size();
            if (m.wm == 0 && m.sgrp[v] == m.sgrp[sp.s]) {
                const Node& n0 = S.nd[sp.q];
                const Head& h0 = S.hd[sp.q];
                int x0 = S.seq[sp.q];
                double arrNew = t + Dm[(size_t)pos * N + m.tsn[x0]];
                c += wd * (arrNew - t);
                double dlt = arrNew - n0.arr;
                double endT; // time at end of the last task
                int lastEnd = m.ten[S.seq[len - 1]];
                if (dlt == 0.0) { c += h0.sc; endT = S.tm[len]; }
                else if (dlt > 0 && dlt <= h0.uR) { c += h0.sc + h0.gR * dlt; endT = S.tm[len] + h0.sR * dlt; }
                else if (dlt < 0 && -dlt <= h0.dL) { c += h0.sc + h0.gL * dlt; endT = S.tm[len] + h0.sL * dlt; }
                else {
                    // exact walk over the suffix with early convergence
                    double delta = dlt;
                    int i = sp.q;
                    endT = -1;
                    for (; i < len; ++i) {
                        const Node& ni = S.nd[i];
                        if (i > sp.q) { // back inside a linear range: close the rest in O(1)
                            const Head& hi = S.hd[i];
                            if (delta > 0) {
                                if (delta <= hi.uR) { c += ni.arcc + hi.sc + hi.gR * delta; endT = S.tm[len] + hi.sR * delta; break; }
                            } else if (-delta <= hi.dL) { c += ni.arcc + hi.sc + hi.gL * delta; endT = S.tm[len] + hi.sL * delta; break; }
                        }
                        double arr = ni.arr + delta;
                        double st = arr < ni.r ? ni.r : arr;
                        double e = st + ni.s;
                        if (i > sp.q) c += ni.arcc;
                        c += ww * (st - arr) + ni.lw * e;
                        if (e > ni.dl) c += ni.dw * (e - ni.dl);
                        delta = e - ni.e;
                        if (std::fabs(delta) < 1e-9) { c += S.pc[len] - S.pc[i + 1]; endT = S.tm[len]; break; }
                        endT = e;
                    }
                }
                double leg = Dm[(size_t)lastEnd * N + m.ven[v]];
                c += wd * leg;
                double ot = endT + leg - m.vse[v];
                if (ot > 0) c += m.wo * ot;
                return Ev{c, 0.0};
            }
            for (int i = sp.q; i < len; ++i) {
                int x = S.seq[i];
                double arr = t + Dm[(size_t)pos * N + m.tsn[x]];
                double st = arr < m.tr[x] ? m.tr[x] : arr;
                double e = st + sv[x];
                c += wd * (arr - t) + ww * (st - arr) + wl * m.tw[x] * e;
                if (e > m.tdl[x]) c += m.tdw[x] * (e - m.tdl[x]);
                t = e; pos = m.ten[x];
            }
        }
        double leg = Dm[(size_t)pos * N + m.ven[v]];
        t += leg; c += wd * leg;
        double ot = t - m.vse[v];
        if (ot > 0) c += m.wo * ot;
        return Ev{c, t};
    }

    bool tryIntra(int r, const std::vector<int>& ns, int k) {
        Ev e = sim(r, ns, k);
        double d = e.cost - R[r].cost;
        if (m.wm > 0) d += m.wm * (std::max(maxOthers(r, r), e.ret) - curMax);
        if (d < -EPS) { setRoute(r, ns); recomputeMax(); return true; }
        return false;
    }
    bool tryInter(int a, const std::vector<int>& na, int ka, int b, const std::vector<int>& nb, int kb) {
        Ev ea = sim(a, na, ka), eb = sim(b, nb, kb);
        double d = ea.cost + eb.cost - R[a].cost - R[b].cost;
        if (m.wm > 0) d += m.wm * (std::max(maxOthers(a, b), std::max(ea.ret, eb.ret)) - curMax);
        if (d < -EPS) { setRoute(a, na); setRoute(b, nb); recomputeMax(); return true; }
        return false;
    }
#ifdef HGS_CHECK
    void check(const Spec& s, const Ev& e, std::vector<int>& buf) {
        materialize(s, buf);
        Ev x = sim(s.v, buf, 0);
        if (std::fabs(x.cost - e.cost) > 1e-6 * (1 + std::fabs(x.cost))) { std::fprintf(stderr, "MISMATCH fast %.9g exact %.9g\n", e.cost, x.cost); std::abort(); }
        ++checked;
    }
    long long checked = 0;
#endif
    bool tryIntraSpec(const Spec& s) {
        Ev e = evalSpec(s);
#ifdef HGS_CHECK
        check(s, e, t2);
#endif
        double d = e.cost - R[s.v].cost;
        if (m.wm > 0) d += m.wm * (std::max(maxOthers(s.v, s.v), e.ret) - curMax);
        if (d >= -EPS * 0.5) return false;
        materialize(s, t1);
        return tryIntra(s.v, t1, s.p);
    }
    bool tryInterSpec(const Spec& a, const Spec& b) { return tryInterEv(a, evalSpec(a), b); }
    bool tryInterEv(const Spec& a, const Ev& ea, const Spec& b) {
        Ev eb = evalSpec(b);
#ifdef HGS_CHECK
        check(a, ea, t1); check(b, eb, t1);
#endif
        double d = ea.cost + eb.cost - R[a.v].cost - R[b.v].cost;
        if (m.wm > 0) d += m.wm * (std::max(maxOthers(a.v, b.v), std::max(ea.ret, eb.ret)) - curMax);
        if (d >= -EPS * 0.5) return false;
        materialize(a, t1); materialize(b, t2);
        return tryInter(a.v, t1, a.p, b.v, t2, b.p);
    }
    bool segOk(int v, const std::vector<int>& s, int p, int len) const {
        for (int i = p; i < p + len; ++i) if (!m.ok(v, s[i])) return false;
        return true;
    }
    // can vehicle v serve every task of route r from position p on?
    bool tailOk(int v, int r, int p) const { return p > R[r].lastBad[m.vcls[v]]; }
    static Spec mk(int v, int p, int s, int q) { Spec x; x.v = v; x.p = p; x.nch = 0; x.s = s; x.q = q; return x; }
    static void add(Spec& x, const int* p, int n, int step = 1) { x.ch[x.nch].p = p; x.ch[x.nch].n = n; x.ch[x.nch].step = step; ++x.nch; }

    bool tryPair(int u, int v) {
        int ru = rid[u], rv = rid[v], pu = idx[u], pv = idx[v];
        const std::vector<int> &A = R[ru].seq, &B = R[rv].seq;
        const int* Ad = A.data(); const int* Bd = B.data();
        int na = (int)A.size(), nb = (int)B.size();
        if (ru == rv) {
            // relocate segment starting at u before/after v
            for (int a = 1; a <= 2 && pu + a <= na; ++a) {
                if (pv >= pu && pv < pu + a) continue;
                int pvr = pv < pu ? pv : pv - a;
                for (int side = 0; side < 2; ++side) {
                    int ins = pvr + side;
                    if (ins == pu) continue;
                    Spec s;
                    if (ins < pu) {
                        s = mk(ru, ins, ru, pu + a);
                        add(s, Ad + pu, a); add(s, Ad + ins, pu - ins);
                    } else {
                        s = mk(ru, pu, ru, ins + a);
                        add(s, Ad + pu + a, ins - pu); add(s, Ad + pu, a);
                    }
                    if (tryIntraSpec(s)) return true;
                }
            }
            // swap segments
            for (int a = 1; a <= 2; ++a)
                for (int b = 1; b <= 2; ++b) {
                    int i, ai, j, bj;
                    if (pu < pv) { i = pu; ai = a; j = pv; bj = b; } else { i = pv; ai = b; j = pu; bj = a; }
                    if (i + ai > j || j + bj > na) continue;
                    Spec s = mk(ru, i, ru, j + bj);
                    add(s, Ad + j, bj); add(s, Ad + i + ai, j - i - ai); add(s, Ad + i, ai);
                    if (tryIntraSpec(s)) return true;
                }
            // 2-opt: reverse (lo, hi]
            int lo = std::min(pu, pv), hi = std::max(pu, pv);
            if (hi > lo + 1) {
                Spec s = mk(ru, lo + 1, ru, hi + 1);
                add(s, Ad + hi, hi - lo, -1);
                if (tryIntraSpec(s)) return true;
            }
            return false;
        }
        // ---- inter-route ----
        if (remU != u || remMod != modAt[ru]) { remU = u; remMod = modAt[ru]; remOk[1] = remOk[2] = false; }
        for (int a = 1; a <= 2 && pu + a <= na; ++a) {
            if (!segOk(rv, A, pu, a)) continue;
            Spec sa = mk(ru, pu, ru, pu + a);
            if (!remOk[a]) { remEv[a] = evalSpec(sa); remOk[a] = true; }
            for (int side = 0; side < 2; ++side) {
                int ins = pv + side;
                Spec sb = mk(rv, ins, rv, ins);
                add(sb, Ad + pu, a);
                if (tryInterEv(sa, remEv[a], sb)) return true;
            }
        }
        for (int a = 1; a <= 2 && pu + a <= na; ++a)
            for (int b = 1; b <= 2 && pv + b <= nb; ++b) {
                if (!segOk(rv, A, pu, a) || !segOk(ru, B, pv, b)) continue;
                Spec sa = mk(ru, pu, ru, pu + a); add(sa, Bd + pv, b);
                Spec sb = mk(rv, pv, rv, pv + b); add(sb, Ad + pu, a);
                if (tryInterSpec(sa, sb)) return true;
            }
        // 2-opt*: tails after u and after v swapped
        if (tailOk(rv, ru, pu + 1) && tailOk(ru, rv, pv + 1)) {
            Spec sa = mk(ru, pu + 1, rv, pv + 1);
            Spec sb = mk(rv, pv + 1, ru, pu + 1);
            if (tryInterSpec(sa, sb)) return true;
        }
        // 2-opt*: u directly followed by v (and its tail)
        if (tailOk(rv, ru, pu + 1) && tailOk(ru, rv, pv)) {
            Spec sa = mk(ru, pu + 1, rv, pv);
            Spec sb = mk(rv, pv, ru, pu + 1);
            if (tryInterSpec(sa, sb)) return true;
        }
        return false;
    }

    // non-granular intra-route relocation of the 1-2 task block at u to every position of its route
    bool tryIntraAll(int u) {
        int ru = rid[u], pu = idx[u];
        const std::vector<int>& A = R[ru].seq;
        const int* Ad = A.data();
        int na = (int)A.size();
        for (int a = 1; a <= 2 && pu + a <= na; ++a)
            for (int ins = 0; ins + a <= na; ++ins) {
                if (ins == pu) continue;
                Spec s;
                if (ins < pu) {
                    s = mk(ru, ins, ru, pu + a);
                    add(s, Ad + pu, a); add(s, Ad + ins, pu - ins);
                } else {
                    s = mk(ru, pu, ru, ins + a);
                    add(s, Ad + pu + a, ins - pu); add(s, Ad + pu, a);
                }
                if (tryIntraSpec(s)) return true;
            }
        return false;
    }

    bool tryEmpty(int u) {
        int ru = rid[u], pu = idx[u];
        const std::vector<int>& A = R[ru].seq;
        int na = (int)A.size();
        ++stampCtr;
        for (int e = 0; e < m.V; ++e) {
            if (e == ru || !R[e].seq.empty() || m.vcls[e] < 0 || stamp[m.twin[e]] == stampCtr) continue;
            if (!m.ok(e, u)) continue;
            stamp[m.twin[e]] = stampCtr;
            for (int a = 1; a <= 2 && pu + a <= na; ++a) {
                if (!segOk(e, A, pu, a)) continue;
                Spec sa = mk(ru, pu, ru, pu + a);
                Spec sb = mk(e, 0, -1, 0); add(sb, A.data() + pu, a);
                if (tryInterSpec(sa, sb)) return true;
            }
        }
        return false;
    }

    bool tryRouteSwaps() {
        bool any = false;
        for (int a = 0; a < m.V; ++a)
            for (int b = a + 1; b < m.V; ++b) {
                if (R[a].seq.empty() && R[b].seq.empty()) continue;
                if (m.twin[a] == m.twin[b] || m.vcls[a] < 0 || m.vcls[b] < 0) continue;
                if (!tailOk(b, a, 0) || !tailOk(a, b, 0)) continue;
                Spec sa = mk(a, 0, -1, 0); add(sa, R[b].seq.data(), (int)R[b].seq.size());
                Spec sb = mk(b, 0, -1, 0); add(sb, R[a].seq.data(), (int)R[a].seq.size());
                if (tryInterSpec(sa, sb)) any = true;
            }
        return any;
    }

    void run(const std::vector<int>& assigned) {
        order = assigned;
        bool improved = true;
        while (improved && !timeUp) {
            improved = false;
            rng.shuffle(order.begin(), order.end());
            for (size_t oi = 0; oi < order.size(); ++oi) {
                int u = order[oi];
                if ((oi & 15) == 0 && Clock::now() >= deadline) { timeUp = true; break; }
                bool again;
                do {
                    again = false;
                    const std::vector<int>& nb = m.nbr[u];
                    size_t nbn = std::min(nb.size(), kLimit);
                    long long lt = lastTest[u];
                    lastTest[u] = ++ctr;
                    for (size_t q = 0; q < nbn; ++q) {
                        if (lt > std::max(modAt[rid[u]], modAt[rid[nb[q]]])) continue;
                        if (tryPair(u, nb[q])) { again = true; improved = true; break; }
                    }
                    if (!again && tryEmpty(u)) { again = true; improved = true; }
                    if (!again && useIntraAll && lastIntra[u] <= modAt[rid[u]]) {
                        lastIntra[u] = ++ctr;
                        if (tryIntraAll(u)) { again = true; improved = true; }
                    }
                } while (again);
            }
            if (!timeUp && tryRouteSwaps()) improved = true;
        }
    }
};

// ---------------- Population ----------------
struct Indiv {
    std::vector<int> tour;
    std::vector<std::vector<int>> routes;
    std::vector<int> succ, pred;
    double cost = INF;
    double biased = 0;
};

struct Solver {
    const Model& m;
    const Params& par;
    Rng rng;
    Clock::time_point t0, deadline;
    std::vector<Indiv> pop;
    std::vector<std::vector<double>> dist;
    Indiv best;
    long long iter = 0;
    double bestAt = 0;
    std::vector<TrajPoint> traj;

    Solver(const Model& mm) : m(mm), par(mm.in.par), rng((uint64_t)mm.in.par.seed), ls(mm, rng) {}

    double elapsed() const { return std::chrono::duration<double>(Clock::now() - t0).count(); }

    Indiv fromRoutes(const std::vector<std::vector<int>>& routes, double cost) const {
        Indiv x;
        x.routes = routes; x.cost = cost;
        x.succ.assign(m.T, -1); x.pred.assign(m.T, -2);
        for (int v : m.vorder) {
            const std::vector<int>& s = routes[v];
            for (size_t i = 0; i < s.size(); ++i) {
                x.tour.push_back(s[i]);
                if (i > 0) x.pred[s[i]] = s[i - 1];
                if (i + 1 < s.size()) x.succ[s[i]] = s[i + 1];
            }
        }
        return x;
    }
    double distance(const Indiv& a, const Indiv& b) const {
        int diff = 0;
        for (int t : m.assigned) {
            if (a.succ[t] != b.succ[t] && a.succ[t] != b.pred[t]) ++diff;
            if (a.pred[t] == -2 && b.pred[t] != -2 && b.succ[t] != -1) ++diff;
        }
        return (double)diff / std::max<size_t>(1, m.assigned.size());
    }

    LS ls;
    std::vector<std::vector<int>> splitBuf;
    Indiv educate(const std::vector<int>& tour, bool initial) {
        ls.kLimit = (size_t)par.granularK; (void)initial;
        std::vector<std::vector<int>>& routes = splitBuf;
        m.split(tour, routes);
        ls.load(routes, deadline);
        ls.run(m.assigned);
        std::vector<std::vector<int>> out(m.V);
        for (int v = 0; v < m.V; ++v) out[v] = ls.R[v].seq;
        return fromRoutes(out, m.totalCost(ls.R));
    }

    void updateBiased() {
        int n = (int)pop.size();
        if (n == 1) { pop[0].biased = 0; return; }
        std::vector<std::pair<double, int>> div(n), fit(n);
        for (int i = 0; i < n; ++i) {
            std::vector<double> d;
            d.reserve(n);
            for (int j = 0; j < n; ++j) if (j != i) d.push_back(dist[i][j]);
            int k = std::min(par.nbClose, (int)d.size());
            std::partial_sort(d.begin(), d.begin() + k, d.end());
            double s = 0;
            for (int q = 0; q < k; ++q) s += d[q];
            div[i] = std::make_pair(-s / std::max(1, k), i); // larger diversity -> better rank
            fit[i] = std::make_pair(pop[i].cost, i);
        }
        std::sort(div.begin(), div.end());
        std::sort(fit.begin(), fit.end());
        std::vector<double> dr(n), fr(n);
        for (int r = 0; r < n; ++r) { dr[div[r].second] = (double)r / (n - 1); fr[fit[r].second] = (double)r / (n - 1); }
        for (int i = 0; i < n; ++i) {
            if (n <= par.nbElite) pop[i].biased = fr[i];
            else pop[i].biased = fr[i] + (1.0 - (double)par.nbElite / n) * dr[i];
        }
    }
    void removeAt(int w) {
        pop.erase(pop.begin() + w);
        dist.erase(dist.begin() + w);
        for (auto& row : dist) row.erase(row.begin() + w);
    }
    void survivors() {
        while ((int)pop.size() > par.popMin) {
            updateBiased();
            int n = (int)pop.size(), w = -1;
            double wf = -1;
            bool wClone = false;
            for (int i = 0; i < n; ++i) {
                bool clone = false;
                for (int j = 0; j < n; ++j) if (j != i && dist[i][j] < 1e-9) { clone = true; break; }
                if (w < 0 || (clone && !wClone) || (clone == wClone && pop[i].biased > wf)) {
                    w = i; wf = pop[i].biased; wClone = clone;
                }
            }
            removeAt(w);
        }
    }
    void addToPop(Indiv&& x) {
        if (x.cost < best.cost - 1e-9) {
            best = x; bestAt = elapsed();
            traj.push_back(TrajPoint{bestAt, iter, x.cost});
            if (par.verbose) std::fprintf(stderr, "[%.2fs] iter %lld best %.6f\n", bestAt, iter, x.cost);
        }
        size_t n = pop.size();
        std::vector<double> row(n + 1, 0.0);
        for (size_t i = 0; i < n; ++i) row[i] = distance(x, pop[i]);
        for (size_t i = 0; i < n; ++i) dist[i].push_back(row[i]);
        dist.push_back(row);
        pop.push_back(std::move(x));
        if ((int)pop.size() >= par.popMin + par.popGen) survivors();
    }

    std::vector<int> crossoverOX(const Indiv& a, const Indiv& b) {
        int M = (int)a.tour.size();
        std::vector<int> child(M, -1);
        std::vector<char> used(m.T, 0);
        int s = rng.range(M), e = rng.range(M);
        for (int i = s; i != (e + 1) % M; i = (i + 1) % M) { child[i] = a.tour[i]; used[a.tour[i]] = 1; }
        int j = (e + 1) % M;
        for (int k = 1; k <= M; ++k) {
            int t = b.tour[(e + k) % M];
            if (!used[t]) { child[j] = t; used[t] = 1; j = (j + 1) % M; }
        }
        return child;
    }
    const Indiv& tournament() {
        int a = rng.range((int)pop.size()), b = rng.range((int)pop.size());
        return pop[a].biased < pop[b].biased ? pop[a] : pop[b];
    }
    bool timeUp() const { return Clock::now() >= deadline; }
    bool iterUp() const { return par.maxIter > 0 && iter >= par.maxIter; }

    // randomised nearest-neighbour giant tour (spatially coherent start for the local search)
    std::vector<char> seen;
    void nnTour(std::vector<int>& tour) {
        std::vector<int> fall = m.assigned;
        rng.shuffle(fall.begin(), fall.end());
        seen.assign(m.T, 0);
        tour.clear();
        size_t fp = 0;
        int cur = -1;
        const int pickFrom = 3;
        while (tour.size() < m.assigned.size()) {
            int cand[pickFrom], nc = 0;
            if (cur >= 0)
                for (int y : m.nbr[cur]) { if (!seen[y]) { cand[nc++] = y; if (nc == pickFrom) break; } }
            int nx;
            if (nc > 0) nx = cand[rng.range(nc)];
            else { while (seen[fall[fp]]) ++fp; nx = fall[fp]; }
            seen[nx] = 1; tour.push_back(nx); cur = nx;
        }
    }

    void generateInitial() {
        int n0 = par.popInit > 0 ? par.popInit : 2 * par.popMin;
        std::vector<int> tour = m.assigned;
        // The initial population may use at most ~20% of the time budget (but at least 4 individuals).
        Clock::time_point initEnd = t0 + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(0.2 * par.timeLimit));
        for (int i = 0; i < n0 && !timeUp() && !iterUp(); ++i) {
            if (i >= 4 && Clock::now() >= initEnd) break;
            if (i % 4 == 0) rng.shuffle(tour.begin(), tour.end());
            else nnTour(tour);
            ++iter;
            addToPop(educate(tour, true));
        }
    }

    void run() {
        t0 = Clock::now();
        deadline = t0 + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(par.timeLimit));
        if (m.assigned.empty()) { best.routes.assign(m.V, {}); best.cost = 0; return; }
        generateInitial();
        long long noImp = 0;
        int failedCycles = 0;       // consecutive cycles (between restarts) that did not improve the global best
        double lastBest = best.cost, cycleStartBest = best.cost;
        while (!timeUp() && !iterUp()) {
            if (pop.empty()) { generateInitial(); continue; }
            if (m.assigned.size() < 2) break;
            updateBiased();
            const Indiv& p1 = tournament();
            const Indiv& p2 = tournament();
            std::vector<int> child = crossoverOX(p1, p2);
            ++iter;
            addToPop(educate(child, false));
            if (best.cost < lastBest - 1e-9) { lastBest = best.cost; noImp = 0; }
            else if (++noImp >= par.maxIterNoImprove) {
                if (best.cost < cycleStartBest - 1e-9) failedCycles = 0; else ++failedCycles;
                cycleStartBest = best.cost;
                // Deterministic termination: stop once restarts stop paying off
                if (par.stopOnStagnation && failedCycles >= 2) break;
                pop.clear(); dist.clear(); noImp = 0;
                if (par.verbose) std::fprintf(stderr, "[%.2fs] restart\n", elapsed());
                generateInitial();
            }
        }
    }
};

} // namespace

Result solve(const Instance& in) {
    Model m(in);
    Solver s(m);
    s.run();
    Result res;
    res.iterations = s.iter;
    res.timeS = s.elapsed();
    res.bestFoundS = s.bestAt;
    res.trajectory = s.traj;
    res.unassigned = m.unassignedT;
    res.routes.assign(m.V, RouteOut());
    Components& o = res.obj;
    double mk = 0;
    const std::vector<std::vector<int>>& routes = s.best.routes;
    for (int v = 0; v < m.V; ++v) {
        RouteOut& ro = res.routes[v];
        ro.returnTime = m.vss[v];
        if (routes.empty() || routes[v].empty()) continue;
        const double* Dm = in.mat[m.vcls[v]].data();
        double t = m.vss[v]; int pos = m.vsn[v];
        for (int x : routes[v]) {
            double arr = t + Dm[(size_t)pos * m.N + m.tsn[x]];
            double st = std::max(arr, m.tr[x]);
            double e = st + m.svcV[(size_t)v * m.T + x];
            ro.deadhead += arr - t; ro.wait += st - arr;
            o.latency += m.tw[x] * e;
            if (e > m.tdl[x]) o.lateness += m.tdw[x] * (e - m.tdl[x]);
            ro.visits.push_back(Visit{x, arr, st, e});
            t = e; pos = m.ten[x];
        }
        double leg = Dm[(size_t)pos * m.N + m.ven[v]];
        ro.deadhead += leg; t += leg;
        ro.returnTime = t;
        o.deadhead += ro.deadhead; o.wait += ro.wait;
        o.overtime += std::max(0.0, t - m.vse[v]);
        mk = std::max(mk, t);
    }
    o.makespan = mk;
    o.total = m.wl * o.latency + m.wd * o.deadhead + m.ww * o.wait + m.wo * o.overtime + m.wm * o.makespan + o.lateness;
    return res;
}

} // namespace hgs
