from datetime import datetime, timedelta, timezone

import pytest

from monitoring.remote_snow import convert_replay, forecast_request, merge_reserve
from monitoring.snow import Segment, road_samples


def test_hosted_grid_is_sampled_against_local_road_geometry():
    now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    north = Segment(id="north", name="North", coordinates=[(158.6, 53.08), (158.61, 53.08)])
    south = Segment(id="south", name="South", coordinates=[(158.6, 53.02), (158.61, 53.02)])
    outside = Segment(id="outside", name="Outside", coordinates=[(158.6, 53.11), (158.61, 53.11)])
    segments = [(segment, list(road_samples(segment))) for segment in (north, south, outside)]
    payload = convert_replay({
        "run_id": "a"*64, "bounds": [158.55, 53.0, 158.65, 53.1], "rows": 2, "cols": 2,
        "grids": {"snow_cm": [[1, 5], [1, 1]], "weather_snow": [[1, 2], [1, 1]]},
    }, now, (segments, None))
    assert payload["counts"] == {"watch": 1, "no_signal": 1, "unavailable": 1}
    assert payload["segments"][0]["excess_cm"] == 3
    assert len(payload["raster_tiles"]) == 1
    missing = payload["segments"][2]
    reserve = {"start": payload["start"], "end": payload["end"],
               "segments": [{**missing, "status": "no_signal", "excess_cm": 0}],
               "tiles_by_segment": {"outside": ["1/1"]}, "raster_tiles": {"1/1": {"png": "tile"}}}
    assert merge_reserve(payload, reserve)["status"] == "ready"
    assert payload["segments"][2]["calculation_source"] == "local"
    assert payload["segments"][0]["calculation_source"] == "remote"
    assert payload["raster_tiles"]["1/1"]["png"] == "tile"
    with pytest.raises(ValueError, match="windows differ"):
        merge_reserve(payload, {**reserve, "end": (now+timedelta(hours=2)).isoformat()})


def test_request_uses_only_a_fresh_complete_hourly_window():
    now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    weather = {"terms": [(now+timedelta(hours=i)).isoformat() for i in range(4)], "sha256": "revision"}
    request, start = forecast_request(weather, now+timedelta(minutes=15))
    assert start == now and request["weather_revision"] == "revision"
    with pytest.raises(ValueError, match="current three-hour"):
        forecast_request(weather, now+timedelta(hours=1))


def test_extended_request_uses_every_contiguous_complete_provider_hour():
    now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    terms = [(now+timedelta(hours=i)).isoformat() for i in range(385)]
    request, _ = forecast_request({"terms": terms, "snow_forecast_terms": terms, "sha256": "revision"}, now)
    assert request["hours"] == 384
    request, _ = forecast_request({"terms": terms, "snow_forecast_terms": terms[:171]+terms[172:],
                                   "sha256": "revision"}, now)
    assert request["hours"] == 170
