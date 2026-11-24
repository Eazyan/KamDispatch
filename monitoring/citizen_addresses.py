from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
import gzip
from importlib import import_module
import re
import unicodedata
from xml.etree import ElementTree

from shapely.geometry import LineString
from shapely.ops import polygonize

from .config import ROOT


CITY = "Петропавловск-Камчатский"
OSM_DIRECTORY = ROOT / "city-sim" / "data" / "pkc"
OSM_FILES = ("city.osm.gz", "depot-east.osm.gz")
OSM_ATLAS = ROOT / "city-sim" / "weather_coverage" / "osm"
SEARCH_OSM = ROOT / "monitoring" / "data" / "search-city.osm.gz"
STREET_TYPES = {
    "ул", "улица", "пр", "проспект", "просп", "пер", "переулок", "пл", "площадь",
    "ш", "шоссе", "наб", "набережная", "бул", "бульвар", "пркт", "мкр", "микрорайон",
}
EDIT_DISTANCE = import_module("city-sim.shared.universal_search.text").edit_distance
KEYBOARD = str.maketrans("qwertyuiop[]asdfghjkl;'zxcvbnm,.`", "йцукенгшщзхъфывапролджэячсмитьбюё")
HOUSE_LETTERS = str.maketrans("abcehkmoptxy", "авсенкмортху")
HOUSE_PART = r"[0-9]+(?:\s*[а-яa-z](?![а-яa-z0-9]))?"
BUILDING = r"с(?:т(?:р(?:оение)?)?)?\.?"
STRUCTURE = r"соор(?:ужение)?\.?"
ESTATE = r"вл(?:адение)?\.?"
CORPUS = r"к(?:орп(?:ус)?)?\.?"
LETTER = r"лит(?:ера)?\.?"
HOUSE_SUFFIX = rf"(?:\s*(?:[/\-]\s*(?:{HOUSE_PART}|[а-яa-z])|(?:{CORPUS}|{BUILDING}|{STRUCTURE})\s*{HOUSE_PART}|{LETTER}\s*[а-яa-z]))"
HOUSE_NUMBER = rf"(?:(?:{ESTATE}|{STRUCTURE})\s*)?{HOUSE_PART}{HOUSE_SUFFIX}*|гск\s*{HOUSE_PART}\s*,\s*{HOUSE_PART}"
HOUSE_MARKER = r"(?:д(?:ом)?\.?(?![а-яa-z])\s*|№\s*)?"
CITY_QUALIFIER = re.compile(r"(?<!\w)(?:г(?:ород)?\.?\s+)?петропавловск[\s‐‑–—-]+камчатский(?!\w)|(?<!\w)камчатский край(?!\w)")
PLACE_TAGS = {
    "amenity", "shop", "tourism", "leisure", "office", "healthcare", "historic", "public_transport",
}


@dataclass(frozen=True)
class AddressPoint:
    latitude: float
    longitude: float
    address: str


def street_name(value):
    value = fold(value)
    value = re.sub(r"\bпр\s*-\s*(?:кт|т)\b", "проспект", value)
    value = re.sub(r"\bакад\b", "академика", value)
    words = re.findall(r"[^\W_]+", value)
    name = [word for word in words if word not in STREET_TYPES]
    if not name and "набережная" in words:
        name = ["набережная"]
    return " ".join(name)


def fold(value):
    return unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")


def house_key(value):
    value = fold(value).strip().replace("\\", "/")
    if not re.fullmatch(HOUSE_NUMBER, value):
        return value
    for form, canonical in ((CORPUS, "к"), (BUILDING, "стр"), (STRUCTURE, "соор"), (ESTATE, "вл")):
        value = re.sub(rf"\s*{form}\s*(?=[0-9])", canonical, value)
    value = re.sub(rf"\s*{LETTER}\s*(?=[а-яa-z])", "лит", value)
    return re.sub(r"\s+", "", value).translate(HOUSE_LETTERS)


def address_query(query):
    value = CITY_QUALIFIER.sub(" ", fold(query.replace("№", "д ").replace("\\", "/")))
    value = re.sub(r"^\s*адрес\b", "", value).strip(" ,.")
    match = re.fullmatch(rf"(.+?),\s*{HOUSE_MARKER}({HOUSE_NUMBER})", value)
    if match is None:
        match = re.fullmatch(rf"(.+?)\s+{HOUSE_MARKER}({HOUSE_NUMBER})", value)
    if match is None:
        reversed_match = re.fullmatch(rf"{HOUSE_MARKER}({HOUSE_NUMBER})[,\s]+(.+)", value)
        if reversed_match is None:
            return None
        house, street = reversed_match.groups()
    else:
        street, house = match.groups()
    key = street_name(street)
    if any(char.isalpha() for char in key):
        return key, house_key(house)
    return None


