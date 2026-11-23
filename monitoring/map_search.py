"""Search the local OSM address catalogue and the selected dashboard run."""

from functools import lru_cache
from importlib import import_module

from .citizen_addresses import address_index
from .dispatch_queue import road_index

_search = import_module("city-sim.shared.universal_search")
_text = import_module("city-sim.shared.universal_search.text")
Entity, SearchIndex = _search.Entity, _search.SearchIndex
SOURCE = "eazyan-map"

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
    "задания": "задание", "обращения": "событие", "обращение": "событие",
    "события": "событие", "адреса": "адрес",
}


@lru_cache(maxsize=1)
def streets():
    by_name = {}
    for road in road_index().values():
        if road["name"] and road["coordinates"]:
            by_name.setdefault(road["name"].casefold(), road)
    return {road["id"]: road for road in by_name.values()}


@lru_cache(maxsize=1)
def houses_by_number():
    result = {}
    for houses in address_index().values():
        for house, points in houses.items():
            for _, point in points.values():
                label = point.address.rsplit(", ", 1)[0]
                for number in {house, _text.normalize(house)}:
                    result.setdefault(number, {})[label.casefold()] = Entity(
                        label.casefold(), "address", label, SOURCE, (point.longitude, point.latitude)
                    )
    return result


def search(query, cameras, buses, dispatch, reports=()):
    normalized = [CATEGORY_WORDS.get(word, word) for word in _text.normalize(query).split()]
    terms, intent, category = _text.interpret(" ".join(normalized))
    roads = streets()
    pages = [Entity(key, "base", label, SOURCE, aliases=aliases) for key, (label, aliases) in PAGES.items()]
    entities = [Entity(key, "street", road["name"], SOURCE) for key, road in roads.items()]
    if any(any(char.isdigit() for char in token) for token in terms):
        houses = houses_by_number()
        address_entities = {}
        for end in range(1, len(terms) + 1):
            for start in range(max(0, end - 5), end):
                address_entities.update(houses.get(" ".join(terms[start:end]), {}))
        entities.extend(address_entities.values())
    entities.extend(Entity(row["id"], "camera", row["name"], SOURCE,
                           coordinates=row.get("coordinates"),
                           aliases=tuple(row[key] for key in ("area", "district", "section_name") if row.get(key)))
                    for row in cameras)
    entities.extend(Entity(row["id"], "bus", "Маршрут " + row["route"], SOURCE,
                           coordinates=(row["lon"], row["lat"]), aliases=(row["route"], row["id"]))
                    for row in buses if row.get("route"))
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
    if intent == "address" and not category:
        entities = [row for row in entities if row.type != "street"]
    if category:
        types = {"task", "event"} if category == "event" else {category}
        entities = [row for row in entities if row.type in types]
    with SearchIndex() as index:
        index.replace_source(SOURCE, [*entities, *pages])
        hits = index.search(" ".join(normalized), limit=100)["hits"] if terms else [row.dict() for row in entities]
    destinations = {"camera": "cameras", "task": "dispatch", "event": "citizens", "bus": "overview"}
    if not terms and category in destinations:
        hits.append(next(row.dict() for row in pages if row.id == destinations[category]))
    result = []
    counts = {}
    for hit in hits:
        kind = hit["type"]
        if counts.get(kind, 0) >= 5:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        result.append({
            "id": hit["id"], "type": "page" if kind == "base" else kind, "name": hit["name"],
            "coordinates": roads[hit["id"]]["coordinates"] if kind == "street" else hit["coordinates"],
            "page": hit["id"] if kind == "base" else destinations.get(kind),
        })
    return result
