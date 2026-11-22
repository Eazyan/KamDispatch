"""Contract checks for screenshot-derived road-corridor observations."""
import math

import pytest

pytest.importorskip("numpy", reason="Install requirements-traffic.txt for the optional traffic experiment")

from PIL import Image, ImageDraw


def test_unchanged_green_basemap_and_unobserved_roads_are_unknown():
    from monitoring.traffic_image import analyze_roads

    west, east, north, south = 158.0, 158.01, 53.01, 53.0
    top = math.asinh(math.tan(math.radians(north)))
    bottom = math.asinh(math.tan(math.radians(south)))

    def coordinate(x, y):
        lon = west + (x - 10) / 180 * (east - west)
        projected_lat = top + (y - 10) / 140 * (bottom - top)
        return [lon, math.degrees(math.atan(math.sinh(projected_lat)))]

    capture = {
        "control_points": [
            {"pixel": [x, y], "lon_lat": coordinate(x, y)}
            for x, y in ((10, 10), (190, 10), (10, 150), (190, 150))
        ],
        "analysis_region": [10, 10, 180, 140],
        "captured_at": "2026-11-22T00:00:00+00:00",
    }
    before = Image.new("RGB", (200, 160), "white")
    ImageDraw.Draw(before).line((30, 35, 170, 35), fill=(120, 193, 103), width=5)
    after = before.copy()
    ImageDraw.Draw(after).line((30, 80, 170, 80), fill=(120, 193, 103), width=5)
    roads = [
        {"id": name, "name": name, "coordinates": [coordinate(30, y), coordinate(170, y)]}
        for name, y in (("unchanged-green", 35), ("new-traffic-green", 80), ("unobserved", 125))
    ]

    result = analyze_roads(before, after, capture, roads)
    assert result["type"] == "FeatureCollection"
    features = {
        feature.get("id", feature["properties"].get("id")): feature
        for feature in result["features"]
    }
    assert set(features) == {road["id"] for road in roads}, "Every in-frame road must retain its identity"
    for road_id in ("unchanged-green", "unobserved"):
        props = features[road_id]["properties"]
        assert props["traffic_status"] == "unknown", (
            f"{road_id}: an unchanged green basemap or absent traffic pixels cannot prove free flow"
        )
        assert props["coverage_fraction"] == 0
    assert features["new-traffic-green"]["properties"]["traffic_status"] == "free", (
        "A continuous newly displayed green traffic line must remain observable"
    )
    for feature in features.values():
        props = feature["properties"]
        assert props["source"] == "2gis_screenshot"
        assert props["direction"] == "unresolved", "A corridor color must not invent travel direction"
        assert props["evidence_scope"] == "road_corridor"
        assert props["observed_at"] == capture["captured_at"]
