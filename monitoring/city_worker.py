"""Publish complete city snapshots independently of camera and mail processing."""
from datetime import datetime, timezone
import logging
import signal
from threading import Event
from time import monotonic

import httpx
from psycopg.types.json import Jsonb

from .config import Settings
from .db import check, connect, migrate, prepare_run
from .remote_snow import merge_reserve, remote_city
from .snow import REFRESH_SECONDS, snow_forecast

LOGGER = logging.getLogger(__name__)
RETRY_SECONDS = 120


def local_city(settings, client, request=None):
    response = client.post(f"{settings.snow_city_url}/api/snow/city", json=request, timeout=httpx.Timeout(110, connect=5))
    response.raise_for_status()
    payload = response.json()
    if payload.get("scope") != "city" or not payload.get("segments"):
        raise ValueError("No road segments in city forecast")
    return payload


def collect_city(settings, client, stop):
    if not settings.snow_city_remote_url:
        return local_city(settings, client)
    errors = (httpx.HTTPError, ValueError, KeyError, TypeError, OSError, EOFError)
    try:
        primary = remote_city(client, settings.snow_city_remote_url, stop)
    except errors as error:
        if not settings.snow_city_url or stop and stop.is_set():
            raise
        LOGGER.warning("Hosted city forecast failed (%s); using local reserve", type(error).__name__)
        reserve = local_city(settings, client)
        reserve["weather_sources"] = ["Резервная модель · Open-Meteo"]
        return reserve
    missing = [row["id"] for row in primary["segments"] if row["status"] == "unavailable"]
    if missing and settings.snow_city_url:
        try:
            reserve = local_city(settings, client, {"start": primary["start"], "segment_ids": missing})
            merge_reserve(primary, reserve)
        except errors as error:
            LOGGER.warning("Local reserve failed (%s); keeping hosted forecast", type(error).__name__)
    return primary


def update_city(settings, client, stop=None):
    started = monotonic()
    payload = collect_city(settings, client, stop)
    calculated_at = datetime.now(timezone.utc)
    summary = {key: payload[key] for key in ("scope", "status", "start", "end", "counts", "weather_sources", "roads_at")}
    if payload.get("remote_run_id"):
        summary["remote_run_id"] = payload["remote_run_id"]
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO snow_forecasts (run_id,calculated_at,status,payload,scope,summary) VALUES (%s,%s,%s,%s,'city',%s)",
            (settings.mode, calculated_at, payload["status"], Jsonb(payload), Jsonb(summary)),
        )
        conn.execute(
            """DELETE FROM snow_forecasts WHERE run_id=%s AND calculated_at < now()-interval '24 hours'
               AND scope='city'""",
            (settings.mode,),
        )
        check(conn, settings.mode, "snow_city", payload["status"])
    LOGGER.info("Published city forecast in %.1fs, coverage %s", monotonic() - started, payload["counts"])
    return summary


def forecast_delay(settings, forecast, now):
    if not forecast or forecast["status"] not in {"ready", "partial"}:
        return 0
    if settings.snow_city_remote_url and not forecast.get("remote_run_id"):
        return 0
    end = datetime.fromisoformat(forecast["end"])
    elapsed = (now - forecast["calculated_at"]).total_seconds()
    return max(0, min(REFRESH_SECONDS - elapsed, (end - now).total_seconds()))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    if settings.mode not in {"preview", "live"} or not settings.city_snow or not settings.database:
        raise SystemExit("City forecasts require a simulator, DATABASE_URL, and preview or live mode")
    migrate(settings)
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    with connect(settings) as owner, httpx.Client() as client:
        lock = owner.execute("SELECT pg_try_advisory_lock(442705) AS acquired").fetchone()
        if not lock["acquired"]:
            raise SystemExit("A city worker already owns the scheduler")
        prepare_run(owner, settings.mode, settings.mode, ())
        now = datetime.now(timezone.utc)
        existing = snow_forecast(owner, settings, settings.mode, now)
        owner.commit()
        if stop.wait(forecast_delay(settings, existing, now)):
            return
        while not stop.is_set():
            delay = REFRESH_SECONDS
            try:
                with connect(settings) as conn:
                    check(conn, settings.mode, "snow_city", "running")
                summary = update_city(settings, client, stop)
                if settings.snow_city_remote_url and not summary.get("remote_run_id"):
                    delay = RETRY_SECONDS
            except Exception as error:
                LOGGER.warning("City forecast unavailable (%s)", type(error).__name__)
                delay = RETRY_SECONDS
                with connect(settings) as conn:
                    check(conn, settings.mode, "snow_city", "unavailable")
            stop.wait(delay)


if __name__ == "__main__":
    main()
