"""Source traffic lines must be extracted independently of OSM geometry."""
import math

import pytest
from PIL import Image, ImageDraw

pytest.importorskip("numpy", reason="Install requirements-traffic.txt for the optional traffic experiment")
pytest.importorskip("cv2", reason="Install requirements-traffic.txt for line extraction")


def test_short_displaced_traffic_line_matches_parallel_road_without_coloring_crossing_alley():
    from monitoring import traffic_image

    west, east, north, south = 158.0, 158.012, 53.01, 53.0
    top = math.asinh(math.tan(math.radians(north)))
    bottom = math.asinh(math.tan(math.radians(south)))

    def coordinate(x, y):
        lon = west + (x - 10) / 220 * (east - west)
        projected_lat = top + (y - 10) / 160 * (bottom - top)
        return [lon, math.degrees(math.atan(math.sinh(projected_lat)))]

    capture = {
        "control_points": [
            {"pixel": [x, y], "lon_lat": coordinate(x, y)}
            for x, y in ((10, 10), (230, 10), (10, 170), (230, 170))
        ],
        "analysis_region": [10, 10, 220, 160],
        "captured_at": "2026-11-22T00:00:00+00:00",
    }
    before = Image.new("RGB", (240, 180), "white")
    # A stable green map decoration must not count as a traffic observation.
    ImageDraw.Draw(before).line((30, 145, 210, 145), fill=(120, 193, 103), width=5)
    after = before.copy()
    # Twenty-five pixels of thin traffic ribbon, nine pixels off the OSM road axis.
    ImageDraw.Draw(after).line((75, 71, 100, 71), fill=(230, 190, 50), width=3)
    roads = [
        {"id": "parallel-short-road", "coordinates": [coordinate(75, 80), coordinate(100, 80)]},
        {"id": "crossing-alley", "coordinates": [coordinate(90, 45), coordinate(90, 120)]},
        {"id": "stable-green-basemap", "coordinates": [coordinate(30, 145), coordinate(210, 145)]},
    ]

    result = traffic_image.analyze_roads(before, after, capture, roads)
    features = {feature["id"]: feature for feature in result["features"]}
    assert features["parallel-short-road"]["properties"]["traffic_status"] == "slow", (
        "A short traffic ribbon displaced from the OSM axis must match by longitudinal alignment"
    )
    for road_id in ("crossing-alley", "stable-green-basemap"):
        assert features[road_id]["properties"]["traffic_status"] == "unknown", (
            f"{road_id}: proximity at a crossing or unchanged green basemap is not traffic evidence"
        )
    assert features["parallel-short-road"]["properties"]["direction"] == "unresolved"

    # Extraction has no OSM input and its geographic output survives an empty road set.
    source_lines = traffic_image.extract_traffic_lines(before, after, capture["analysis_region"])
    assert source_lines, "The original colored ribbon must remain available for inspection"
    assert any(line["traffic_status"] == "slow" for line in source_lines)
    without_osm = traffic_image.analyze_roads(before, after, capture, [])
    assert result["traffic_lines"]["type"] == "FeatureCollection"
    assert result["traffic_lines"]["features"], "Source-line geometry must not be replaced by OSM roads"
    assert result["traffic_lines"] == without_osm["traffic_lines"], (
        "Changing the OSM road set must not change the extracted source traffic lines"
    )


def test_compact_logo_is_not_a_traffic_line():
    from monitoring.traffic_image import extract_traffic_lines
    before = Image.new("RGB", (120, 100), "white")
    after = before.copy()
    ImageDraw.Draw(after).ellipse((40, 30, 62, 52), fill=(120, 193, 103))
    assert extract_traffic_lines(before, after, [0, 0, 120, 100]) == []


def test_two_competing_parallel_roads_are_not_both_painted():
    from monitoring.traffic_image import analyze_roads
    def coordinate(x,y):
        return [158+x/10000,math.degrees(math.atan(math.sinh(1.1-y/10000)))]
    before=Image.new("RGB",(200,160),"white")
    capture={"control_points":[{"pixel":[x,y],"lon_lat":coordinate(x,y)}
                               for x,y in ((10,10),(190,10),(10,150),(190,150))],
             "analysis_region":[0,0,200,160],"captured_at":"2026-11-22T00:00:00Z"}
    road={"coordinates":[coordinate(30,80),coordinate(170,80)]}
    after = before.copy()
    ImageDraw.Draw(after).line((30, 82, 170, 82), fill=(120, 193, 103), width=1)
    first = {"id":"first","coordinates":road['coordinates']}
    # Both candidates are equally close and aligned to the same source line.
    def offset(point):
        lon,lat=point
        projected=math.asinh(math.tan(math.radians(lat))) - 4/10000
        return [lon,math.degrees(math.atan(math.sinh(projected)))]
    second = {"id":"second","coordinates":[offset(p) for p in road['coordinates']]}
    result=analyze_roads(before,after,capture,[first,second])
    assert all(f['properties']['traffic_status']=='unknown' for f in result['features'])
