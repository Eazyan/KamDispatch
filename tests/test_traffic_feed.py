from copy import deepcopy
from datetime import datetime, timezone

import pytest

from monitoring.traffic_feed import TrafficFeed, classify, project


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
    assert result["valid_until"] == "2026-11-24T00:00:00+00:00"
    first, second = result["segments"]
    assert first == {
        "corridor_id": "road", "status": "slow", "coordinates": [[158.5, 53], [159, 53], [159.5, 53]],
        "observed_at": AT, "valid_until": "2026-11-24T00:00:00+00:00",
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
    assert [row["status"] for row in result["segments"]] == ["slow", "free", "slow"]
    first, = result["segments"][2:]
    assert first["estimated"]
    assert first["coordinates"] == [[159.1, 53], [159.3, 53]]


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
        "a": [{"status": "slow", "interval_m": [0, 80], "timestamp": AT}],
        "c": [{"status": "slow", "interval_m": [20, 100], "timestamp": AT}],
    }}
    estimated = [row for row in project(latest, roads)["segments"] if row.get("estimated")]
    assert [row["corridor_id"] for row in estimated] == ["a", "b", "c"]
    assert all(row["status"] == "slow" for row in estimated)
    assert estimated[1]["coordinates"] == [[158, 53], [157, 53]]
    roads["segments"][1]["length_m"] = 100
    assert not any(row.get("estimated") for row in project(latest, roads)["segments"])
    roads["segments"][1]["length_m"] = 20
    roads["segments"].append(
        {"id": "spur", "nodes": (2, 5), "length_m": 8, "coordinates": [[157, 53], [157, 54]]}
    )
    assert not any(row.get("estimated") for row in project(latest, roads)["segments"])


def test_passes_through_junction_only_along_unique_same_street():
    roads = {"graph_sha256": "fixture", "segments": [
        {"id": "left", "street": "главная", "nodes": (1, 2), "length_m": 150, "coordinates": [[156, 53], [157, 53]]},
        {"id": "right", "street": "главная", "nodes": (2, 3), "length_m": 150, "coordinates": [[157, 53], [158, 53]]},
        {"id": "spur", "street": "боковая", "nodes": (2, 4), "length_m": 20, "coordinates": [[157, 53], [157, 54]]},
    ]}
    latest = {**PUBLICATION, "segments": {
        "left": [{"status": "free", "interval_m": [0, 60], "timestamp": AT}],
        "right": [{"status": "slow", "interval_m": [90, 150], "timestamp": AT}],
    }}
    estimated = [row for row in project(latest, roads)["segments"] if row.get("estimated")]
    assert [(row["corridor_id"], row["status"]) for row in estimated] == [("right", "slow")]
    roads["segments"][2]["street"] = "главная"
    assert not any(row.get("estimated") for row in project(latest, roads)["segments"])


def test_partial_results_expire_per_interval_and_never_infer_an_open_road():
    payload = deepcopy(PUBLICATION)
    payload["segments"]["road"][1]["interval_m"] = [30, 60]
    payload["segments"]["road"][1]["status"] = "free"
    payload["segments"]["road"][2]["timestamp"] = payload["published_at"]
    projected = project(payload, GRAPH, ttl=180)
    output = classify(projected, datetime(2026, 11, 23, 23, 3, tzinfo=timezone.utc))
    assert [row["status"] for row in output["segments"]] == ["stale", "free"]
    assert all(not row.get("estimated") for row in output["segments"])
    assert projected["segments"][0]["status"] == "free"


def test_last_traffic_snapshot_survives_provider_failure(monkeypatch):
    feed = TrafficFeed("http://fixture")
    snapshot = project(PUBLICATION, GRAPH)
    monkeypatch.setattr(feed, "fetch", lambda: snapshot)
    assert feed.latest()["source"]["reason"] is None
    def bad_feed():
        raise ValueError("graph changed")
    monkeypatch.setattr(feed, "fetch", bad_feed)
    feed.next_fetch = 0
    result = feed.latest()
    assert result["source"]["reason"] == "invalid_publication"
    assert all(row["status"] == "stale" for row in result["segments"])
