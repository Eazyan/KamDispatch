#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Классификация рёбер graph_v3: passable / municipal_service / required_current_stage
+ приоритеты 1/2/3 (веса 10/3/1). Только локальные источники, сеть запрещена.

Вход:  out/graph_v3.json (только чтение), mapdata/data/attractors.csv (только чтение)
Выход: out/service_classification.csv, out/manual_review.csv
       (+ печать статистики и инвариантов; QA-карту собирает prep/build_check_map.py)

Правила (mapprep/TASK_service_classification.md + mapprep/TASK_align_stage1_map_v3.md):
- priority 1: (а) bus_route=true; (б) trunk/primary/secondary(+_link); (в) критические
  подъезды — рёбра в <=150 м от attractors tag=hospital/clinic (fire_station/emergency
  в attractors отсутствуют — проверено).
- priority 2: tertiary/residential/unclassified/tertiary_link с названием;
  priority 3: living_street/service или без названия.
- priority НЕ равен этапу. Этап 1 (required_current_stage): municipal_service
  И (bus_route=true на ребре/паре ИЛИ critical_access=true). Просто priority==1
  (магистраль без автобуса/крит.доступа) — следующие этапы.
- priority_source=ksodd НЕ назначается (привязки ksodd_street_match.csv нет).
- ownership: municipal подтвердить нечем (КСОДД-привязки нет, OSM владение не даёт)
  -> везде unknown, ничего не выдумываем.
- passable=false только при доказательстве: граф v3 уже исключил way с
  access=private/no/customers (проверено по osm_petropavlovsk.json) -> все рёбра
  passable=true.
- Парная семантика: решение принимается на пару (u->v, v->u), копируется на оба
  направления; one-way — отдельно.
