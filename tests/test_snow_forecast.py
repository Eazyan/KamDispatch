from base64 import b64decode
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from unittest.mock import patch

import httpx
from PIL import Image

from monitoring.config import ROOT
from monitoring.snow import Segment, calculate, read_segments, road_samples, tile_at


def test_forecast_samples_road_cells_and_deduplicates_requests():
    started = datetime(2026, 1, 18, 14, 30, tzinfo=timezone.utc)
    north, south = 53.048, 53.046
    west, east = 158.648, 158.650
    sample_tile = tile_at(158.649, 53.047)
    segments = [
        Segment(id="first", name="First", coordinates=[(158.6482, 53.0474), (158.6490, 53.0474)]),
        Segment(id="second", name="Second", coordinates=[(158.6482, 53.0468), (158.6490, 53.0468)]),
        Segment(id="third", name="Third", coordinates=[(158.64, 53.045), (158.641, 53.045)]),
    ]
    # Geometry for this regression is intentionally small with a tile mocked around the road samples.
    x, y = sample_tile
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload["tile"])
        assert request.url.path == "/api/snow" and payload["city_cache_only"] is True
        assert payload["hours"] == 3 and "manual_weather" not in payload
        if payload["tile"]["x"] != x:
            return httpx.Response(204)
        return httpx.Response(200, json={
            "tile": {"z": 17, "x": x, "y": y}, "bbox_wgs84": [west, south, east, north],
            "snow_depth_cm": [[6.5, 16], [5, None]], "forecast_snowfall_cm": 5,
            "weather_source": "Open-Meteo", "model": "hybrid",
            "weather": [{"end_utc": (started.replace(minute=0) + timedelta(hours=i)).isoformat(),
                         "wind_from_deg": 315, "wind_speed_ms": 12, "snowfall_cm": 5 / 3}
                        for i in range(1, 4)],
        })

    with patch("monitoring.snow.tile_at", side_effect=lambda lon, lat: (x, y) if lon > 158.645 else (x-1, y)):
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = calculate(client, "http://snow.test", segments, started)
            outside_window = calculate(client, "http://snow.test", segments, started + timedelta(hours=1))

    assert len(requests) == 4
    assert result["status"] == "partial"
    assert [row["status"] for row in result["segments"]] == ["watch", "no_signal", "unavailable"]
    assert [row["excess_cm"] for row in result["segments"]] == [1.5, 0, None]
    assert result["weather_by_tile"][0]["hours"][0]["wind_from_deg"] == 315
    raster = result["raster_tiles"][result["tiles_by_segment"]["first"][0]]
    with Image.open(BytesIO(b64decode(raster["png"]))) as png:
        alpha = png.convert("RGBA").getchannel("A")
        assert 0 < alpha.getpixel((0, 0)) < alpha.getpixel((1, 0))
        assert alpha.getpixel((0, 1)) == alpha.getpixel((1, 1)) == 0
    assert outside_window["status"] == "unavailable"


def test_demo_segments_use_the_bundled_geometry():
    segments = read_segments(ROOT / "monitoring" / "snow_segments.json")
    demo = json.loads((ROOT / "monitoring" / "snow_demo.json").read_text())
    for config, row in zip(segments, demo["segments"], strict=True):
        assert row["coordinates"] == config.model_dump(mode="json")["coordinates"]
        assert list(road_samples(config))
