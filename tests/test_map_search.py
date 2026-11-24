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
