"""Read the public RNIS map feed and keep bus observations isolated by run."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
from pathlib import Path
from time import monotonic
from typing import Literal

import httpx
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, model_validator

from .bus import Confirmation, LOCAL_TIME, MAX_SOURCE_AGE, Passage, Position, Section
from .bus import baselines, measure_passages, observations, usable_position
from .db import check, connect

LOGGER = logging.getLogger(__name__)
RNIS_URL = "https://bus.kamgov.ru/ajax/request?com.rnis.grpc-proxy.action.telematics.get"
BOUNDS = {"RightBottomLongitude": 158.79, "TopLeftLongitude": 158.32,
          "TopLeftLatitude": 53.24, "RightBottomLatitude": 52.91}
RNIS_REQUEST = {"headers": {"meta": {"filters": {
    "BoundingBox": BOUNDS, "Subsystem": "kiutr", "LastSignal": "30", "WithClustered": False, "Zoom": 12,
}}}, "payload": {}}
POLL_SECONDS = 30


class PilotSection(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    coordinates: list[tuple[float, float]] = Field(min_length=2)
    entry_fraction: float = Field(gt=0, lt=1)
    exit_fraction: float = Field(gt=0, lt=1)
    location_key: str = Field(min_length=1, max_length=255)
    publish: list[Literal["day", "night"]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_geometry(self):
        if self.exit_fraction <= self.entry_fraction or len(set(self.coordinates)) < 2 or not all(
            BOUNDS["TopLeftLongitude"] <= lon <= BOUNDS["RightBottomLongitude"]
            and BOUNDS["RightBottomLatitude"] <= lat <= BOUNDS["TopLeftLatitude"] for lon, lat in self.coordinates
        ):
            raise ValueError("Invalid section gates or coordinates outside RNIS coverage")
        return self

    def geometry(self):
        return Section(self.id, self.name, self.coordinates, self.entry_fraction, self.exit_fraction)


def load_sections(path):
    if path is None:
        return []
    sections = [PilotSection.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    if len(sections) > 3 or len({section.id for section in sections}) != len(sections):
        raise ValueError("Configure at most three sections with unique IDs")
    return sections


def parse_positions(data, received_at):
    if not isinstance(data, dict) or data.get("success") is not True or data.get("errors"):
        raise ValueError("RNIS rejected the request")
    payload = data.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("vehicles"), list):
        raise ValueError("RNIS response changed")
    buses = [item for item in payload["vehicles"] if isinstance(item, dict) and item.get("VehicleType") == "bus"]
    positions = []
    for item in buses:
        try:
            vehicle = item["VehicleUUID"]
            if not isinstance(vehicle, str) or not vehicle.strip():
                continue
            lon, lat = float(item["Longitude"]), float(item["Latitude"])
            measured_at = datetime.fromtimestamp(item["DeviceTime"], timezone.utc)
        except (KeyError, ValueError, TypeError, OverflowError, OSError):
            continue
        if not (BOUNDS["TopLeftLongitude"] <= lon <= BOUNDS["RightBottomLongitude"]
                and BOUNDS["RightBottomLatitude"] <= lat <= BOUNDS["TopLeftLatitude"]):
            continue
        pseudonym = sha256(f"rnis:{vehicle}".encode()).hexdigest()
        route = item.get("RouteNumber")
        route = (route.strip()[:24] or None) if isinstance(route, str) else None
        point = Position(pseudonym, lon, lat, measured_at, received_at, route)
        if usable_position(point):
            positions.append(point)
    return positions


def fetch_positions(client):
    response = client.post(RNIS_URL, content=json.dumps(RNIS_REQUEST), timeout=8,
                           headers={"Content-Type": "text/plain;charset=UTF-8"})
    response.raise_for_status()
    now = datetime.now(timezone.utc)
    return now, parse_positions(response.json(), now)


def read_confirmations(conn, run_id, sections, cutoff, now):
    if not sections:
        return []
    keys = [section.location_key for section in sections]
    camera = conn.execute(
        """SELECT l.location_key, fresh.observed_at
           FROM dispatch_camera_locations l
           JOIN LATERAL (
               SELECT f.observed_at, o.scene FROM observations o
               JOIN frames f ON f.id=o.frame_id
               WHERE f.run_id=l.run_id AND f.camera_id=l.camera_id
                 AND f.observed_at > %s AND f.observed_at <= %s AND o.analyzed_at <= %s
               ORDER BY f.observed_at DESC, o.frame_id DESC LIMIT 1
           ) fresh ON true
           WHERE l.run_id=%s AND l.location_key=ANY(%s)
             AND fresh.scene->>'format_valid'='true'
             AND fresh.scene->'details'->>'road_view' IN ('good','limited')
             AND fresh.scene->'details'->>'road_obstruction' IN ('partial','major')""",
        (cutoff, now, now, run_id, keys),
    ).fetchall()
    reports = conn.execute(
        """SELECT location_key, created_at AS observed_at FROM dispatch_reports
           WHERE run_id=%s AND location_key=ANY(%s) AND passage_obstructed
             AND created_at > %s AND created_at <= %s AND closed_at IS NULL""",
        (run_id, keys, cutoff, now),
    ).fetchall()
    return [
        Confirmation(section.id, source, item["observed_at"])
        for source, items in (("camera", camera), ("report", reports))
        for item in items for section in sections if section.location_key == item["location_key"]
    ]


def save_snapshot(conn, run_id, now, state, rows):
    payload = [
        {**row, **{field: row[field].isoformat() if row[field] else None for field in ("last_seen", "valid_until")}}
        for row in rows
    ]
    by_key = {(row["section_id"], row["direction"]): row for row in payload if row["status"] == "check"}
    conn.execute(
        """UPDATE bus_hints SET closed_at=valid_until
           WHERE run_id=%s AND closed_at IS NULL AND valid_until <= %s""", (run_id, now),
    )
    for item in conn.execute(
        "SELECT section,direction FROM bus_hints WHERE run_id=%s AND closed_at IS NULL", (run_id,),
    ).fetchall():
        section, direction = item["section"], item["direction"]
        if (section, direction) not in by_key:
            conn.execute(
                """UPDATE bus_hints SET closed_at=%s
                   WHERE run_id=%s AND section=%s AND direction=%s AND closed_at IS NULL""",
                (now, run_id, section, direction),
            )
    for (section, direction), evidence in by_key.items():
        conn.execute(
            """INSERT INTO bus_hints (run_id,section,direction,opened_at,valid_until,evidence) VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (run_id,section,direction) WHERE closed_at IS NULL DO UPDATE
               SET valid_until=EXCLUDED.valid_until""",
            (run_id, section, direction, now, evidence["valid_until"], Jsonb(evidence)),
        )
    conn.execute(
        """INSERT INTO bus_snapshots VALUES (%s,%s,%s,%s)
           ON CONFLICT (run_id) DO UPDATE SET observed_at=EXCLUDED.observed_at,
               state=EXCLUDED.state, rows=EXCLUDED.rows""",
        (run_id, now, state, Jsonb(payload)),
    )


def collect(conn, run_id, sections, geometry, now, positions, state):
    with conn.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO bus_positions (run_id,vehicle,measured_at,received_at,lon,lat,route)
               VALUES (%(run_id)s,%(vehicle)s,%(measured_at)s,%(received_at)s,%(lon)s,%(lat)s,%(route)s)
               ON CONFLICT DO NOTHING""",
            [{"run_id": run_id, **asdict(point)} for point in positions],
        )
    last_position = max((point.measured_at for point in positions), default=None)
    rows = []
    if sections:
        if positions:
            traces = conn.execute(
                """SELECT vehicle,measured_at,received_at,lon,lat FROM bus_positions
                   WHERE run_id=%s AND measured_at >= %s AND received_at <= %s""",
                (run_id, now - timedelta(minutes=25), now),
            ).fetchall()
            passages = list(measure_passages(geometry, (Position(**trace) for trace in traces)))
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO bus_passages (run_id,section,direction,vehicle,entered_at,ended_at,received_at)
                       SELECT %(run_id)s,%(section)s,%(direction)s,%(vehicle)s,%(entered_at)s,%(ended_at)s,%(received_at)s
                       WHERE NOT EXISTS (
                           SELECT 1 FROM bus_passages WHERE run_id=%(run_id)s AND section=%(section)s
                             AND direction=%(direction)s AND vehicle=%(vehicle)s
                             AND entered_at BETWEEN %(entered_at)s - interval '45 seconds'
                                                AND %(entered_at)s + interval '45 seconds'
                       )
                       ON CONFLICT DO NOTHING""",
                    [{"run_id": run_id, **asdict(passage)} for passage in passages],
                )
        midnight = now.astimezone(LOCAL_TIME).replace(hour=0, minute=0, second=0, microsecond=0)
        reference = conn.execute(
            """SELECT p.section,p.direction,p.vehicle,p.entered_at,p.ended_at,p.received_at
               FROM bus_passages p
               JOIN unnest(%s::text[], %s::text[]) AS s(section,location_key) ON s.section=p.section
               WHERE p.run_id=%s AND p.ended_at >= %s AND p.ended_at < %s
                 AND NOT EXISTS (
                     SELECT 1 FROM bus_hints h WHERE h.run_id=p.run_id AND h.section=p.section
                       AND h.direction=p.direction AND h.opened_at <= p.ended_at + interval '10 minutes'
                       AND coalesce(h.closed_at,%s) >= p.entered_at
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM dispatch_reports r WHERE r.run_id=p.run_id AND r.location_key=s.location_key
                       AND r.passage_obstructed AND r.created_at BETWEEN
                           p.entered_at - interval '10 minutes' AND p.ended_at + interval '10 minutes'
                 )""",
            ([section.id for section in sections], [section.location_key for section in sections],
             run_id, midnight - timedelta(days=28), midnight, now),
        ).fetchall()
        recent = conn.execute(
            """SELECT section,direction,vehicle,entered_at,ended_at,received_at FROM bus_passages
               WHERE run_id=%s AND ended_at > %s""",
            (run_id, now - timedelta(minutes=10)),
        ).fetchall()
        evidence = read_confirmations(conn, run_id, sections, now - timedelta(minutes=5), now)
        rows = observations(geometry, (Passage(**item) for item in recent),
                            baselines(Passage(**item) for item in reference), evidence, now, last_position)
    save_snapshot(conn, run_id, now, state, rows)
    check(conn, run_id, "bus_rnis", state)


def run_collection(stop, settings, run_id, sections):
    geometry = [section.geometry() for section in sections]
    pause = POLL_SECONDS
    next_prune = 0
    with httpx.Client() as client:
        while not stop.is_set():
            try:
                now, positions = fetch_positions(client)
                state = "ready" if positions else "insufficient"
            except (httpx.HTTPError, ValueError) as exc:
                LOGGER.warning("RNIS read failed (%s)", type(exc).__name__)
                now, positions, state = datetime.now(timezone.utc), [], "unavailable"
            try:
                with connect(settings) as conn:
                    collect(conn, run_id, sections, geometry, now, positions, state)
                    if monotonic() >= next_prune:
                        conn.execute("DELETE FROM bus_positions WHERE run_id=%s AND measured_at < %s",
                                     (run_id, now - timedelta(days=7)))
                        conn.execute("DELETE FROM bus_passages WHERE run_id=%s AND ended_at < %s",
                                     (run_id, now - timedelta(days=28)))
                        next_prune = monotonic() + 3600
            except Exception:
                LOGGER.exception("Bus observations could not be saved")
                state = "unavailable"
            pause = min(pause * 2, 300) if state == "unavailable" else POLL_SECONDS
            stop.wait(pause)


def dashboard_rnis(conn, settings, run_id, now):
    if not settings.collect_buses or run_id not in ("preview", "live"):
        return None
    period = "day" if 6 <= now.astimezone(LOCAL_TIME).hour < 22 else "night"
    try:
        sections = [section for section in load_sections(settings.bus_sections_path) if period in section.publish]
    except (OSError, ValueError, TypeError):
        return None
    if not sections:
        return None
    snapshot = conn.execute(
        "SELECT observed_at,state,rows FROM bus_snapshots WHERE run_id=%s", (run_id,),
    ).fetchone()
    fresh = snapshot and now - MAX_SOURCE_AGE <= snapshot["observed_at"] <= now
    state = snapshot["state"] if fresh else "unavailable"
    section_ids = {section.id for section in sections}
    rows = [row for row in (snapshot["rows"] if fresh else []) if row["section_id"] in section_ids]
    if not rows:
        rows = observations([section.geometry() for section in sections], (), {}, (), now, None)
    elif state == "ready":
        # Do not keep serving a hint past its window just because the next poll is pending.
        for row in rows:
            if row["valid_until"] and datetime.fromisoformat(row["valid_until"]) <= now:
                row.update(status="insufficient", reason=None, last_seen=None, observed_seconds=None,
                           vehicle_count=0, valid_until=None)
    return {"source": "rnis", "state": state, "as_of": snapshot["observed_at"] if fresh else None, "rows": rows}


def map_rnis(conn, settings, run_id, now):
    if not settings.collect_buses or run_id not in ("preview", "live"):
        return None
    rows = conn.execute(
        """SELECT id,lon,lat,measured_at,route FROM (
               SELECT DISTINCT ON (vehicle) vehicle AS id, lon, lat, measured_at, route FROM bus_positions
               WHERE run_id=%s AND measured_at > %s AND received_at <= %s
               ORDER BY vehicle, measured_at DESC
           ) latest WHERE lon BETWEEN 158.48 AND 158.80 AND lat BETWEEN 52.91 AND 53.16""",
        (run_id, now - MAX_SOURCE_AGE, now),
    ).fetchall()
    snapshot = conn.execute(
        "SELECT state,observed_at FROM bus_snapshots WHERE run_id=%s", (run_id,),
    ).fetchone()
    state = "waiting"
    if snapshot:
        state = "unavailable" if now - snapshot["observed_at"] > MAX_SOURCE_AGE else snapshot["state"]
    if state in ("waiting", "unavailable"):
        rows = []
    else:
        state = "ready" if rows else "insufficient"
    return {"source": "rnis", "state": state, "positions": rows}


def coverage(conn, run_id, sections, scan=False):
    from collections import defaultdict
    from .bus import DIRECTIONS

    interval = timedelta(minutes=10)
    now = datetime.now(timezone.utc)
    end = now.replace(minute=now.minute // 10 * 10, second=0, microsecond=0)
    start = end - timedelta(days=1 if scan else 7)
    first = conn.execute(
        "SELECT min(measured_at) AS first FROM bus_positions WHERE run_id=%s", (run_id,),
    ).fetchone()["first"]
    if first is None:
        return []
    start = max(start, first.replace(minute=first.minute // 10 * 10, second=0, microsecond=0) + interval)
    if start >= end:
        return []
    if scan:
        traces = conn.execute(
            """SELECT vehicle,measured_at,received_at,lon,lat FROM bus_positions
               WHERE run_id=%s AND measured_at >= %s AND measured_at < %s""",
            (run_id, start - timedelta(minutes=25), end),
        ).fetchall()
        passages = measure_passages(sections, (Position(**trace) for trace in traces))
    else:
        rows = conn.execute(
            """SELECT section,direction,vehicle,entered_at,ended_at,received_at FROM bus_passages
               WHERE run_id=%s AND ended_at >= %s AND ended_at < %s""",
            (run_id, start, end),
        ).fetchall()
        passages = (Passage(**row) for row in rows)
    slots = defaultdict(set)
    for passage in passages:
        if start <= passage.ended_at < end:
            index = (passage.ended_at - start) // interval
            slots[passage.section, passage.direction, index].add(passage.vehicle)
    groups = {"day": [], "night": []}
    for index in range((end - start) // interval):
        hour = (start + interval * index).astimezone(LOCAL_TIME).hour
        groups["day" if 6 <= hour < 22 else "night"].append(index)
    return [
        {"section": section.id, "direction": direction, "period": period, "windows": len(indices),
         "observed_windows": sum(bool(slots.get((section.id, direction, index))) for index in indices),
         "two_bus_windows": sum(len(slots.get((section.id, direction, index), ())) >= 2 for index in indices)}
        for section in sections for direction in DIRECTIONS for period, indices in groups.items()
    ]


if __name__ == "__main__":
    import argparse
    from .config import Settings

    parser = argparse.ArgumentParser(description="Summarize observed bus coverage in ten-minute windows")
    parser.add_argument("--run", choices=("preview", "live"), default="preview")
    parser.add_argument("--scan", action="store_true", help="Re-measure bundled roads from the last 24 hours of GPS")
    args = parser.parse_args()
    settings = Settings()
    if args.scan:
        roads = json.loads(Path(__file__).with_name("snow_segments.json").read_text(encoding="utf-8"))
        sections = [Section(road["id"], road["name"], road["coordinates"], .15, .85) for road in roads]
    else:
        sections = [section.geometry() for section in load_sections(settings.bus_sections_path)]
    with connect(settings) as db:
        print(json.dumps(coverage(db, args.run, sections, args.scan), ensure_ascii=False, indent=2))
