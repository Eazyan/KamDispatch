# -*- coding: utf-8 -*-
"""Здания и POI Петропавловска-Камчатского из PBF Geofabrik (pyosmium).
bbox берётся из scripts/overpass_query.ql. Один проход: nodes/ways с
координатами (locations), relations — через member ways.
Выход: data/residential.csv, data/attractors.csv (UTF-8).
"""
import csv
import math
import os
import re
import sys

import osmium

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
QL = os.path.join(HERE, "overpass_query.ql")
PBF = os.path.join(ROOT, "data", "raw", "far-eastern-fed-district-latest.osm.pbf")
OUT_RES = os.path.join(ROOT, "data", "residential.csv")
OUT_ATT = os.path.join(ROOT, "data", "attractors.csv")

R_EARTH = 6371000.0

RES_TYPES = {"apartments", "residential", "house", "detached", "dormitory",
             "semidetached_house", "terrace"}
ATT_BUILDING = {"commercial": "work", "retail": "work", "office": "work",
                "industrial": "industry", "warehouse": "industry",
                "public": "service"}
AMENITY_KIND = {"school": "school", "kindergarten": "school",
                "university": "school", "college": "school",
                "hospital": "health", "clinic": "health",
                "marketplace": "service", "bank": "service", "cafe": "service",
                "restaurant": "service", "fuel": "service",
                "place_of_worship": "service", "townhall": "service"}
LANDUSE_KIND = {"industrial": "industry", "commercial": "work",
                "retail": "shop", "port": "port"}


def parse_bbox():
    with open(QL, "r", encoding="utf-8") as f:
        m = re.search(r'\((\d+\.\d+),(\d+\.\d+),(\d+\.\d+),(\d+\.\d+)\)', f.read())
    if not m:
        raise SystemExit("bbox not found in overpass_query.ql")
    # Overpass bbox: (lat,lon,lat,lon) -> osmium Box: (lon,lat,lon,lat)
    return osmium.osm.Box(float(m.group(2)), float(m.group(1)),
                          float(m.group(4)), float(m.group(3)))


def tags_of(obj):
    return {t.k: t.v for t in obj.tags}


def interesting(tags):
    b = tags.get("building")
    if b in RES_TYPES or b in ATT_BUILDING:
        return True
    if b == "yes" and (tags.get("building:levels") or
                       any(k.startswith("addr:") for k in tags)):
        return True
    if tags.get("office") or tags.get("shop"):
        return True
    if tags.get("amenity") in AMENITY_KIND:
        return True
    if tags.get("landuse") in LANDUSE_KIND:
        return True
    if tags.get("man_made") == "pier" or tags.get("industrial") == "port":
        return True
    return False


def classify_attractor(tags):
    if tags.get("industrial") == "port":
        return "port", "industrial=port"
    if tags.get("man_made") == "pier":
        return "port", "man_made=pier"
    lu = tags.get("landuse")
    if lu in LANDUSE_KIND:
        return LANDUSE_KIND[lu], "landuse=" + lu
    am = tags.get("amenity")
    if am in AMENITY_KIND:
        return AMENITY_KIND[am], "amenity=" + am
    sh = tags.get("shop")
    if sh:
        return "shop", "shop=" + sh
    of = tags.get("office")
    if of:
        return "work", "office=" + of
    b = tags.get("building")
    if b in ATT_BUILDING:
        return ATT_BUILDING[b], "building=" + b
    return None


def classify_residential(tags):
    b = tags.get("building")
    if b in RES_TYPES:
        return True
    if b == "yes" and (tags.get("building:levels") or
                       any(k.startswith("addr:") for k in tags)):
        return True
    return False


def project(lon, lat, cos_lat):
    return (math.radians(lon) * R_EARTH * cos_lat, math.radians(lat) * R_EARTH)


def poly_area_centroid(pts):
    """pts — [(x,y),...] в метрах. -> (area_m2, (cx,cy)) или (0, avg)."""
    n = len(pts)
    if n < 3:
        return 0.0, None
    a = cx = cy = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        cr = x1 * y2 - x2 * y1
        a += cr
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    a *= 0.5
    if abs(a) < 1e-6:
        xs = sum(p[0] for p in pts) / n
        ys = sum(p[1] for p in pts) / n
        return 0.0, (xs, ys)
    cx /= 6.0 * a
    cy /= 6.0 * a
    return abs(a), (cx, cy)


def unproject(x, y, cos_lat):
    return (math.degrees(x / (R_EARTH * cos_lat)), math.degrees(y / R_EARTH))


def fmt_area(a):
    return "" if a is None else str(int(round(a)))


