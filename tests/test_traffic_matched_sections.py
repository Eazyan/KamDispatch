"""Retain localized evidence without extending it to an unobserved OSM road."""
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")
from monitoring.traffic_image import match_traffic_lines


def test_partial_road_keeps_observed_sections_without_assuming_unseen_remainder_free():
    lines = [{"id": "green", "traffic_status": "free", "pixels": [[30, 82], [80, 82]]}]
    result = match_traffic_lines(lines, [np.array([[30., 80.], [180., 80.]])],
                                [{"id": "road"}], [0, 0, 200, 160])[0]
    assert result["traffic_status"] == "unknown"
    sections = result["matched_sections"]
    assert sections, "A partial road must retain its locally observed traffic geometry"
    assert all(s["traffic_status"] == "free" for s in sections)
    points = [p for s in sections for p in s["pixels"]]
    assert min(p[0] for p in points) >= 30
    assert 75 <= max(p[0] for p in points) <= 84
    assert all(p[1] == 80 for p in points)


def test_line_beyond_short_road_endpoint_cannot_paint_that_road():
    lines = [{"id": "beyond", "traffic_status": "free", "pixels": [[68, 80], [95, 80]]}]
    result = match_traffic_lines(lines, [np.array([[55., 80.], [60., 80.]])],
                                [{"id": "short"}], [0, 0, 200, 160])[0]
    assert result["traffic_status"] == "unknown", "Evidence beyond an endpoint is not evidence on the road"
    assert not result["matched_sections"]


def test_matched_sections_do_not_bridge_unobserved_gap_or_leave_analysis_region():
    lines = [{"id": str(i), "traffic_status": "free", "pixels": points} for i, points in enumerate(
        ([[20, 82], [60, 82]], [[100, 82], [140, 82]]))]
    result = match_traffic_lines(lines, [np.array([[0., 80.], [180., 80.]])],
                                [{"id": "road"}], [15, 10, 135, 140])[0]
    sections = result["matched_sections"]
    assert len(sections) == 2
    assert max(p[0] for p in sections[0]["pixels"]) <= 64
    assert min(p[0] for p in sections[1]["pixels"]) >= 96
    assert all(15 <= p[0] < 150 for s in sections for p in s["pixels"])


@pytest.mark.parametrize("mirror", [False, True])
def test_endpoint_does_not_steal_evidence_from_actual_continuation(mirror):
    def points(items):
        return np.array([[100-x if mirror else x,y] for x,y in items],dtype=float)
    roads = [points([(20,80),(24,80)]), points([(24,85),(34,85)])]
    lines = [{"id":"signal", "traffic_status":"free", "pixels":points([(27,80),(34,80)]).tolist()}]
    result = match_traffic_lines(lines, roads, [{"id":"ended"},{"id":"continuation"}], [0,0,120,120])
    assert result[0]["traffic_status"] == "unknown"
    assert result[1]["traffic_status"] == "free"
