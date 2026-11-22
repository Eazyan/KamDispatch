import logging
import signal
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from threading import Event
from time import monotonic
from uuid import uuid4

import httpx

from .adapters import AmbiguousDelivery, Media, analyze, deliver, fetch_camera, forecast
from .bus_worker import load_sections as load_bus_sections, run_collection
from .config import Settings
from .db import check, connect, migrate, prepare_run
from .operations import record_observation, save_forecast, save_summary, store_frame
from .scene import Scene, unknown
from .snow import REFRESH_SECONDS, read_segments, snow_tick

LOGGER = logging.getLogger(__name__)
# Forecast reference points at city scale. These are independent of camera geometry.
POINTS = {"Петропавловск-Камчатский": (53.045, 158.648), "Елизово": (53.189, 158.381)}


def capture_tick(settings, run_id, cameras, media, ffmpeg, pool, due, failures):
    clock = monotonic()
    captures = {
        pool.submit(fetch_camera, camera, ffmpeg, camera.get("last_digest")): camera
        for camera in cameras if due.get(camera["id"], 0) <= clock
    }
    for future in as_completed(captures):
        camera = captures[future]
        camera_id = camera["id"]
        stored = None
        try:
            status, data = future.result()
            with connect(settings) as conn:
                if data and data[3] in camera.get("placeholder_sha256", []):
                    status, data = "stale", None
                    LOGGER.warning("camera_capture camera=%s reason=known_placeholder", camera_id)
                if data:
                    stored = store_frame(conn, run_id, camera_id, data, media, analyze=bool(settings.model_url))
                    if not stored:
                        status = conn.execute("SELECT status FROM cameras WHERE run_id=%s AND id=%s",
                                              (run_id, camera_id)).fetchone()["status"]
                else:
                    conn.execute("UPDATE cameras SET status=%s,last_poll=now() WHERE run_id=%s AND id=%s",
                                 (status, run_id, camera_id))
            if stored:
                camera["last_digest"] = data[3]
            if stored or status != camera.get("last_capture_status"):
                LOGGER.info("camera_capture camera=%s status=%s frame=%s source_at=%s",
                            camera_id, status, stored, data[4] if data else None)
            camera["last_capture_status"] = status
        except Exception as exc:
            LOGGER.warning("Capture failed for %s (%s)", camera_id, type(exc).__name__)
            status = "error"
            with connect(settings) as conn:
                conn.execute("UPDATE cameras SET status='error',last_poll=now() WHERE run_id=%s AND id=%s",
                             (run_id, camera_id))
        failed = status in {"error", "needs_feed"}
        failures[camera_id] = min(failures.get(camera_id, 0) + 1, 4) if failed else 0
        interval = camera["interval_seconds"]
        due[camera_id] = clock + max(interval, min(interval * 2 ** failures[camera_id], 300))


def claim_job(conn, run_id, freshness):
    skipped = conn.execute(
        """WITH candidates AS (
               SELECT f.id,f.camera_id,CASE
                   WHEN j.attempts >= 3 THEN 'attempts_exhausted'
                   WHEN f.observed_at < now() - greatest(%s,2*c.interval_seconds)*interval '1 second' THEN 'frame_expired'
                   WHEN EXISTS (SELECT 1 FROM frames newer JOIN jobs next ON next.frame_id=newer.id
                       WHERE newer.run_id=f.run_id AND newer.camera_id=f.camera_id AND newer.observed_at>f.observed_at
                       AND next.stage IN ('pending','running')) THEN 'frame_superseded'
               END AS reason
               FROM jobs j JOIN frames f ON f.id=j.frame_id
               JOIN cameras c ON (c.run_id,c.id)=(f.run_id,f.camera_id)
               WHERE f.run_id=%s AND (j.stage='pending' OR j.stage='running' AND j.lease_until<now())
           )
           UPDATE jobs j SET stage='skipped',error=c.reason,lease_id=NULL,lease_until=NULL FROM candidates c
           WHERE j.frame_id=c.id AND c.reason IS NOT NULL
           AND (j.stage='pending' OR j.stage='running' AND j.lease_until<now())
           RETURNING j.frame_id,c.camera_id,c.reason""", (freshness, run_id),
    ).fetchall()
    for row in skipped:
        LOGGER.info("camera_analysis camera=%s frame=%s status=skipped reason=%s",
                    row["camera_id"], row["frame_id"], row["reason"])
    frame = conn.execute(
        """SELECT f.*,j.attempts FROM jobs j JOIN frames f ON f.id=j.frame_id
           WHERE f.run_id=%s AND (j.stage='pending' OR j.stage='running' AND j.lease_until<now())
           AND j.available_at<=now() ORDER BY f.observed_at
           FOR UPDATE OF j SKIP LOCKED LIMIT 1""", (run_id,),
    ).fetchone()
    if frame:
        frame["lease_id"] = uuid4()
        conn.execute(
            """UPDATE jobs SET stage='running',attempts=attempts+1,
               lease_until=now()+interval '180 seconds',lease_id=%s WHERE frame_id=%s""",
            (frame["lease_id"], frame["id"]),
        )
    return frame


