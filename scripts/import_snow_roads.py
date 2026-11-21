"""Extract road centerlines from an OSM PBF and clip to the simulator's municipal boundary."""
import argparse
import gzip
import json
from pathlib import Path

import osmium
from shapely.affinity import scale
from shapely.geometry import LineString
from shapely.ops import substring

from snowmap.city_cache import read_snapshot


ROAD_TYPES = frozenset("""
    motorway motorway_link trunk trunk_link primary primary_link secondary secondary_link
    tertiary tertiary_link unclassified residential living_street service road track
""".split())


def parts(geometry):
    if geometry.geom_type == "LineString":
        if geometry.length:
            yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from parts(part)


def load_roads(path, boundary):
    segments = []
    west, south, east, north = boundary.bounds
    objects = osmium.FileProcessor(path, osmium.osm.NODE | osmium.osm.WAY).with_locations()
    for way in objects:
        if not way.is_way():
            continue
        tags = way.tags
        if tags.get("highway") not in ROAD_TYPES or tags.get("area") == "yes":
            continue
        coords = [(node.lon, node.lat) for node in way.nodes if node.location.valid()]
        if len(coords) < 2:
            continue
        xs, ys = zip(*coords)
        if max(xs) < west or min(xs) > east or max(ys) < south or min(ys) > north:
            continue
        line = LineString(coords)
        osm_id = way.id
        name = tags.get("name") or tags.get("ref") or f"Участок OSM №{osm_id}"
        index = 0
        for piece in parts(line.intersection(boundary)):
            metric = scale(piece, xfact=67_000, yfact=111_195, origin=(0, 0))
            for start in range(0, int(metric.length) + 1, 350):
                item = substring(metric, start, min(start + 350, metric.length))
                if item.geom_type != "LineString":
                    continue
                coords = scale(item, xfact=1/67_000, yfact=1/111_195, origin=(0, 0)).coords
                rounded = [(round(x, 7), round(y, 7)) for x, y in coords]
                for offset in range(0, len(rounded) - 1, 199):
                    section = rounded[offset:offset + 200]
                    if len(set(section)) < 2:
                        continue
                    index += 1
                    segments.append({"id": f"osm-{osm_id}-{index}", "osm_way": osm_id, "name": name,
                                     "coordinates": section})
    return sorted(segments, key=lambda item: (item["osm_way"], item["id"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pbf", type=Path)
    parser.add_argument("buildings", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    boundary, _, _ = read_snapshot(args.buildings, args.buildings.stat().st_mtime_ns)
    segments = load_roads(args.pbf, boundary)
    if not segments:
        parser.error("No roads intersect the municipal boundary")
    with osmium.io.Reader(args.pbf) as reader:
        timestamp = reader.header().get("osmosis_replication_timestamp")
    result = {
        "source": "OpenStreetMap",
        "source_timestamp": timestamp or None,
        "segments": segments,
    }
    args.output.write_bytes(gzip.compress(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))
    print(f"{len(segments)} road sections from {len({s['osm_way'] for s in segments})} OSM ways")


if __name__ == "__main__":
    main()
