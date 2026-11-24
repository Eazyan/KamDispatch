from shapely.geometry import Point
from shapely.ops import linemerge

from monitoring.map_search import search, street_paths


def test_street_paths_merge_connected_edges_and_keep_gaps():
    west, middle, east = (158.60, 53.0), (158.61, 53.0), (158.62, 53.0)
    separate = [(158.63, 53.0), (158.64, 53.0)]
    paths = street_paths([[west, middle], [middle, west], [middle, east], separate])

    assert linemerge(paths).equals(linemerge([[west, middle, east], separate]))
    assert len(paths) == 2


def test_leninskaya_uses_graph_and_finds_building_from_atlas():
    street = next(hit for hit in search("Ленинская", [], [], []) if hit["type"] == "street")
    assert street["name"] == "Ленинская улица"
    assert linemerge(street["paths"]).length > 0.01

    hits = search("Ленинская 8", [], [], [])
    addresses = [hit for hit in hits if hit["type"] == "address"]
    assert [hit["name"] for hit in addresses] == ["Ленинская улица, 8"]
    assert linemerge(street["paths"]).distance(Point(addresses[0]["coordinates"])) < 0.001
    assert not any(hit["type"] == "street" for hit in hits)


def test_avenue_with_number_in_name_is_searchable():
    assert any(hit["type"] == "street" and "50 лет Октября" in hit["name"]
               for hit in search("проспект 50 лет Октября", [], [], []))


def test_bus_search_distinguishes_vehicle_from_route():
    fleet = [
        {"id": "a", "fleet_number": "20", "route": "4", "lon": 158.6, "lat": 53.0},
        {"id": "b", "route": "20", "lon": 158.61, "lat": 53.01},
        {"id": "c", "route": "20", "lon": 158.62, "lat": 53.02},
        {"id": "d", "route": "120", "lon": 158.63, "lat": 53.03},
    ]

    hits = search("20 авто", [], fleet, [])
    assert [hit["id"] for hit in hits] == ["a", "b", "c"]
    assert hits[0]["name"] == "Автобус № 20"
    assert hits[1]["route"] == "20" and hits[1]["coordinates"] == (158.61, 53.01)
    assert [hit["id"] for hit in search("маршрут №20", [], fleet, [])] == ["b", "c"]
    assert [hit["id"] for hit in search("бортовой номер 20", [], fleet, [])] == ["a"]
    assert len(search("автобусы", [], fleet, [])) == 4
    assert search("борт 120", [], fleet, []) == []


def test_bus_search_finds_numbered_demo_vehicle():
    from monitoring.bus_demo import map_demo

    hits = search("20 авто", [], map_demo()["positions"], [])
    assert [(hit["name"], hit["route"]) for hit in hits] == [("Автобус № 20", "Д1")]
