from dataclasses import replace
from datetime import datetime, timedelta, timezone
from time import monotonic, sleep

import psycopg
from fastapi.testclient import TestClient

from monitoring import api
from monitoring.config import Settings
from monitoring.forecast_sites import forecast_sites
from monitoring.system_status import bus_status, weather_status, worker_status

NOW = datetime(2026, 11, 24, 6, tzinfo=timezone.utc)
SETTINGS = Settings(database="fixture", mode="preview", weather_key="fixture",
                    weather_ttl=1800, worker_heartbeat_ttl=90, bus_rnis_enabled=True)


def test_forecast_retains_last_success_and_reports_provider_error_until_expiration():
    point = next(iter(forecast_sites([])))
    fetched = NOW - timedelta(minutes=5)
    forecasts = [{"point": point, "fetched_at": fetched,
                  "periods": [{"horizon": "hourly", "at": NOW.isoformat()}]}]
    checks = [{"name": f"forecast:{point}", "status": "unavailable",
               "observed_at": NOW, "reason": "upstream_http_503"}]
    state = weather_status(SETTINGS, [], forecasts, checks, NOW)["points"][point]
    assert state == {"status": "degraded", "last_success_at": fetched, "checked_at": NOW,
                     "age_seconds": 300, "valid_until": fetched + timedelta(minutes=30),
                     "reason": "upstream_http_503"}
    expired = weather_status(SETTINGS, [], forecasts, checks, NOW + timedelta(minutes=25))["points"][point]
    assert expired["status"] == "stale"
    assert expired["reason"] == "upstream_http_503"


def test_heartbeat_and_bus_measurement_age_not_renewed_by_frozen_input():
    row = {"name": "worker", "observed_at": NOW - timedelta(seconds=90)}
    assert worker_status(SETTINGS, [row], NOW)["reason"] == "heartbeat_expired"
    assert worker_status(replace(SETTINGS, mode="demo"), [row], NOW)["status"] == "unknown"
    state = bus_status(SETTINGS, {"state": "insufficient", "observed_at": NOW},
                       NOW - timedelta(seconds=91), NOW)
    assert state["status"] == "stale"
    assert state["reason"] == "positions_expired"


def test_status_is_available_from_cold_start_with_database_down(monkeypatch):
    def unreachable(*args, **kwargs):
        raise psycopg.OperationalError("test database offline")

    monkeypatch.setattr(api, "migrate", unreachable)
    with TestClient(api.create_app(SETTINGS)) as client:
        end = monotonic() + 2
        while monotonic() < end:
            response = client.get("/api/v1/status")
            if response.json()["components"]["postgresql"]["status"] == "unavailable":
                break
            sleep(.02)
        assert response.status_code == 200
        assert response.json()["components"]["postgresql"]["status"] == "unavailable"
        assert client.get("/healthz").status_code == 503
        dashboard = client.get("/api/v1/dashboard")
        assert dashboard.status_code == 503
        assert dashboard.json()["code"] == "database_unavailable"
        assert client.get("/api/v1/citizens/form-config").status_code == 200


def test_status_probe_reads_migrated_postgresql():
    import os

    import pytest

    database = os.getenv("TEST_DATABASE_URL")
    if not database:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL database")
    with TestClient(api.create_app(replace(SETTINGS, database=database))) as client:
        end = monotonic() + 5
        while monotonic() < end:
            components = client.get("/api/v1/status").json()["components"]
            if components["postgresql"]["status"] == "healthy":
                break
            sleep(.03)
        assert components["postgresql"]["status"] == "healthy"
        assert components["worker"]["status"] in {"unknown", "stale", "healthy"}
        assert client.get("/api/v1/dashboard").status_code == 200
