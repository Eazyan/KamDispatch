"""Read-only component health, separate from dashboard readiness."""
import asyncio
from datetime import datetime, timedelta, timezone
import logging

import httpx
import psycopg

from .bus import MAX_SOURCE_AGE
from .db import connect
from .forecast_sites import forecast_sites
from .traffic_feed import classify

LOGGER = logging.getLogger(__name__)
DB_PROBE_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError, psycopg.errors.QueryCanceled)
DB_UNAVAILABLE = (psycopg.OperationalError, psycopg.InterfaceError)
LIVE_MODES = {"preview", "live"}
PERIOD_HOURS = {"hourly": 1, "three_hourly": 3, "daily": 24}
POLL_SECONDS = 5


def component(status, now, *, success=None, checked=None, expires=None, reason=None):
    return {
        "status": status,
        "last_success_at": success,
        "checked_at": checked,
        "age_seconds": max(0, int((now - success).total_seconds())) if success else None,
        "valid_until": expires,
        "reason": reason,
    }


def forecast_deadline(periods):
    return max(datetime.fromisoformat(item["at"]) + timedelta(hours=PERIOD_HOURS[item["horizon"]])
               for item in periods)


def weather_status(settings, districts, forecasts, checks, now):
    if settings.mode not in LIVE_MODES or not settings.weather_key:
        return {**component("unknown", now, reason="not_configured"), "points": {}}
    rows = {row["point"]: row for row in forecasts}
    by_check = {row["name"]: row for row in checks}
    points = {}
    for name in forecast_sites(districts):
        row, check = rows.get(name), by_check.get(f"forecast:{name}")
        fetched = row["fetched_at"] if row else None
        checked = check["observed_at"] if check else None
        expires = None
        if row:
            try:
                expires = min(fetched + timedelta(seconds=settings.weather_ttl), forecast_deadline(row["periods"]))
            except (TypeError, ValueError, KeyError, OverflowError):
                expires = fetched
        failed = check and check["status"] == "unavailable"
        reason = (check.get("reason") or "upstream_unavailable") if failed else None
        if expires and expires <= now:
            state, reason = "stale", reason or "forecast_expired"
        elif failed:
            state = "degraded" if row else "unavailable"
        elif row:
            state = "healthy"
        else:
            state, reason = "unknown", "awaiting_forecast"
        points[name] = component(state, now, success=fetched, checked=checked, expires=expires, reason=reason)
    states = {point["status"] for point in points.values()}
    state = next(iter(states)) if len(states) == 1 else "degraded"
    expiries = [point["valid_until"] for point in points.values() if point["valid_until"]]
    return {**component(
        state, now,
        success=max((point["last_success_at"] for point in points.values() if point["last_success_at"]), default=None),
        checked=max((point["checked_at"] for point in points.values() if point["checked_at"]), default=None),
        expires=min(expiries, default=None),
        reason=next((point["reason"] for point in points.values() if point["reason"]), None),
    ), "points": points}


def worker_status(settings, checks, now):
    if settings.mode not in LIVE_MODES:
        return component("unknown", now, reason="not_configured")
    beat = next((row["observed_at"] for row in checks if row["name"] == "worker"), None)
    if beat is None:
        return component("unknown", now, reason="awaiting_heartbeat")
    expiration = beat + timedelta(seconds=settings.worker_heartbeat_ttl)
    if expiration <= now:
        return component("stale", now, success=beat, checked=beat, expires=expiration, reason="heartbeat_expired")
    return component("healthy", now, success=beat, checked=beat, expires=expiration)


def bus_status(settings, snapshot, last_position, now):
    if settings.mode not in LIVE_MODES or not settings.collect_buses:
        return component("unknown", now, reason="not_configured")
    checked = snapshot["observed_at"] if snapshot else None
    expires = last_position + MAX_SOURCE_AGE if last_position else None
    if snapshot and snapshot["state"] == "unavailable":
        state, reason = "unavailable", "upstream_unavailable"
    elif expires and expires <= now:
        state, reason = "stale", "positions_expired"
    elif not checked:
        state, reason = "unknown", "awaiting_positions"
    elif checked + MAX_SOURCE_AGE <= now:
        state, reason = "stale", "collection_expired"
    elif not last_position or snapshot["state"] != "ready":
        state, reason = "degraded", "no_recent_positions"
    else:
        state, reason = "healthy", None
    return component(state, now, success=last_position, checked=checked, expires=expires, reason=reason)


