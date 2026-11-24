from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import ROOT


@contextmanager
def connect(settings, *, timeout=5, statement_timeout=15000):
    with psycopg.connect(settings.database, row_factory=dict_row, connect_timeout=timeout,
                         options=f"-c statement_timeout={statement_timeout} -c timezone=UTC") as conn:
        yield conn


def migrate(settings, *, timeout=5):
    with connect(settings, timeout=timeout) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(442701)")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY)")
        names = {row["name"] for row in conn.execute("SELECT name FROM schema_migrations")}
        for path in sorted((ROOT / "monitoring" / "migrations").glob("*.sql")):
            if path.name not in names:
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations VALUES (%s)", (path.name,))


def prepare_run(conn, run_id, mode, cameras):
    conn.execute("INSERT INTO runs (id, mode) VALUES (%s, %s) ON CONFLICT DO NOTHING", (run_id, mode))
    for camera in cameras:
        conn.execute(
            """INSERT INTO cameras (run_id,id,name,area,road,coordinates,location_note,interval_seconds,
                                    road_priority,road_priority_reason,district,section_id,section_name)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (run_id,id) DO UPDATE SET name=EXCLUDED.name, area=EXCLUDED.area,
               road=EXCLUDED.road, coordinates=EXCLUDED.coordinates, location_note=EXCLUDED.location_note,
               interval_seconds=EXCLUDED.interval_seconds, road_priority=EXCLUDED.road_priority,
               road_priority_reason=EXCLUDED.road_priority_reason, district=EXCLUDED.district,
               section_id=EXCLUDED.section_id, section_name=EXCLUDED.section_name""",
            (run_id, camera["id"], camera["name"], camera["area"], camera["road"],
             Jsonb(camera["coordinates"]) if camera.get("coordinates") else None, camera.get("location_note"), camera.get("interval_seconds"),
             camera.get("road_priority", 0), camera.get("road_priority_reason"), camera.get("district"),
             camera.get("section_id"), camera.get("section_name")),
        )


def check(conn, run_id, name, status, reason=None):
    conn.execute(
        """INSERT INTO checks (run_id,name,status,observed_at,reason) VALUES (%s,%s,%s,now(),%s)
           ON CONFLICT (run_id,name) DO UPDATE SET status=EXCLUDED.status, observed_at=now(), reason=EXCLUDED.reason""",
        (run_id, name, status, reason),
    )
