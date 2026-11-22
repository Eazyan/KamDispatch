#!/usr/bin/env python3
# Обработка выгрузок Overpass -> data/residential.csv и data/attractors.csv.
# Площадь контура — шнуровка Гаусса в локальной эквивалентной цилиндрической
# проекции (м/градус по широте ~111132, по долготе * cos(lat0)).
import json, csv, math, os, sys
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")

LAT0 = 53.0
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = M_PER_DEG_LAT * math.cos(math.radians(LAT0))

def project(lon, lat):
    return lon * M_PER_DEG_LON, lat * M_PER_DEG_LAT

def ring_area_centroid(geom):
    """geom: [{lat,lon},...] -> (area_m2, (cx, cy) в lon/lat) или (0, None)."""
    if not geom or len(geom) < 3:
        return 0.0, None
    pts = [project(p["lon"], p["lat"]) for p in geom]
    a = 0.0
    cx = cy = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        cr = x1 * y2 - x2 * y1
        a += cr
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    a = abs(a) / 2.0
    if a < 1e-6:
        return 0.0, None
    # центроид многоугольника (знак площади не важен: числитель меняет знак вместе с ним)
    cx /= 6.0 * a
    cy /= 6.0 * a
    # обратно в градусы (проекция почти линейная на масштабе города)
    cx /= M_PER_DEG_LON
    cy /= M_PER_DEG_LAT
    return a, (cx, cy)

def element_geom_parts(el):
    """Список колец геометрии: для way — [geometry]; для relation — геометрии member'ов с role=outer."""
    if el["type"] == "way":
        return [el.get("geometry", [])]
    parts = []
    for m in el.get("members", []):
        if m.get("type") == "way" and m.get("role") == "outer" and "geometry" in m:
            parts.append(m["geometry"])
    return parts

def area_centroid(el):
    total = 0.0
    cx = cy = 0.0
    for g in element_geom_parts(el):
        a, c = ring_area_centroid(g)
        if a and c:
            total += a
            cx += c[0] * a
            cy += c[1] * a
    if total:
        return round(total, 1), (cx / total, cy / total)
    return None, None

# ---------- 1. Жильё ----------
residential_vals = {"apartments", "residential", "house", "detached", "dormitory",
                    "semidetached_house", "terrace"}
rows_res = []
n_skip_yes = 0
with open(os.path.join(DATA, "osm_residential.json"), encoding="utf-8") as f:
    d = json.load(f)
for el in d["elements"]:
    if el["type"] != "way":
        continue
    t = el.get("tags", {})
    b = t.get("building", "")
    if b == "yes":
        # building=yes берём, только если есть building:levels или addr:*
        if not t.get("building:levels") and not any(k.startswith("addr:") for k in t):
            n_skip_yes += 1
            continue
    elif b not in residential_vals:
        continue
    area, cen = area_centroid(el)
    if cen is None:
        continue
    street = t.get("addr:street", "").strip()
    house = t.get("addr:housenumber", "").strip()
    addr = ", ".join(x for x in (street, house) if x)
    rows_res.append({
        "osm_id": f"w{el['id']}",
        "lon": round(cen[0], 7),
        "lat": round(cen[1], 7),
        "building": b,
        "levels": t.get("building:levels", ""),
        "area_m2": area if area is not None else "",
        "addr": addr,
    })
rows_res.sort(key=lambda r: r["osm_id"])

with open(os.path.join(DATA, "residential.csv"), "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["osm_id", "lon", "lat", "building", "levels", "area_m2", "addr"])
    w.writeheader()
    w.writerows(rows_res)

n_levels = sum(1 for r in rows_res if r["levels"])
def _float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0
sum_area = sum(_float(r["area_m2"]) for r in rows_res)
sum_area_levels = sum(_float(r["area_m2"]) * _float(r["levels"]) for r in rows_res if r["levels"])

# ---------- 2. Притяжение ----------
SCHOOL = {"school", "kindergarten", "university", "college"}
HEALTH = {"hospital", "clinic"}
SHOP_AM = {"marketplace", "cafe", "restaurant"}
SERVICE = {"bank", "fuel", "place_of_worship", "townhall"}

def classify(t):
    """-> (kind, tag) или None. Приоритет: amenity > shop > office > building > landuse/industrial/man_made."""
    am = t.get("amenity", "")
    if am in SCHOOL:  return "school", f"amenity={am}"
    if am in HEALTH:  return "health", f"amenity={am}"
    if am in SERVICE: return "service", f"amenity={am}"
    if am in SHOP_AM: return "shop", f"amenity={am}"
    if t.get("shop"): return "shop", f"shop={t['shop']}"
    if t.get("office"): return "work", f"office={t['office']}"
    b = t.get("building", "")
    if b == "office": return "work", f"building={b}"
    if b in ("retail", "commercial"): return "shop", f"building={b}"
    if b in ("industrial", "warehouse"): return "industry", f"building={b}"
    if b == "public": return "service", f"building={b}"
    if t.get("industrial") == "port" or t.get("landuse") == "port" or t.get("man_made") == "pier":
        return "port", "industrial=port" if t.get("industrial") == "port" else (f"landuse={t['landuse']}" if t.get("landuse") == "port" else "man_made=pier")
    lu = t.get("landuse", "")
    if lu == "industrial": return "industry", f"landuse={lu}"
    if lu == "commercial": return "work", f"landuse={lu}"
    if lu == "retail": return "shop", f"landuse={lu}"
    return None

rows_att, seen = [], set()
def add_attractor(el, lon, lat, area):
    t = el.get("tags", {})
    c = classify(t)
    if not c:
        return
    oid = f"{el['type'][0]}{el['id']}"
    if oid in seen:
        return
    seen.add(oid)
    rows_att.append({
        "osm_id": oid, "lon": round(lon, 7), "lat": round(lat, 7),
        "kind": c[0], "tag": c[1], "area_m2": area if area is not None else "",
    })

# сначала полигоны (есть площадь)
with open(os.path.join(DATA, "osm_attract_poly.json"), encoding="utf-8") as f:
    d = json.load(f)
for el in d["elements"]:
    area, cen = area_centroid(el)
    if cen is None:
        continue
    add_attractor(el, cen[0], cen[1], area)

# затем точки/контуры с center
with open(os.path.join(DATA, "osm_attract_center.json"), encoding="utf-8") as f:
    d = json.load(f)
for el in d["elements"]:
    if "center" in el:
        lon, lat = el["center"]["lon"], el["center"]["lat"]
    elif "lat" in el and "lon" in el:
        lon, lat = el["lon"], el["lat"]
    else:
        continue
    add_attractor(el, lon, lat, None)

rows_att.sort(key=lambda r: r["osm_id"])
with open(os.path.join(DATA, "attractors.csv"), "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["osm_id", "lon", "lat", "kind", "tag", "area_m2"])
    w.writeheader()
    w.writerows(rows_att)

kinds = Counter(r["kind"] for r in rows_att)

print(f"residential.csv: {len(rows_res)} зданий (пропущено building=yes без levels/addr: {n_skip_yes}),"
      f" с levels: {n_levels}, сумма площади: {sum_area:,.0f} м2, площадь x этажи: {sum_area_levels:,.0f} м2")
print("attractors.csv:", len(rows_att), dict(sorted(kinds.items())))