class SystemStatus:
    def __init__(self, settings, districts, traffic, initialize_db):
        self.settings = settings
        self.districts = districts
        self.traffic = traffic
        self.initialize_db = initialize_db
        self.database_success = None
        self.city_success = None
        self.checked = None
        now = datetime.now(timezone.utc)
        self.parts = {name: component("unknown", now, reason="awaiting_probe")
                      for name in ("postgresql", "worker", "weather", "traffic", "buses", "city_sim",
                                   "citizens", "dispatch")}

    def database(self):
        now = datetime.now(timezone.utc)
        try:
            self.initialize_db()
            with connect(self.settings, timeout=2, statement_timeout=2000) as conn:
                conn.execute("SELECT 1")
                checks = conn.execute(
                    "SELECT name,status,observed_at,reason FROM checks WHERE run_id=%s "
                    "AND (name='worker' OR name LIKE 'forecast:%%')", (self.settings.mode,),
                ).fetchall()
                forecasts = conn.execute(
                    "SELECT DISTINCT ON (point) point,fetched_at,periods FROM forecasts "
                    "WHERE run_id=%s ORDER BY point,id DESC", (self.settings.mode,),
                ).fetchall()
                snapshot, position = None, None
                if self.settings.mode in LIVE_MODES and self.settings.collect_buses:
                    snapshot = conn.execute("SELECT state,observed_at FROM bus_snapshots WHERE run_id=%s",
                                            (self.settings.mode,)).fetchone()
                    position = conn.execute(
                        "SELECT measured_at FROM bus_positions WHERE run_id=%s "
                        "AND measured_at<=%s AND received_at<=%s ORDER BY measured_at DESC LIMIT 1",
                        (self.settings.mode, now, now),
                    ).fetchone()
        except DB_PROBE_ERRORS:
            result = {name: component("unknown", now, reason="database_unavailable")
                      for name in ("worker", "weather", "buses")}
            result.update({name: component("unavailable", now, checked=now, reason="database_unavailable")
                           for name in ("citizens", "dispatch")})
            result["postgresql"] = component("unavailable", now, success=self.database_success,
                                             checked=now, reason="database_unavailable")
            return result
        self.database_success = now
        return {
            "postgresql": component("healthy", now, success=now, checked=now),
            "worker": worker_status(self.settings, checks, now),
            "weather": weather_status(self.settings, self.districts, forecasts, checks, now),
            "buses": bus_status(self.settings, snapshot, position["measured_at"] if position else None, now),
            "citizens": component("healthy", now, success=now, checked=now),
            "dispatch": component("healthy", now, success=now, checked=now),
        }

    def city(self):
        now = datetime.now(timezone.utc)
        url = self.settings.snow_city_remote_url
        if not url:
            return component("unknown", now, reason="not_configured")
        try:
            response = httpx.get(url + "/api/v1/health", timeout=2, trust_env=False)
            response.raise_for_status()
            flags = response.json()
            if not isinstance(flags, dict):
                raise ValueError("Invalid City Sim health")
        except (httpx.HTTPError, ValueError):
            return component("unavailable", now, success=self.city_success, checked=now, reason="city_health_failed")
        if flags.get("terrain_loaded") is not True or flags.get("snow_solver_connected") is not True:
            return component("degraded", now, success=self.city_success, checked=now, reason="terrain_or_solver_unready")
        self.city_success = now
        return component("healthy", now, success=now, checked=now)

    def traffic_status(self):
        now = datetime.now(timezone.utc)
        if self.traffic is None:
            return component("unknown", now, reason="not_configured")
        publication, success, checked, reason = self.traffic.refresh()
        if not publication or not publication["segments"]:
            return component("unavailable" if reason else "unknown", now, success=success, checked=checked,
                             reason=reason or "no_observed_intervals")
        expires = datetime.fromisoformat(publication["valid_until"])
        stale = sum(row["status"] == "stale" for row in classify(publication, now)["segments"])
        if stale == len(publication["segments"]):
            state, reason = "stale", reason or "all_intervals_expired"
        elif stale or reason or publication["status"] == "partial":
            state, reason = "degraded", reason or ("some_intervals_expired" if stale else "partial_publication")
        else:
            state = "healthy"
        return component(state, now, success=success, checked=checked, expires=expires, reason=reason)

    async def run(self):
        probes = (self.database, self.city, self.traffic_status)
        while True:
            database, city, traffic = await asyncio.gather(*(asyncio.to_thread(probe) for probe in probes),
                                                            return_exceptions=True)
            now = datetime.now(timezone.utc)
            if isinstance(database, Exception):
                LOGGER.error("Database status probe failed (%s)", type(database).__name__)
                database = {name: component("unknown", now, reason="probe_failed")
                            for name in ("postgresql", "worker", "weather", "buses", "citizens", "dispatch")}
            for name, value in (("city_sim", city), ("traffic", traffic)):
                if isinstance(value, Exception):
                    LOGGER.error("%s status probe failed (%s)", name, type(value).__name__)
                    value = component("unknown", now, reason="probe_failed")
                database[name] = value
            self.parts, self.checked = database, now
            await asyncio.sleep(POLL_SECONDS)

    def snapshot(self):
        now = datetime.now(timezone.utc)
        parts = self.parts
        if self.checked is None or now - self.checked > timedelta(seconds=20):
            parts = {name: component("unknown", now, reason="status_snapshot_expired") for name in parts}
        states = {part["status"] for part in parts.values() if part["reason"] != "not_configured"}
        meaningful = states - {"unknown"}
        if states == {"healthy"}:
            overall = "healthy"
        elif len(meaningful) <= 1 and "healthy" not in meaningful:
            overall = next(iter(meaningful), "unknown")
        else:
            overall = "degraded"
        return {
            "status": overall,
            "checked_at": self.checked,
            "components": parts,
        }
