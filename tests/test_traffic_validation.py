"""Offline guards for calibration, ambiguous evidence and capture integrity."""
from copy import deepcopy
import math

from PIL import Image, ImageDraw
import pytest

pytest.importorskip("numpy", reason="Install requirements-traffic.txt for the optional traffic experiment")
pytest.importorskip("cv2", reason="Install requirements-traffic.txt for line extraction")

from monitoring.traffic_image import analyze_roads, calibration


def scene():
    def coordinate(x, y):
        return [158 + x / 10000, math.degrees(math.atan(math.sinh(1.1 - y / 10000)))]
    capture = {"control_points": [{"pixel": [x, y], "lon_lat": coordinate(x, y)}
                                  for x, y in ((10, 10), (190, 10), (10, 150), (190, 150))],
               "analysis_region": [0, 0, 200, 160], "captured_at": "2026-11-22T00:00:00Z"}
    before = Image.new("RGB", (200, 160), "white")
    road = {"id": "road", "coordinates": [coordinate(30, 80), coordinate(170, 80)]}
    return before, capture, road


@pytest.mark.parametrize("color,status", [((120, 193, 103), "free"), ((240, 185, 55), "slow"),
                                         ((230, 70, 70), "congested")])
def test_new_continuous_traffic_colors_are_display_classes_not_speeds(color, status):
    before, capture, road = scene()
    after = before.copy()
    ImageDraw.Draw(after).line((30, 80, 170, 80), fill=color, width=5)
    props = analyze_roads(before, after, capture, [road])["features"][0]["properties"]
    assert props["traffic_status"] == status
    assert props["direction"] == "unresolved"
    assert props["provider_updated_at"] is None
    assert "speed" not in props


def test_mixed_opposite_colors_remain_ambiguous():
    before, capture, road = scene()
    after = before.copy()
    draw = ImageDraw.Draw(after)
    draw.line((30, 77, 170, 77), fill=(120, 193, 103), width=3)
    draw.line((30, 83, 170, 83), fill=(230, 70, 70), width=3)
    props = analyze_roads(before, after, capture, [road])["features"][0]["properties"]
    assert props["traffic_status"] == "unknown"


def test_fourth_control_point_is_an_independent_check():
    _, capture, _ = scene()
    capture["control_points"][3]["pixel"][0] += 8
    with pytest.raises(ValueError, match="one pixel"):
        calibration(capture)


def test_collinear_and_nonfinite_control_points_fail():
    _, capture, _ = scene()
    invalid = deepcopy(capture)
    invalid["control_points"][2] = invalid["control_points"][1]
    with pytest.raises(ValueError, match="collinear"):
        calibration(invalid)
    capture["control_points"][0]["lon_lat"][0] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        calibration(capture)


def test_image_dimensions_and_region_must_match():
    before, capture, road = scene()
    with pytest.raises(ValueError, match="equal dimensions"):
        analyze_roads(before, Image.new("RGB", (201, 160)), capture, [road])
    capture["analysis_region"] = [0, 0, 201, 160]
    with pytest.raises(ValueError, match="region"):
        analyze_roads(before, before, capture, [road])


def test_unseen_remainder_of_road_is_not_assumed_free():
    before, capture, road = scene()
    after = before.copy()
    ImageDraw.Draw(after).line((30, 80, 170, 80), fill=(120, 193, 103), width=5)
    capture["analysis_region"] = [0, 0, 75, 160]
    props = analyze_roads(before, after, capture, [road])["features"][0]["properties"]
    assert props["traffic_status"] == "unknown"
    assert props["coverage_fraction"] < .6


def test_capture_analysis_rejects_modified_screenshot(tmp_path):
    from scripts.capture_traffic import analyze, digest, write_json
    before, capture, road = scene()
    before.save(tmp_path / "traffic-off.png")
    before.save(tmp_path / "traffic-on.png")
    capture.update(schema_version=1, source_url="https://2gis.ru/p_kamchatskiy",
                   image_sha256={name: digest(tmp_path / name) for name in ("traffic-off.png", "traffic-on.png")})
    write_json(tmp_path / "capture.json", capture)
    before.putpixel((10, 10), (0, 0, 0))
    before.save(tmp_path / "traffic-on.png")
    roads_file = tmp_path / "roads.json"
    write_json(roads_file, {"segments": [road]})
    with pytest.raises(ValueError, match="hash mismatch"):
        analyze(tmp_path, roads_file)
    assert not (tmp_path / "roads.geojson").exists()


def test_thin_continuous_ribbon_is_evidence_but_text_is_not():
    before, capture, road = scene()
    after = before.copy()
    ImageDraw.Draw(after).line((30, 80, 170, 80), fill=(240, 185, 55), width=1)
    assert analyze_roads(before, after, capture, [road])["features"][0]["properties"]["traffic_status"] == "slow"
    text = before.copy()
    ImageDraw.Draw(text).text((60, 75), "Cafe", fill=(240, 185, 55))
    assert analyze_roads(before, text, capture, [road])["features"][0]["properties"]["traffic_status"] == "unknown"


def test_device_pixel_screenshots_cannot_use_css_control_points():
    before, capture, road = scene()
    capture["viewport"] = [200, 160]
    retina = before.resize((400, 320))
    with pytest.raises(ValueError, match="CSS viewport"):
        analyze_roads(retina, retina, capture, [road])
