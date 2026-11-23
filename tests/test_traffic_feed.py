from copy import deepcopy

import pytest

from monitoring.traffic_feed import project


GRAPH = {
    "graph_sha256": "fixture",
    "segments": [{"id": "road", "length_m": 100, "coordinates": [[158, 53], [159, 53], [160, 53]]}],
}
AT = "2026-11-23T23:00:00+00:00"
PUBLICATION = {
    "graph_sha256": "fixture",
    "published_at": "2026-11-23T23:02:00+00:00",
    "status": "partial",
    "source": "yandex_maps_ui",
    "segments": {
        "road": [
            {"status": "unknown", "interval_m": [0, 25], "timestamp": None},
            {"status": "slow", "interval_m": [25, 75], "timestamp": AT},
            {"status": "free", "interval_m": [75, 100.00000000000001], "timestamp": AT},
            {"status": "free", "interval_m": [100, 100.00000000000001], "timestamp": AT},
        ]
    },
}


def test_projection_keeps_only_observed_subsections_and_tolerates_roundoff():
    result = project(PUBLICATION, GRAPH)
    assert result["status"] == "partial"
    assert result["valid_until"] == "2026-11-23T23:10:00+00:00"
    first, second = result["segments"]
    assert first == {
        "corridor_id": "road", "status": "slow", "coordinates": [[158.5, 53], [159, 53], [159.5, 53]]
    }
    assert second["coordinates"] == [[159.5, 53], [160, 53]]


def test_projection_rejects_an_unknown_graph_and_out_of_bounds_observations():
    with pytest.raises(ValueError, match="different graph"):
        project({**PUBLICATION, "graph_sha256": "different"}, GRAPH)
    publication = deepcopy(PUBLICATION)
    publication["segments"]["road"][1]["interval_m"] = [25, 101]
    with pytest.raises(ValueError, match="Invalid traffic interval"):
        project(publication, GRAPH)


def test_fills_short_gap_at_midpoint_and_preserves_observed_intervals():
    publication = deepcopy(PUBLICATION)
    publication["segments"]["road"][1]["interval_m"] = [25, 55]
    result = project(publication, GRAPH)
    assert [row["status"] for row in result["segments"]] == ["slow", "free", "slow", "free"]
    first, second = result["segments"][2:]
    assert first["estimated"] and second["estimated"]
    assert first["coordinates"] == [[159.1, 53], [159.3, 53]]
    assert second["coordinates"] == [[159.3, 53], [159.5, 53]]


def test_fills_across_reversed_edges_with_no_guess_at_intersections():
    roads = {
        "graph_sha256": "fixture",
        "segments": [
            {"id": "a", "nodes": (1, 2), "length_m": 100, "coordinates": [[156, 53], [157, 53]]},
            {"id": "b", "nodes": (3, 2), "length_m": 20, "coordinates": [[158, 53], [157, 53]]},
            {"id": "c", "nodes": (3, 4), "length_m": 100, "coordinates": [[158, 53], [159, 53]]},
        ],
    }
    latest = {**PUBLICATION, "segments": {
        "a": [{"status": "free", "interval_m": [0, 80], "timestamp": AT}],
        "c": [{"status": "free", "interval_m": [20, 100], "timestamp": AT}],
    }}
    estimated = [row for row in project(latest, roads)["segments"] if row.get("estimated")]
    assert [row["corridor_id"] for row in estimated] == ["a", "b", "c"]
    assert all(row["status"] == "free" for row in estimated)
    assert estimated[1]["coordinates"] == [[158, 53], [157, 53]]
    roads["segments"][1]["length_m"] = 100
    assert not any(row.get("estimated") for row in project(latest, roads)["segments"])
    roads["segments"][1]["length_m"] = 20
    roads["segments"].append(
        {"id": "spur", "nodes": (2, 5), "length_m": 8, "coordinates": [[157, 53], [157, 54]]}
    )
    assert not any(row.get("estimated") for row in project(latest, roads)["segments"])
