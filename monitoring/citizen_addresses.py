from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
import gzip
import re
from xml.etree import ElementTree

from shapely.geometry import LineString
from shapely.ops import polygonize

from .config import ROOT


CITY = "Петропавловск-Камчатский"
OSM_DIRECTORY = ROOT / "city-sim" / "data" / "pkc"
OSM_FILES = ("city.osm.gz", "depot-east.osm.gz")
STREET_TYPES = {
    "ул", "улица", "пр", "проспект", "просп", "пер", "переулок", "пл", "площадь",
    "ш", "шоссе", "наб", "набережная", "бульвар",
}


@dataclass(frozen=True)
class AddressPoint:
    latitude: float
    longitude: float
    address: str


def street_name(value):
    words = re.findall(r"[^\W_]+", value.casefold().replace("ё", "е"))
    return " ".join(word for word in words if word not in STREET_TYPES)


def coordinates(item, nodes, ways):
    if item.tag == "node":
        return float(item.attrib["lon"]), float(item.attrib["lat"])
    if item.tag == "way":
        rings = [item]
    else:
        rings = [ways[member.attrib["ref"]] for member in item.findall("member")
                 if member.get("type") == "way" and member.get("role") == "outer"
                 and member.get("ref") in ways]
    lines = []
    for ring in rings:
        refs = [node.attrib["ref"] for node in ring.findall("nd")]
        if len(refs) >= 2 and all(ref in nodes for ref in refs):
            lines.append(LineString([nodes[ref] for ref in refs]))
    polygon = max(polygonize(lines), key=lambda shape: shape.area, default=None)
    if polygon is None:
        return None
    point = polygon.representative_point()
    return point.x, point.y


@lru_cache(maxsize=1)
def address_index():
    addresses = {}
    for filename in OSM_FILES:
        with gzip.open(OSM_DIRECTORY / filename) as source:
            root = ElementTree.parse(source).getroot()
        nodes = {node.attrib["id"]: (float(node.attrib["lon"]), float(node.attrib["lat"]))
                 for node in root.findall("node")}
        ways = {way.attrib["id"]: way for way in root.findall("way")}
        for item in root:
            tags = {tag.attrib["k"]: tag.attrib["v"] for tag in item.findall("tag")}
            street, house = tags.get("addr:street", "").strip(), tags.get("addr:housenumber", "").strip()
            label = f"{street}, {house}, {CITY}"
            if not street or not house or tags.get("addr:city", CITY).casefold() != CITY.casefold() or len(label) > 240:
                continue
            point = coordinates(item, nodes, ways)
            if point is None:
                continue
            entries = addresses.setdefault(street_name(street), {}).setdefault(house.casefold(), {})
            key = label.casefold()
            # Prefer the footprint of the building to a business tagged at the same address.
            priority = 2 if "building" in tags and item.tag != "node" else 1 if item.tag != "node" else 0
            if key not in entries or priority > entries[key][0]:
                longitude, latitude = point
                entries[key] = (priority, AddressPoint(latitude, longitude, label))
    if not addresses:
        raise ValueError("OSM address index is empty")
    return addresses


def find_addresses(street, house):
    requested = street_name(street)
    house = house.casefold()
    index = address_index()
    if requested in index:
        streets = [requested]
    else:
        streets = [name for name in index if SequenceMatcher(None, requested, name).ratio() >= 0.75]
    return sorted(
        (point for name in streets for _, point in index[name].get(house, {}).values()),
        key=lambda point: point.address,
    )
