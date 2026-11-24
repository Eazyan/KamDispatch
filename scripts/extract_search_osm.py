"""Build the search-only OSM extract from a dated regional PBF.

Run from the repository root with Shapely and osmium installed.
"""

import argparse
import gzip
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

import osmium
from shapely.geometry import LineString, Point
from shapely.ops import polygonize, unary_union
from shapely.prepared import prep

from monitoring.citizen_addresses import OSM_ATLAS, PLACE_TAGS


def city_polygon():
    with gzip.open(OSM_ATLAS / "city-boundary.osm.gz") as file:
        root = ElementTree.parse(file).getroot()
    nodes = {node.get("id"): (float(node.get("lon")), float(node.get("lat"))) for node in root.findall("node")}
    ways = {way.get("id"): [nodes[ref.get("ref")] for ref in way.findall("nd")] for way in root.findall("way")}
    relation = root.find("relation[@id='4139922']")
    rings = {}
    for role in ("outer", "inner"):
        lines = [LineString(ways[member.get("ref")]) for member in relation.findall("member")
                 if member.get("type") == "way" and member.get("role") == role]
        rings[role] = unary_union(list(polygonize(lines)))
    result = rings["outer"].difference(rings["inner"])
    if result.is_empty or not result.is_valid:
        raise ValueError("Invalid city boundary")
    return result


def searchable(tags):
    return bool((tags.get("addr:housenumber") and (tags.get("addr:street") or tags.get("addr:place")))
                or ((tags.get("name") or tags.get("name:ru"))
                    and (tags.get("highway") or any(key in tags for key in PLACE_TAGS))))


def extract(source, output):
    boundary = city_polygon()
    city = prep(boundary)
    west, south, east, north = boundary.bounds

    def in_city(location):
        return (location.valid() and west <= location.lon <= east and south <= location.lat <= north
                and city.covers(Point(location.lon, location.lat)))

    with TemporaryDirectory(dir=output.parent) as tmp:
        xml = Path(tmp) / "search.osm"
        count = 0
        relations = set()
        with osmium.BackReferenceWriter(xml, source, relation_depth=1) as writer:
            keys = ("addr:housenumber", "name", "name:ru")
            features = (osmium.FileProcessor(source)
                        .with_areas(osmium.filter.KeyFilter(*keys))
                        .with_filter(osmium.filter.KeyFilter(*keys)))
            for item in features:
                if not searchable(item.tags):
                    continue
                if item.is_node() and in_city(item.location):
                    writer.add(item)
                    count += 1
                elif item.is_way() and any(in_city(node.location) for node in item.nodes):
                    writer.add(item)
                    count += 1
                elif item.is_area() and not item.from_way():
                    if any(in_city(node.location) for ring in item.outer_rings() for node in ring):
                        relations.add(item.orig_id())
            for relation in osmium.FileProcessor(source, osmium.osm.RELATION):
                if relation.id in relations:
                    writer.add(relation)
                    count += 1
        if count == 0:
            raise ValueError("Extract is empty")
        compressed = Path(tmp) / output.name
        with xml.open("rb") as reader, compressed.open("wb") as destination:
            with gzip.GzipFile(fileobj=destination, mode="wb", filename="", mtime=0) as archive:
                shutil.copyfileobj(reader, archive)
        compressed.replace(output)
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("regional_pbf", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(f"Extracted {extract(args.regional_pbf, args.output)} address, street or place OSM objects")