def street_candidates(requested, keys, *, prefixes=False):
    if requested in keys:
        return [requested]
    if re.fullmatch(r"[a-z\s0-9]+", requested):
        from_keyboard = requested.translate(KEYBOARD)
        if from_keyboard in keys:
            return [from_keyboard]
        requested = from_keyboard
    numeric = re.findall(r"\d+", requested)
    candidates = []
    for key in keys:
        if re.findall(r"\d+", key) != numeric:
            continue
        if prefixes and len(requested) >= 3 and key.startswith(requested):
            candidates.append((0, len(key), key))
            continue
        max_distance = 2 if len(requested) >= 9 else 1 if len(requested) >= 4 else 0
        if abs(len(key) - len(requested)) <= max_distance:
            distance = EDIT_DISTANCE(requested, key)
            if distance <= max_distance:
                candidates.append((distance, len(key), key))
    return [key for _, _, key in sorted(candidates)]


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
def osm_catalog():
    addresses = {}
    places = {}
    streets = defaultdict(list)
    files = [OSM_DIRECTORY / name for name in OSM_FILES]
    files.extend(path for path in sorted(OSM_ATLAS.glob("*.osm.gz"))
                 if path.name not in {"city-boundary.osm.gz", "stops.osm.gz"})
    files.append(SEARCH_OSM)
    for path in files:
        with gzip.open(path) as source:
            root = ElementTree.parse(source).getroot()
        nodes = {node.attrib["id"]: (float(node.attrib["lon"]), float(node.attrib["lat"]))
                 for node in root.findall("node")}
        ways = {way.attrib["id"]: way for way in root.findall("way")}
        for item in root:
            tags = {tag.attrib["k"]: tag.attrib["v"] for tag in item.findall("tag")}
            street = (tags.get("addr:street") or tags.get("addr:place") or "").strip()
            house = tags.get("addr:housenumber", "").strip()
            label = f"{street}, {house}, {CITY}"
            if fold(tags.get("addr:city") or CITY).strip() != fold(CITY):
                continue
            is_address = bool(street and house and len(label) <= 240)
            name = (tags.get("name:ru") or tags.get("name") or "").strip()
            if path == SEARCH_OSM and item.tag == "way" and name and "highway" in tags:
                refs = [ref.get("ref") for ref in item.findall("nd")]
                if len(refs) >= 2 and all(ref in nodes for ref in refs):
                    streets[name].append([nodes[ref] for ref in refs])
            is_place = bool(name and PLACE_TAGS.intersection(tags))
            if not (is_address or is_place):
                continue
            point = coordinates(item, nodes, ways)
            if point is None:
                continue
            if is_place:
                identifier = f'osm:{item.tag}:{item.attrib["id"]}'
                aliases = tuple(tags[k] for k in ("alt_name", "official_name", "short_name", "brand", "name:en")
                                if tags.get(k))
                places.setdefault(identifier, (name, point, aliases))
            if not is_address:
                continue
            for number in house.split(";"):
                number = number.strip()
                if not number:
                    continue
                canonical = house_key(number)
                entries = addresses.setdefault(street_name(street), {}).setdefault(canonical, {})
                key = fold(street), canonical
                # Prefer a building footprint to a business at the same address.
                priority = 2 if "building" in tags and item.tag != "node" else 1 if item.tag != "node" else 0
                if key not in entries or priority > entries[key][0]:
                    longitude, latitude = point
                    entries[key] = (priority, AddressPoint(latitude, longitude, f"{street}, {number}, {CITY}"))
    if not addresses:
        raise ValueError("OSM address index is empty")
    return addresses, places, streets


def address_index():
    return osm_catalog()[0]


def find_addresses(street, house):
    requested = street_name(street)
    return lookup_addresses(requested, house_key(house))


def lookup_addresses(street, house, *, known_streets=(), prefixes=False):
    index = address_index()
    streets = street_candidates(street, index.keys() | set(known_streets), prefixes=prefixes)
    return [
        point
        for name in streets
        for _, point in sorted(index.get(name, {}).get(house, {}).values(), key=lambda row: row[1].address)
    ]
