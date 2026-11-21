from datetime import datetime, timedelta, timezone

from monitoring.city_worker import forecast_delay
from monitoring.config import Settings


def test_city_worker_reuses_fresh_remote_forecast():
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    settings = Settings(snow_city_remote_url="http://city-api:8008")
    snapshot = {"status": "ready", "end": (now + timedelta(hours=2)).isoformat(),
                "calculated_at": now - timedelta(minutes=5), "remote_run_id": "cached"}
    assert forecast_delay(settings, snapshot, now) == 1500
    assert forecast_delay(settings, {**snapshot, "end": (now + timedelta(minutes=5)).isoformat()}, now) == 300
    assert forecast_delay(settings, {**snapshot, "status": "stale"}, now) == 0
    assert forecast_delay(settings, {**snapshot, "remote_run_id": None}, now) == 0
    assert forecast_delay(settings, None, now) == 0