def main():
    box = parse_bbox()
    bl, tr = box.bottom_left, box.top_right
    left, bottom, right, top = bl.lon, bl.lat, tr.lon, tr.lat
    cos_lat = math.cos(math.radians((bottom + top) / 2.0))

    residential = []   # (osm_id, lon, lat, building, levels, area, addr)
    attractors = []    # (osm_id, lon, lat, kind, tag, area)
    way_geom = {}      # way_id -> [(lon,lat),...]  (для relations)

    fp = osmium.FileProcessor(PBF).with_locations()

    n_seen = 0
    for obj in fp:
        if obj.deleted:
            continue
        if obj.is_node():
            if not (left <= obj.lon <= right and bottom <= obj.lat <= top):
                continue
            tags = tags_of(obj)
            if not interesting(tags):
                continue
            kind_tag = classify_attractor(tags)
            if kind_tag:
                attractors.append(("n%d" % obj.id, obj.lon, obj.lat,
                                   kind_tag[0], kind_tag[1], None))
            elif classify_residential(tags):
                addr = ", ".join(x for x in (tags.get("addr:street"),
                              tags.get("addr:housenumber")) if x)
                residential.append(("n%d" % obj.id, obj.lon, obj.lat,
                                    tags.get("building", ""),
                                    tags.get("building:levels", ""), None, addr))
        elif obj.is_way():
            tags = tags_of(obj)
            if not interesting(tags):
                continue
            coords = [(nd.lon, nd.lat) for nd in obj.nodes
                      if nd.location.valid()]
            inside = any(left <= lo <= right and bottom <= la <= top
                         for lo, la in coords)
            if not inside:
                continue
            way_geom[obj.id] = coords
            pts = [project(lo, la, cos_lat) for lo, la in coords]
            area, cen = poly_area_centroid(pts)
            if cen:
                clon, clat = unproject(cen[0], cen[1], cos_lat)
            elif coords:
                clon = sum(c[0] for c in coords) / len(coords)
                clat = sum(c[1] for c in coords) / len(coords)
            else:
                continue
            kind_tag = classify_attractor(tags)
            if kind_tag:
                attractors.append(("w%d" % obj.id, clon, clat, kind_tag[0],
                                   kind_tag[1], area if area else None))
            elif classify_residential(tags):
                addr = ", ".join(x for x in (tags.get("addr:street"),
                              tags.get("addr:housenumber")) if x)
                residential.append(("w%d" % obj.id, clon, clat,
                                    tags.get("building", ""),
                                    tags.get("building:levels", ""),
                                    area if area else None, addr))
        elif obj.is_relation():
            tags = tags_of(obj)
            if not interesting(tags):
                continue
            kind_tag = classify_attractor(tags)
            if not kind_tag:
                continue
            pts = []
            coords_all = []
            for mem in obj.members:
                if mem.type == "w" and mem.ref in way_geom:
                    for lo, la in way_geom[mem.ref]:
                        coords_all.append((lo, la))
            if not coords_all:
                continue
            pts = [project(lo, la, cos_lat) for lo, la in coords_all]
            area, cen = poly_area_centroid(pts)
            if cen:
                clon, clat = unproject(cen[0], cen[1], cos_lat)
            else:
                clon = sum(c[0] for c in coords_all) / len(coords_all)
                clat = sum(c[1] for c in coords_all) / len(coords_all)
            attractors.append(("r%d" % obj.id, clon, clat, kind_tag[0],
                               kind_tag[1], area if area else None))
        n_seen += 1

    # --- запись CSV (UTF-8) ---
    with open(OUT_RES, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["osm_id", "lon", "lat", "building", "levels",
                    "area_m2", "addr"])
        for r in residential:
            w.writerow([r[0], "%.7f" % r[1], "%.7f" % r[2], r[3], r[4],
                        fmt_area(r[5]), r[6]])
    with open(OUT_ATT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["osm_id", "lon", "lat", "kind", "tag", "area_m2"])
        for r in attractors:
            w.writerow([r[0], "%.7f" % r[1], "%.7f" % r[2], r[3], r[4],
                        fmt_area(r[5])])

    # --- статистика ---
    n_lvl = sum(1 for r in residential if r[4])
    area_sum = sum(r[5] for r in residential if r[5])
    area_lvl_sum = sum(r[5] * float(r[4]) for r in residential
                       if r[5] and r[4] and r[4].replace(".", "", 1).isdigit())
    by_kind = {}
    for r in attractors:
        by_kind[r[3]] = by_kind.get(r[3], 0) + 1
    att_with_area = sum(1 for r in attractors if r[5])
    att_area_sum = sum(r[5] for r in attractors if r[5])
    print("RESIDENTIAL_TOTAL %d" % len(residential))
    print("RESIDENTIAL_WITH_LEVELS %d" % n_lvl)
    print("RESIDENTIAL_AREA_M2 %d" % int(round(area_sum)))
    print("RESIDENTIAL_AREA_X_LEVELS_M2 %d" % int(round(area_lvl_sum)))
    print("ATTRACTORS_TOTAL %d" % len(attractors))
    print("ATTRACTORS_WITH_AREA %d" % att_with_area)
    print("ATTRACTORS_AREA_M2 %d" % int(round(att_area_sum)))
    for k in sorted(by_kind):
        print("KIND %s %d" % (k, by_kind[k]))


if __name__ == "__main__":
    main()
