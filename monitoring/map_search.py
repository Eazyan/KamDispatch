"""Search the local OSM address catalogue and the selected dashboard run."""

from collections import defaultdict
from functools import lru_cache
from importlib import import_module
import json
import re
from threading import Lock

from shapely.ops import linemerge

from .citizen_addresses import address_index, address_query, lookup_addresses, osm_catalog, street_candidates, street_name
from .dispatch_queue import road_index
from .traffic_feed import GRAPH_FILE

_search = import_module("city-sim.shared.universal_search")
_text = import_module("city-sim.shared.universal_search.text")
Entity, SearchIndex = _search.Entity, _search.SearchIndex
SOURCE = "eazyan-map"
SEGMENT = re.compile(r"участок (?:osm )?([0-9]+)")
STATIC_INDEX_LOCK = Lock()

PAGES = {
    "overview": ("Дорожная обстановка", ("карта пробки трафик снег",)),
    "dispatch": ("Диспетчерская", ("задания работы бригады уборка",)),
    "citizens": ("Обращения жителей", ("обращение жалобы заявки жители",)),
    "cameras": ("Камеры", ("камера видеонаблюдение трансляции",)),
    "forecast": ("Прогноз", ("погода снегопад осадки наносы",)),
    "reports": ("Сводки и доставка", ("отчёты сводка уведомления",)),
}
CATEGORY_WORDS = {
    "камеры": "камера", "камер": "камера",
    "автобусы": "автобус", "автобусов": "автобус", "маршруты": "маршрут",
    "авто": "автобус", "бус": "автобус", "бусик": "автобус",
    "задания": "задание", "обращения": "событие", "обращение": "событие",
    "события": "событие", "адреса": "адрес",
}


@lru_cache(maxsize=1)
def streets():
    with GRAPH_FILE.open(encoding="utf-8") as source:
        edges = json.load(source)["edges"]
    graph_groups = defaultdict(list)
    for edge in edges:
        name = (edge.get("name") or "").strip()
        if name and len(edge["geometry"]) >= 2:
            graph_groups[_text.normalize(name)].append(edge)

    fallback_groups = defaultdict(list)
    for road in road_index().values():
        key = _text.normalize(road["name"])
        if key and key not in graph_groups and len(road["coordinates"]) >= 2:
            fallback_groups[key].append(road)

    result = {}
    for key, group in graph_groups.items():
        main = [edge for edge in group if edge["highway"] != "service"
                and not edge["highway"].endswith("_link")]
        result[key] = {"name": group[0]["name"], "paths": street_paths(
            edge["geometry"] for edge in (main or group)
        )}
    for key, group in fallback_groups.items():
        result[key] = {"name": group[0]["name"], "paths": street_paths(
            road["coordinates"] for road in group
        )}
    known = {street_name(road["name"]) for road in result.values()}
    for name, paths in osm_catalog()[2].items():
        if street_name(name) not in known:
            result[_text.normalize(name)] = {"name": name, "paths": street_paths(paths)}
    return result


def street_paths(lines):
    unique = {}
    for coordinates in lines:
        path = tuple(map(tuple, coordinates))
        unique.setdefault(min(path, path[::-1]), path)
    merged = linemerge(list(unique.values()))
    parts = [merged] if merged.geom_type == "LineString" else merged.geoms
    return [list(part.coords) for part in parts]


@lru_cache(maxsize=1)
def named_streets():
    result = {}
    for key, road in streets().items():
        if not SEGMENT.fullmatch(key):
            result.setdefault(street_name(road["name"]), []).append(key)
    return result


def street_hit(key, *, label=None):
    road = streets()[key]
    return {"id": key, "type": "street", "name": road["name"], "coordinates": None,
            "paths": road["paths"], "page": None, **({"label": label} if label else {})}


def address_hits(street, house):
    names = named_streets()
    points = lookup_addresses(street, house, known_streets=names, prefixes=True)
    if points:
        hits = []
        for point in points[:5]:
            label = point.address.rsplit(", ", 1)[0]
            hits.append({"id": label.casefold(), "type": "address", "name": label,
                         "coordinates": (point.longitude, point.latitude), "page": None})
        return hits
    candidates = street_candidates(street, names)
    if len(candidates) == 1 and len(names[candidates[0]]) == 1:
        return [street_hit(names[candidates[0]][0], label="Дом не найден в справочнике")]
    return []


def resident_addresses(query):
    parsed = address_query(query)
    if not parsed:
        return {"local": False, "matches": []}
    street, house = parsed
    names = named_streets()
    points = lookup_addresses(street, house, known_streets=names, prefixes=True)
    return {"local": bool(points or street in names or street in address_index()), "matches": points[:5]}


def bus_hit(bus):
    number = bus.get("fleet_number")
    return {
        "id": bus["id"], "type": "bus", "name": f"Автобус № {number}" if number else "Автобус",
        "coordinates": (bus["lon"], bus["lat"]), "route": bus.get("route"),
    }