def analysis_tick(settings, run_id, media, client):
    with connect(settings) as conn:
        frame = claim_job(conn, run_id, settings.freshness)
    if not frame:
        return False
    started = monotonic()
    try:
        features = analyze(client, settings, media.get(frame["media_key"]),
                           camera_id=frame["camera_id"], frame_id=frame["id"])
        status = "ready" if features["format_valid"] else "invalid"
    except (httpx.HTTPError, ValueError, OSError) as exc:
        failure = str(exc.response.status_code) if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        retry = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in (429, 502, 503, 504)
        LOGGER.warning("camera_analysis camera=%s frame=%s attempt=%s failure=%s",
                       frame["camera_id"], frame["id"], frame["attempts"] + 1, failure)
        if retry and frame["attempts"] < 2:
            with connect(settings) as conn:
                conn.execute(
                    """UPDATE jobs SET stage='pending',available_at=now()+(%s*interval '1 second'),
                       lease_id=NULL,lease_until=NULL,error=%s WHERE frame_id=%s AND lease_id=%s""",
                    (3 * 2 ** frame["attempts"], failure, frame["id"], frame["lease_id"]),
                )
                check(conn, run_id, "model", "unavailable")
            return True
        features = Scene(schema_version="1", model="unknown", prompt_version="unknown",
                         format_valid=False, **unknown()).model_dump()
        status = "unavailable"
    with connect(settings) as conn:
        owned = conn.execute(
            """UPDATE jobs SET stage=%s,lease_id=NULL,lease_until=NULL,error=%s
               WHERE frame_id=%s AND lease_id=%s RETURNING frame_id""",
            ("done" if status == "ready" else "failed", None if status == "ready" else status,
             frame["id"], frame["lease_id"]),
        ).fetchone()
        if owned:
            duration = round((monotonic() - started) * 1000)
            record_observation(conn, settings, frame, features, duration)
            LOGGER.info("camera_analysis camera=%s frame=%s status=%s schema=%s prompt=%s source=%s elapsed_ms=%s unknown=%s",
                        frame["camera_id"], frame["id"], status, features["schema_version"], features["prompt_version"],
                        features.get("snow_source"), duration,
                        ",".join(key for key, value in features.items() if value == "uncertain"))
            model_status = status
            if status == "ready" and features.get("snow_source") == "snow":
                model_status = "v2_partial" if settings.model_contract == "scene_v2" else "snow_only"
            check(conn, run_id, "model", model_status)
    return True


def forecast_tick(settings, run_id, client):
    if not settings.weather_key:
        with connect(settings) as conn:
            check(conn, run_id, "forecast", "unconfigured")
        return
    for point, (latitude, longitude) in POINTS.items():
        try:
            data = forecast(client, settings.weather_key, latitude, longitude)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, OverflowError) as exc:
            LOGGER.warning("Forecast unavailable for %s (%s)", point, type(exc).__name__)
            with connect(settings) as conn:
                check(conn, run_id, f"forecast:{point}", "unavailable")
            continue
        with connect(settings) as conn:
            save_forecast(conn, settings, run_id, point, data, datetime.now(timezone.utc))
            check(conn, run_id, f"forecast:{point}", "three_hourly" if data[0]["horizon"] == "three_hourly" else "ready")


