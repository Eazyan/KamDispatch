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