"""
import csv
import json
import math
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # mapprep/
PROJ = os.path.dirname(ROOT)                                        # snegoplan/
GP = os.path.join(ROOT, "out", "graph_v3.json")
AP = os.path.join(PROJ, "mapdata", "data", "attractors.csv")
OUT_CSV = os.path.join(ROOT, "out", "service_classification.csv")
OUT_REV = os.path.join(ROOT, "out", "manual_review.csv")

CRIT_TAGS = ("hospital", "clinic", "fire_station", "emergency")
P1_HW = {"trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link"}
P2_HW = {"tertiary", "tertiary_link", "residential", "unclassified"}
W = {1: 10, 2: 3, 3: 1}

# ---------- вход ----------
g = json.load(open(GP, encoding="utf-8"))
E = g["edges"]
by_id = {e["id"]: e for e in E}

pois_all = list(csv.DictReader(open(AP, encoding="utf-8")))
crit_pois = [p for p in pois_all if any(t in p["tag"] for t in CRIT_TAGS)]
crit_xy = [(float(p["lon"]), float(p["lat"]), p["tag"]) for p in crit_pois]

LAT0 = 53.04
KX = math.cos(LAT0 * math.pi / 180.0) * 111320.0
KY = 110540.0


def pt_seg_dist2(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l = dx * dx + dy * dy
    t = 0.0 if l == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l))
    qx, qy = ax + t * dx - px, ay + t * dy - py
    return qx * qx + qy * qy


def dist_poi_edge(lon, lat, geom):
    px, py = lon * KX, lat * KY
    best = 1e18
    for i in range(len(geom) - 1):
        a, b = geom[i], geom[i + 1]
        best = min(best, pt_seg_dist2(px, py, a[0] * KX, a[1] * KY, b[0] * KX, b[1] * KY))
    return math.sqrt(best)


# критические подъезды: минимальное расстояние ребра до любого критического POI
crit_dist = {}
poi_best = [1e18] * len(crit_xy)  # мин. расстояние каждого POI до графа
for e in E:
    dmin, tagmin = 1e18, None
    for i, (lon, lat, tag) in enumerate(crit_xy):
        d = dist_poi_edge(lon, lat, e["geometry"])
        if d < poi_best[i]:
            poi_best[i] = d
        if d < dmin:
            dmin, tagmin = d, tag
    crit_dist[e["id"]] = (dmin, tagmin)
poi_covered = sum(1 for d in poi_best if d <= 150.0)


# ---------- сырые (направленные) решения ----------
def raw_decision(e):
    """-> (priority, source, confidence, rationale_raw)"""
    d, _ = crit_dist[e["id"]]
    if d <= 150.0:
        return (1, "attractor", 0.9, "критический подъезд <=150 м от %s" % _)
    if e.get("bus_route"):
        return (1, "osm_bus", 0.95, "автобусный маршрут (bus_route)")
    hw = e["highway"]
    if hw in P1_HW:
        return (1, "osm_highway", 0.8, "магистраль %s" % hw)
    if hw in P2_HW:
        if e["name"]:
            return (2, "osm_highway", 0.8, "%s с названием" % hw)
        return (3, "estimate", 0.5, "%s без названия" % hw)
    return (3, "osm_highway", 0.7, "%s" % hw)


RANK = {"attractor": 4, "osm_bus": 3, "osm_highway": 2, "estimate": 1, "ksodd": 0}

# ---------- парная семантика ----------
seen = set()
pair_rows = []       # решения: одна строка на пару/one-way
for e in E:
    if e["id"] in seen:
        continue
    r = e.get("reverse_edge_id")
    mate = by_id.get(r) if r else None
    members = [e] + ([mate] if mate else [])
    for m in members:
        seen.add(m["id"])

    raw = [raw_decision(m) for m in members]
    best = min(raw, key=lambda t: (t[0], -RANK[t[1]]))
    pri, src, conf = best[0], best[1], best[2]
    if len(set(x[0] for x in raw)) > 1 or len(set(x[1] for x in raw)) > 1:
        conf = min(conf, 0.6)  # направления сошлись не идеально — понижаем уверенность

    hwset = set(m["highway"] for m in members)
    critical = any(crit_dist[m["id"]][0] <= 150.0 for m in members)
    bus = any(bool(m.get("bus_route")) for m in members)
    service_like = all(m["highway"] == "service" for m in members)
    municipal = not service_like or critical

    # Этап 1: только municipal И (автобус ИЛИ крит. доступ). priority==1 сам по
    # себе основанием не является (магистрали без автобуса — следующие этапы).
    required = municipal and (bus or critical)
    basis = "both" if (bus and critical) else ("bus" if bus else ("crit" if critical else ""))

    if service_like and critical:
        why = "подъезд к критическому объекту (hospital/clinic) — проезд обязан содержаться"
    elif service_like:
        why = "service-подъезд без доказательства публичного статуса — не подтверждено"
    else:
        why = "публичная улица (%s)" % "/".join(sorted(hwset))

    # физические км пары: двусторонняя пара считается один раз (км одной стороны),
    # one-way — целиком
    if len(members) > 1:
        phys_km = members[0]["length_m"] / 1000.0
    else:
        phys_km = sum(m["length_m"] for m in members) / 1000.0
    pair_rows.append(dict(members=members, priority=pri, source=src, confidence=conf,
                          municipal=municipal, critical=critical, bus=bus,
                          required=required, basis=basis, why=why, km=phys_km))

# ---------- выгрузка CSV ----------
COLS = ["edge_id", "passable", "municipal_service", "required_current_stage", "priority",
        "priority_weight", "priority_source", "priority_confidence", "critical_access",
        "ownership", "review_flag", "rationale"]

review_rows = []
n_written = 0
with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader()
    for pr in pair_rows:
        conflict = len(set(x[0] for x in [raw_decision(m) for m in pr["members"]])) > 1
        est_pri = pr["source"] == "estimate"
        non_mun = not pr["municipal"]
        buscrit = pr["bus"] or pr["critical"]
        review = conflict or non_mun or est_pri
        rat = []
        if conflict:
            rat.append("ПАРНЫЙ КОНФЛИКТ направлений")
        rat.append(pr["why"])
        rat.append("приоритет: " + {1: "магистраль/автобус/крит.подъезд", 2: "именованная местная",
                                    3: "дворовая/без имени"}[pr["priority"]])
        if pr["required"]:
            rat.append("этап 1: " + {"bus": "автобусный маршрут", "crit": "критический подъезд",
                                     "both": "автобус + крит. подъезд"}[pr["basis"]])
        elif pr["municipal"]:
            rat.append("этап 2+: муниципальная сеть без автобуса/крит.доступа")
        rat.append("ownership: КСОДД-привязки и подтверждения владения нет -> unknown")
        rat_s = "; ".join(rat)
        for m in pr["members"]:
            w.writerow(dict(edge_id=m["id"], passable=True,
                            municipal_service=pr["municipal"],
                            required_current_stage=pr["required"],
                            priority=pr["priority"], priority_weight=W[pr["priority"]],
                            priority_source=pr["source"], priority_confidence=pr["confidence"],
                            critical_access=pr["critical"], ownership="unknown",
                            review_flag=review, rationale=rat_s))
            n_written += 1
        if review:
            review_rows.append(dict(
                edge_id=pr["members"][0]["id"],
                pair_edge_id=pr["members"][1]["id"] if len(pr["members"]) > 1 else "",
                issue=("nonmunicipal_buscrit" if (non_mun and buscrit)
                       else "nonmunicipal?" if non_mun
                       else ("pair_conflict" if conflict else "estimate_priority")),
                highway="/".join(sorted(set(x["highway"] for x in pr["members"]))),
                name=pr["members"][0]["name"] or "",
                zone=pr["members"][0]["zone"],
                pair_km=round(pr["km"], 3),
                priority=pr["priority"], priority_source=pr["source"],
                critical_access=pr["critical"], ownership="unknown", rationale=rat_s))

with open(OUT_REV, "w", newline="", encoding="utf-8") as f:
    c = ["edge_id", "pair_edge_id", "issue", "highway", "name", "zone", "pair_km",
         "priority", "priority_source", "critical_access", "ownership", "rationale"]
    w = csv.DictWriter(f, fieldnames=c)
    w.writeheader()
    w.writerows(review_rows)

# ---------- статистика ----------
dir_km = defaultdict(float)   # (pri, municipal, zone) -> км направленных рёбер
dir_n = defaultdict(int)
est_by_pri = defaultdict(lambda: [0, 0])  # pri -> [all-estimate, total]
st_dir_km = defaultdict(float)   # basis -> км направленных рёбер этапа 1
st_dir_n = defaultdict(int)
st_zone_dir_km = defaultdict(float)   # zone -> км направленных рёбер этапа 1
st_zone_pair_km = defaultdict(float)  # zone -> физ. км пар этапа 1
st_zone_pair_n = defaultdict(int)
later_zone_pair_km = defaultdict(float)  # municipal, не этап 1 (физ. км пар по зонам)
later_zone_pair_n = defaultdict(int)
for pr in pair_rows:
    for m in pr["members"]:
        k = (pr["priority"], pr["municipal"], m["zone"])
        dir_km[k] += m["length_m"] / 1000.0
        dir_n[k] += 1
        ae = (m["width_source"] == "estimate" and m.get("lanes_source") == "estimate"
              and m.get("maxspeed_source") == "estimate")
        est_by_pri[pr["priority"]][0 if ae else 1] += 1
        if pr["required"]:
            st_dir_km[pr["basis"]] += m["length_m"] / 1000.0
            st_dir_n[pr["basis"]] += 1
            st_zone_dir_km[m["zone"]] += m["length_m"] / 1000.0
    if pr["required"]:
        z = pr["members"][0]["zone"]
        st_zone_pair_km[z] += pr["km"]
        st_zone_pair_n[z] += 1
    elif pr["municipal"]:
        z = pr["members"][0]["zone"]
        later_zone_pair_km[z] += pr["km"]
        later_zone_pair_n[z] += 1

pair_km_pri = defaultdict(float)
pair_n_pri = defaultdict(int)
mun_pair_km = [0.0, 0.0]
p1_nostage_dir_km = 0.0     # P1 без допуска в этап 1 (магистраль без автобуса/крит.доступа)
p1_nostage_dir_n = 0
p1_nostage_pair_km = 0.0
p1_nostage_pair_n = 0
for pr in pair_rows:
    pair_km_pri[pr["priority"]] += pr["km"]
    pair_n_pri[pr["priority"]] += 1
    mun_pair_km[1 if pr["municipal"] else 0] += pr["km"]
    if pr["priority"] == 1 and not pr["required"]:
        p1_nostage_dir_n += len(pr["members"])
        p1_nostage_pair_n += 1
        p1_nostage_pair_km += pr["km"]
        p1_nostage_dir_km += sum(m["length_m"] for m in pr["members"]) / 1000.0

stats = dict(
    total_dir_km=sum(e["length_m"] for e in E) / 1000.0,
    total_pair_km=sum(pr["km"] for pr in pair_rows),
    n_edges=len(E), n_pairs=len(pair_rows),
    dir_km={"%d|%s|%d" % k: round(v, 3) for k, v in sorted(dir_km.items())},
    dir_n={"%d|%s|%d" % k: v for k, v in sorted(dir_n.items())},
    pair_km_pri={str(k): round(v, 3) for k, v in sorted(pair_km_pri.items())},
    pair_n_pri={str(k): v for k, v in sorted(pair_n_pri.items())},
    mun_pair_km=round(mun_pair_km[1], 3), nonmun_pair_km=round(mun_pair_km[0], 3),
    est_by_pri={str(k): v for k, v in sorted(est_by_pri.items())},
    n_crit_edges=sum(len(pr["members"]) for pr in pair_rows if pr["critical"]),
    n_crit_pairs=sum(1 for pr in pair_rows if pr["critical"]),
    n_bus_edges=sum(len(pr["members"]) for pr in pair_rows if pr["bus"]),
    n_bus_pairs=sum(1 for pr in pair_rows if pr["bus"]),
    stage1_dir_km=round(sum(st_dir_km.values()), 3),
    stage1_dir_n=sum(st_dir_n.values()),
    stage1_pair_km=round(sum(st_zone_pair_km.values()), 3),
    stage1_pair_n=sum(st_zone_pair_n.values()),
    stage1_by_basis_dir_km={k: round(v, 3) for k, v in sorted(st_dir_km.items())},
    stage1_by_basis_dir_n={k: v for k, v in sorted(st_dir_n.items())},
    stage1_by_basis_pair_km={},
    stage1_by_basis_pair_n={},
    stage1_zone_dir_km={str(k): round(v, 3) for k, v in sorted(st_zone_dir_km.items())},
    stage1_zone_pair_km={str(k): round(v, 3) for k, v in sorted(st_zone_pair_km.items())},
    stage1_zone_pair_n={str(k): v for k, v in sorted(st_zone_pair_n.items())},
    later_stage_mun_pair_km=round(sum(later_zone_pair_km.values()), 3),
    later_stage_mun_pair_n=sum(later_zone_pair_n.values()),
    later_stage_mun_zone_pair_km={str(k): round(v, 3) for k, v in sorted(later_zone_pair_km.items())},
    later_stage_mun_zone_pair_n={str(k): v for k, v in sorted(later_zone_pair_n.items())},
    p1_no_stage1_dir_km=round(p1_nostage_dir_km, 3), p1_no_stage1_dir_n=p1_nostage_dir_n,
    p1_no_stage1_pair_km=round(p1_nostage_pair_km, 3), p1_no_stage1_pair_n=p1_nostage_pair_n,
    n_pois=len(crit_xy), n_pois_covered=poi_covered,
    poi_tags=sorted(set(t for _, _, t in crit_xy)),
    n_review=len(review_rows),
    n_review_km=round(sum(r["pair_km"] for r in review_rows), 3),
)
for pr in pair_rows:
    if pr["required"]:
        stats["stage1_by_basis_pair_km"][pr["basis"]] = \
            round(stats["stage1_by_basis_pair_km"].get(pr["basis"], 0) + pr["km"], 3)
        stats["stage1_by_basis_pair_n"][pr["basis"]] = \
            stats["stage1_by_basis_pair_n"].get(pr["basis"], 0) + 1
print("=== РАСПРЕДЕЛЕНИЯ (service_stats) ===")
print(json.dumps(stats, ensure_ascii=False, indent=1))

# ---------- инварианты ----------
errs = []
if n_written != len(E):
    errs.append("CSV: записано %d != %d рёбер" % (n_written, len(E)))
for pr in pair_rows:
    if len(pr["members"]) > 1:
        a, b = pr["members"]
        if b.get("reverse_edge_id") != a["id"]:
            errs.append("пара %s: обратная ссылка сломана" % a["id"])
        if a["zone"] != b["zone"]:
            errs.append("пара %s: разные зоны %s/%s" % (a["id"], a["zone"], b["zone"]))
        if a["length_m"] != b["length_m"]:
            errs.append("пара %s: разная длина" % a["id"])
if any(not pr["municipal"] and True for pr in pair_rows):  # required=municipal по построению
    pass
ksodd = [pr for pr in pair_rows if pr["source"] == "ksodd"]
if ksodd:
    errs.append("priority_source=ksodd без привязки: %d" % len(ksodd))
tot = sum(e["length_m"] for e in E) / 1000.0
if abs(tot - 2498.0) > 0.5:
    errs.append("сумма км %.1f != 2498.0" % tot)

# явные проверки этапности (не только «по построению»)
nonmun_buscrit = []  # nonmunicipal пары с bus/crit: НЕ в этапе 1, уходят в review
for pr in pair_rows:
    if pr["required"] and not pr["municipal"]:
        errs.append("этап 1 при municipal=false: %s" % pr["members"][0]["id"])
    if pr["required"] and not (pr["bus"] or pr["critical"]):
        errs.append("этап 1 без bus/crit: %s" % pr["members"][0]["id"])
    if pr["municipal"] and (pr["bus"] or pr["critical"]) and not pr["required"]:
        errs.append("municipal bus/crit не в этапе 1: %s" % pr["members"][0]["id"])
    if not pr["municipal"] and (pr["bus"] or pr["critical"]):
        nonmun_buscrit.append(pr)

# ---------- связность (неориентированно): этап 1 и (отдельно) вся municipal сеть ----------
def components(sel):
    par = {}

    def find(x):
        par.setdefault(x, x)
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    for pr in pair_rows:
        if not sel(pr):
            continue
        for m in pr["members"]:
            ru, rv = find(m["u"]), find(m["v"])
            if ru != rv:
                par[ru] = rv
    comp = defaultdict(float)
    for pr in pair_rows:
        if sel(pr):
            comp[find(pr["members"][0]["u"])] += pr["km"]
    return sorted(comp.values(), reverse=True)


def conn_of(comps):
    return dict(n_components=len(comps),
                largest_km=round(comps[0], 1) if comps else 0,
                largest_share=round(comps[0] / sum(comps) * 100, 2) if comps else 0,
                small_components_km_lt1=round(sum(c for c in comps if c < 1.0), 2),
                n_small_components_lt1=sum(1 for c in comps if c < 1.0))


conn = conn_of(components(lambda pr: pr["required"]))
conn_mun = conn_of(components(lambda pr: pr["municipal"]))

print("=== ИНВАРИАНТЫ ===")
for name, ok in [
    ("required_current_stage => municipal_service", True),
    ("этап 1 = municipal И (bus_route ИЛИ critical_access), обе стороны", not errs),
    ("nonmunicipal bus/crit исключены из этапа 1 и отправлены в review: %d пар / %.3f км"
     % (len(nonmun_buscrit), round(sum(pr["km"] for pr in nonmun_buscrit), 3)),
     all(not pr["required"] for pr in nonmun_buscrit)),
    ("просто priority==1 достаточным основанием не считается (P1 без этапа 1: %d пар / %.1f км)"
     % (p1_nostage_pair_n, p1_nostage_pair_km), True),
    ("парные рёбра идентичны (решение на пару, копия на оба направления)", not any("пара" in e for e in errs)),
    ("обратное ребро в той же зоне", not any("зоны" in e for e in errs)),
    ("priority_source=ksodd => запись привязки (ksodd не используется)", not ksodd),
    ("passable=false только с rationale (passable=false нет)", True),
    ("сумма км по направленным рёбрам = 2498.0 (%.1f)" % tot, abs(tot - 2498.0) <= 0.5),
    ("CSV покрывает все рёбра (%d/%d)" % (n_written, len(E)), n_written == len(E)),
]:
    print(("PASS " if ok else "FAIL ") + name)
for e in errs:
    print("FAIL " + e)
print("=== СВЯЗНОСТЬ ЭТАПА 1 (required set) ===")
print(json.dumps(conn, ensure_ascii=False))
print("=== СВЯЗНОСТЬ ВСЕЙ municipal СЕТИ (этапы 2+, справочно) ===")
print(json.dumps(conn_mun, ensure_ascii=False))
print("=== КЛЮЧЕВЫЕ ЧИСЛА ===")
print(json.dumps({k: stats[k] for k in ["total_dir_km", "total_pair_km", "n_pairs",
                                        "mun_pair_km", "nonmun_pair_km", "n_crit_edges",
                                        "n_crit_pairs", "n_bus_edges", "n_bus_pairs",
                                        "stage1_dir_km", "stage1_dir_n", "stage1_pair_km",
                                        "stage1_pair_n", "stage1_by_basis_dir_km",
                                        "stage1_by_basis_pair_km", "later_stage_mun_pair_km",
                                        "later_stage_mun_pair_n", "p1_no_stage1_dir_km",
                                        "p1_no_stage1_pair_n", "n_pois", "n_pois_covered",
                                        "poi_tags", "n_review", "n_review_km"]},
                 ensure_ascii=False, indent=1))
sys.exit(1 if errs else 0)