def mail_tick(settings, run_id, media, transport=deliver):
    with connect(settings) as conn:
        conn.execute(
            """UPDATE outbox SET stage='ambiguous',error='Interrupted after delivery claim'
               WHERE run_id=%s AND stage='sending' AND lease_until<now()""", (run_id,),
        )
        lease = uuid4()
        row = conn.execute(
            """UPDATE outbox SET stage='sending',attempts=attempts+1,lease_id=%s,
               lease_until=now()+interval '90 seconds' WHERE id=(
                 SELECT id FROM outbox WHERE run_id=%s AND stage='queued' AND available_at<=now()
                 ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""", (lease, run_id),
        ).fetchone()
    if not row:
        return False
    stage, error = "sent", None
    try:
        attachment = media.get(row["media_key"]) if row["media_key"] else None
        transport(settings, row, attachment)
    except AmbiguousDelivery:
        stage, error = "ambiguous", "SMTP acceptance unknown"
    except Exception as exc:
        code = getattr(exc, "smtp_code", None)
        permanent = isinstance(code, int) and 500 <= code < 600
        stage = "failed" if permanent or row["attempts"] >= 5 else "queued"
        error = f"{type(exc).__name__}{f' ({code})' if code else ''}"
    with connect(settings) as conn:
        conn.execute(
            """UPDATE outbox SET stage=%s,error=%s,lease_id=NULL,lease_until=NULL,
               available_at=now()+(%s*interval '1 second') WHERE id=%s AND lease_id=%s AND stage='sending'""",
            (stage, error, 30 * 2 ** row["attempts"], row["id"], lease),
        )
    return True


def repeat(stop, interval, name, fn):
    while not stop.is_set():
        try:
            fn()
        except Exception as exc:
            LOGGER.error("%s failed (%s)", name, type(exc).__name__)
        stop.wait(interval)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings()
    settings.validate()
    migrate(settings)
    if settings.mode in {"demo", "preview"}:
        from .replay import seed_demo
        seed_demo(settings)
    if settings.mode not in {"preview", "live"}:
        return

    run_id = settings.mode
    cameras = list(settings.cameras())
    bus_sections = []
    if settings.collect_buses:
        try:
            bus_sections = load_bus_sections(settings.bus_sections_path)
        except (OSError, ValueError, TypeError) as exc:
            LOGGER.error("Invalid bus section configuration (%s)", type(exc).__name__)
    snow_segments = []
    if settings.snow_sim_url:
        try:
            snow_segments = read_segments(settings.snow_segments_path)
        except (OSError, ValueError, TypeError) as exc:
            LOGGER.error("Invalid snow segment configuration (%s)", type(exc).__name__)
    media = Media(settings)
    ffmpeg = shutil.which("ffmpeg")
    if any(camera["kind"] == "hls" for camera in cameras) and not ffmpeg:
        raise SystemExit("ffmpeg is required for HLS capture")
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_args: stop.set())
    due, failures = {}, {}
    with connect(settings) as owner:
        if not owner.execute("SELECT pg_try_advisory_lock(442702) AS acquired").fetchone()["acquired"]:
            raise SystemExit("A live worker already owns the scheduler")
        prepare_run(owner, run_id, settings.mode, cameras)
        if not settings.model_url:
            check(owner, run_id, "model", "unconfigured")
        if settings.snow_sim_url and not snow_segments:
            check(owner, run_id, "snow_sim", "invalid_segments")
        else:
            owner.execute("DELETE FROM checks WHERE run_id=%s AND name='snow_sim'", (run_id,))
        mail_status = "local_log"
        if settings.mode == "live":
            mail_status = "ready" if settings.recipients else "unconfigured"
        elif settings.mail_test:
            mail_status = "mail_test"
        check(owner, run_id, "mail", mail_status)
        owner.commit()
        with ThreadPoolExecutor(max_workers=4) as capture_pool, ThreadPoolExecutor(max_workers=6) as services:
            with httpx.Client() as client:
                services.submit(repeat, stop, 1, "capture", lambda: capture_tick(settings, run_id, cameras, media, ffmpeg, capture_pool, due, failures))
                if settings.model_url:
                    services.submit(repeat, stop, 1, "analysis", lambda: analysis_tick(settings, run_id, media, client))
                services.submit(repeat, stop, 1800, "forecast", lambda: forecast_tick(settings, run_id, client))
                if snow_segments:
                    services.submit(repeat, stop, REFRESH_SECONDS, "snow simulation",
                                    lambda: snow_tick(settings, run_id, client, snow_segments))
                if settings.mode == "live" or settings.mail_test:
                    services.submit(repeat, stop, 2, "mail", lambda: mail_tick(settings, run_id, media))
                if settings.collect_buses:
                    services.submit(run_collection, stop, settings, run_id, bus_sections)
                try:
                    while not stop.is_set():
                        with owner.transaction():
                            check(owner, run_id, "worker", "ready")
                            save_summary(owner, settings, run_id, datetime.now(timezone.utc))
                        stop.wait(30)
                finally:
                    stop.set()
                    services.shutdown(wait=True)


if __name__ == "__main__":
    main()