def search_buses(words, buses):
    route_only = "маршрут" in words
    fleet_only = any(word in {"борт", "бортовой"} for word in words)
    cues = {"автобус", "маршрут", "борт", "бортовой", "номер"}
    terms = tuple(word for word in words if word not in cues)
    if not terms:
        return buses
    fleet = [bus for bus in buses if _text.terms(bus.get("fleet_number") or "") == terms]
    if fleet_only:
        return fleet
    routes = [bus for bus in buses if _text.terms(bus.get("route") or "") == terms]
    if route_only:
        return routes
    return fleet + [bus for bus in routes if bus not in fleet]


@lru_cache(maxsize=1)
def page_entities():
    return [Entity(key, "base", label, SOURCE, aliases=aliases) for key, (label, aliases) in PAGES.items()]


@lru_cache(maxsize=1)
def static_index():
    roads = streets()
    entities = [Entity(key, "street", roads[key]["name"], SOURCE) for keys in named_streets().values() for key in keys]
    entities.extend(Entity(key, "poi", name, SOURCE, point, aliases)
                    for key, (name, point, aliases) in osm_catalog()[1].items())
    index = SearchIndex(check_same_thread=False)
    try:
        index.replace_source(SOURCE, [*entities, *page_entities()])
    except Exception:
        index.close()
        raise
    return index


def search(query, cameras, buses, dispatch, reports=()):
    raw = _text.normalize(query)
    normalized = [CATEGORY_WORDS.get(word, word) for word in raw.split()]
    if set(normalized) & {"автобус", "маршрут", "борт", "бортовой"}:
        return [bus_hit(bus) for bus in search_buses(normalized, list(buses))[:5]]
    buses_by_id = {bus["id"]: bus for bus in buses}
    terms, _, category = _text.interpret(" ".join(normalized))
    roads = streets()
    segment = SEGMENT.fullmatch(raw)
    if segment and (key := "участок osm " + segment[1]) in roads:
        return [street_hit(key)]
    exact_streets = named_streets().get(street_name(query), ())
    parsed_address = address_query(query) if category in (None, "address") and not exact_streets else None
    geographic_hits = []
    if parsed_address:
        geographic_hits = address_hits(*parsed_address)
    elif category is None:
        geographic_hits = [street_hit(key) for key in exact_streets[:5]]
    entities = []
    entities.extend(Entity(row["id"], "camera", row["name"], SOURCE,
                           coordinates=row.get("coordinates"),
                           aliases=tuple(row[key] for key in ("area", "district", "section_name") if row.get(key)))
                    for row in cameras)
    entities.extend(Entity(row["id"], "bus", "Автобус", SOURCE,
                           coordinates=(row["lon"], row["lat"]),
                           aliases=tuple(filter(None, (row.get("route"), row.get("fleet_number")))))
                    for row in buses_by_id.values())
    entities.extend(Entity(row["location_key"] + "/" + row["kind"], "task" if row["task"] else "event",
                           row["address"], SOURCE, aliases=tuple(filter(None, (
                               row.get("district"), *(text for report in row["reports"]
                                                      for text in (report["description"], report["reference"])),
                           ))))
                    for row in dispatch)
    entities.extend(Entity("citizen:" + str(row["id"]), "event",
                           row["address"] or f'Обращение №{row["report_number"]}', SOURCE,
                           coordinates=(row["longitude"], row["latitude"]),
                           aliases=tuple(filter(None, (str(row["report_number"]), row["district"],
                                                       row["description"], *row["tags"]))))
                    for row in reports)
    if category:
        types = {"task", "event"} if category == "event" else {category}
        entities = [row for row in entities if row.type in types]
    hits = []
    if terms:
        normalized_query = " ".join(normalized)
        if category is None and not geographic_hits:
            with STATIC_INDEX_LOCK:
                hits.extend(static_index().search(normalized_query, limit=100)["hits"])
        if entities:
            with SearchIndex() as index:
                index.replace_source(SOURCE, entities)
                hits.extend(index.search(normalized_query, limit=100)["hits"])
        hits.sort(key=lambda hit: -hit["relevance"])
    else:
        hits = [row.dict() for row in entities]
    destinations = {"camera": "cameras", "task": "dispatch", "event": "citizens", "bus": "overview"}
    if not terms and category in destinations:
        hits.append(next(row.dict() for row in page_entities() if row.id == destinations[category]))
    result = geographic_hits
    counts = {}
    for hit in hits:
        kind = hit["type"]
        if counts.get(kind, 0) >= 5:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "bus":
            result.append(bus_hit(buses_by_id[hit["id"]]))
            continue
        result.append({
            "id": hit["id"], "type": "page" if kind == "base" else kind, "name": hit["name"],
            "coordinates": hit["coordinates"],
            **({"paths": roads[hit["id"]]["paths"]} if kind == "street" else {}),
            "page": hit["id"] if kind == "base" else destinations.get(kind),
        })
    return result
