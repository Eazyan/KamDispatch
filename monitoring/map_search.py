"""Map search over the dispatch catalogue and the selected monitoring run."""

from functools import lru_cache
from importlib import import_module

from .dispatch_queue import road_index

_search = import_module("city-sim.shared.universal_search")
Entity, SearchIndex = _search.Entity, _search.SearchIndex
SOURCE = "eazyan-map"


@lru_cache(maxsize=1)
def streets():
    by_name = {}
    for road in road_index().values():
        if road["name"] and road["coordinates"]:
            by_name.setdefault(road["name"].casefold(), road)
    return {road["id"]: road for road in by_name.values()}


def search(query, cameras, buses, dispatch):
    roads = streets()
    entities = [Entity(key, "street", road["name"], SOURCE) for key, road in roads.items()]
    entities.extend(Entity(row["id"], "camera", row["name"], SOURCE,
                           aliases=tuple(row[key] for key in ("area", "district", "section_name") if row.get(key)))
                    for row in cameras)
    entities.extend(Entity(row["id"], "bus", "Маршрут " + row["route"], SOURCE,
                           coordinates=(row["lon"], row["lat"]), aliases=(row["route"],))
                    for row in buses if row.get("route"))
    entities.extend(Entity(row["location_key"] + "/" + row["kind"], "task" if row["task"] else "event",
                           row["address"], SOURCE, aliases=tuple(filter(None, (
                               row.get("district"), *(text for report in row["reports"]
                                                      for text in (report["description"], report["reference"])),
                           ))))
                    for row in dispatch)
    with SearchIndex() as index:
        index.replace_source(SOURCE, entities)
        hits = index.search(query, limit=25)["hits"]
    return [{"id": hit["id"], "type": hit["type"], "name": hit["name"],
             "coordinates": roads[hit["id"]]["coordinates"] if hit["type"] == "street" else hit["coordinates"]}
            for hit in hits]
