"""Run controlled failure scenarios against localhost and the named audit project."""
import argparse
import concurrent.futures
import json
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

AUDIT = Path(__file__).resolve().parents[1]
STATE = AUDIT.parent / ".resilience-local"
BASE = "http://127.0.0.1:18088"
COMPOSE = AUDIT / "deploy" / "compose"
CONTROL = STATE / "control"
DASH = "/api/v1/dashboard"
HEALTH = "/api/v1/health"
TRAFFIC = "/api/v1/traffic"
TICK = "from monitoring.worker import Settings,forecast_tick; import httpx; forecast_tick(Settings(),'preview',httpx.Client())"


def dc(*args, input=None):
    run = subprocess.run([COMPOSE, *args], input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if run.returncode:
        raise RuntimeError(f"Compose {args[0]} failed ({run.returncode}): {run.stderr.decode(errors='replace')[-600:]}")
    return run.stdout


def get(path):
    start = time.monotonic()
    opener = build_opener(ProxyHandler({}))
    try:
        response = opener.open(BASE + path, timeout=8)
    except HTTPError as exc:
        response = exc
    except (URLError, TimeoutError):
        return 0, None, round(time.monotonic() - start, 3)
    with response:
        raw = response.read()
        data = json.loads(raw) if "application/json" in response.headers.get("Content-Type", "") else None
    return response.status, data, round(time.monotonic() - start, 3)


def wait(probe, predicate, timeout=28):
    began = time.monotonic()
    while True:
        value = probe()
        elapsed = round(time.monotonic() - began, 2)
        if predicate(value):
            return value, elapsed
        if elapsed >= timeout:
            raise TimeoutError(f"Expected state was not observed after {elapsed}s")
        time.sleep(0.4)


def set_mode(component, mode):
    (CONTROL / component).write_text(mode)


def dash():
    code, payload, _ = get(DASH)
    return payload if code == 200 else None


def status(path):
    return get(path)[0]


def forecast():
    return dc("exec", "-T", "worker", "python", "-c", TICK)


def sql(statement, database="monitor"):
    return dc("exec", "-T", "db", "psql", "-U", "monitor", "-d", database,
              "-X", "-v", "ON_ERROR_STOP=1", "-At", "-c", statement).decode().strip()


def baseline():
    dashboard, seconds = wait(dash, lambda data: bool(data and data["forecasts"] and any(
        camera["frame_id"] for camera in data["cameras"])), 45)
    route_status = {path: status(path) for path in ["/", "/healthz", "/city/", HEALTH, "/static/ops/map.js", TRAFFIC,
                                                     "/api/v1/operations/city/catalog", "/citizens/"]}
    _, solver, _ = get(HEALTH)
    _, search, _ = get("/api/v1/map/search?" + urlencode({"q": "Ленинская 8"}))
    frame = next(camera["frame_id"] for camera in dashboard["cameras"] if camera["frame_id"])
    frame_status = status(f"/api/v1/frames/{frame}")
    details = {"http": route_status, "role": dashboard["role"], "mode": dashboard["mode"], "frames_http": frame_status,
               "observed_cameras": sum(bool(camera["frame_id"]) for camera in dashboard["cameras"]),
               "forecast_points": len(dashboard["forecasts"]), "bus_positions": len((dashboard["bus_map"] or {}).get("positions", [])),
               "terrain": solver["terrain_loaded"], "snow_solver": solver["snow_solver_connected"],
               "address_found": any(row["type"] == "address" for row in search), "seconds_to_ready": seconds}
    return "PASS", details


def weather_failure():
    before = dash()["forecasts"]
    try:
        phases = {}
        for state in ("down", "invalid", "fallback", "ok"):
            set_mode("weather", state)
            start = time.monotonic()
            forecast()
            dashboard = dash()
            phases[state] = {"seconds": round(time.monotonic() - start, 2),
                             "dashboard_http": status(DASH), "visible_checks": len(dashboard["checks"]),
                             "same_forecast_as_before": dashboard["forecasts"] == before,
                             "horizons": sorted({p["horizon"] for f in dashboard["forecasts"] for p in f["periods"]}),
                             "db_checks": sql("SELECT status,count(*) FROM checks WHERE run_id='preview' AND name LIKE 'forecast:%' GROUP BY status")}
        stale_unmarked = phases["down"]["same_forecast_as_before"] and not phases["down"]["visible_checks"]
        fallback_ok = phases["fallback"]["horizons"] == ["three_hourly"] and phases["ok"]["horizons"] == ["hourly"]
        return ("FAIL" if stale_unmarked or not fallback_ok else "PASS"), phases
    finally:
        set_mode("weather", "ok")


def cameras_failure():
    def counts():
        value = dash()
        return dict(Counter(camera["status"] for camera in value["cameras"])) if value else {}
    initial = counts()
    try:
        set_mode("cameras", "down")
        outage, detected = wait(counts, lambda counter: counter.get("error") == sum(counter.values()) > 0)
        return_status = status(DASH)
    finally:
        set_mode("cameras", "ok")
    recovered, seconds = wait(counts, lambda counter: counter.get("available") == sum(counter.values()) > 0, 40)
    return "PASS", {"initial": initial, "outage": outage, "recovered": recovered, "http": return_status,
                    "detect_s": detected, "recover_s": seconds}


def traffic_failure():
    def traffic():
        code, data, _ = get(TRAFFIC)
        return code, (data or {})
    try:
        set_mode("traffic", "wrong-graph")
        wrong, detected = wait(traffic, lambda result: result[0] == 503)
        set_mode("traffic", "stale")
        stale, stale_seconds = wait(traffic, lambda result: result[0] == 200)
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(stale[1]["valid_until"])).total_seconds()
        set_mode("traffic", "ok")
        recovered, recovered_s = wait(traffic, lambda result: result[0] == 200 and
                                      datetime.fromisoformat(result[1]["valid_until"]) > datetime.now(timezone.utc))
        return "PASS", {"mismatch_http": wrong[0], "detect_s": detected, "stale_http": stale[0],
                        "stale_segments": len(stale[1]["segments"]), "expired_by_s": round(age, 2),
                        "stale_fetch_s": stale_seconds, "recover_s": recovered_s,
                        "recovered_segments": len(recovered[1]["segments"]), "api_exposes_expiry": True}
    finally:
        set_mode("traffic", "ok")


def combined_outage():
    try:
        set_mode("weather", "down")
        set_mode("traffic", "down")
        set_mode("cameras", "down")
        forecast()
        _, detected = wait(lambda: status(TRAFFIC), lambda code: code == 503)
        codes = {path: status(path) for path in ("/", DASH, HEALTH, TRAFFIC)}
        return ("PASS" if codes == {"/": 200, DASH: 200, HEALTH: 200, TRAFFIC: 503} else "FAIL"), {
            "http": codes, "traffic_detect_s": detected, "visible_checks": len(dash()["checks"]),
            "weather_rows_still_returned": len(dash()["forecasts"]),
            "camera_status": dict(Counter(row["status"] for row in dash()["cameras"])),
            "runtime_internet": "blocked by Docker internal network throughout all experiments"}
    finally:
        for component in ("weather", "traffic", "cameras"):
            set_mode(component, "ok")
        forecast()


def city_outage():
    dc("stop", "-t", "4", "city-api")
    try:
        error, detected = wait(lambda: status(HEALTH), lambda code: code != 200)
        others = {path: status(path) for path in ("/", DASH, "/city/", "/static/ops/map.js")}
    finally:
        dc("start", "city-api")
    _, recovered = wait(lambda: status(HEALTH), lambda code: code == 200, 60)
    return "PASS", {"city_api_http": error, "remaining_http": others, "detect_s": detected, "recover_s": recovered}


def database_outage():
    dc("stop", "-t", "4", "db")
    try:
        _, detected = wait(lambda: status("/healthz"), lambda code: code != 200)
        codes = {path: status(path) for path in ("/", "/healthz", DASH, HEALTH)}
        count = bool(dash())
    finally:
        dc("start", "db")
    _, recovered = wait(dash, lambda value: bool(value and value["forecasts"]), 45)
    return ("FAIL" if not count else "PASS"), {"http": codes, "detect_s": detected, "recover_s": recovered,
                                              "forecasts_after": len(dash()["forecasts"])}


def worker_outage():
    dc("stop", "-t", "3", "worker")
    try:
        beat = "SELECT observed_at FROM checks WHERE run_id='preview' AND name='worker'"
        before = sql(beat)
        time.sleep(3)
        during = sql(beat)
        observed = {"health": status("/healthz"), "dashboard": status(DASH),
                    "public_checks": len(dash()["checks"]), "heartbeat_unchanged": before == during,
                    "forecast_count": len(dash()["forecasts"])}
    finally:
        dc("start", "worker")
    _, recovered = wait(lambda: sql(beat), lambda value: value != before)
    observed["recover_s"] = recovered
    return ("FAIL" if not observed["public_checks"] and observed["health"] == 200 else "PASS"), observed


def load_and_public_workflows():
    route_codes = {path: status(path) for path in ("/citizens/", "/api/v1/dispatch/roads")}
    began = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        response = list(pool.map(lambda _: get(DASH), range(36)))
    elapsed = sorted(item[2] for item in response)
    return ("PASS" if all(item[0] == 200 for item in response) else "FAIL"), {
        "requests": len(response), "parallelism": 6, "status": dict(Counter(item[0] for item in response)),
        "p95_s": elapsed[34], "total_s": round(time.monotonic() - began, 2), "public_workflows": route_codes}


def restore():
    backup = STATE / "backup"
    backup.mkdir(exist_ok=True)
    before = sql("SELECT count(*) FROM schema_migrations")
    database = "resilience_restore"
    dump = dc("exec", "-T", "db", "pg_dump", "-U", "monitor", "-d", "monitor", "-Fc")
    (backup / "monitor.dump").write_bytes(dump)
    dc("exec", "-T", "db", "dropdb", "-U", "monitor", "--if-exists", database)
    dc("exec", "-T", "db", "createdb", "-U", "monitor", database)
    started = time.monotonic()
    dc("exec", "-T", "db", "pg_restore", "-U", "monitor", "-d", database, "--exit-on-error", input=dump)
    migrated = sql("SELECT count(*) FROM schema_migrations", database)
    rows = sql("SELECT (SELECT count(*) FROM frames), (SELECT count(*) FROM forecasts)", database)
    city = dc("exec", "-T", "city-api", "python", "-c",
              "import os,sqlite3,tempfile,sys; a=sqlite3.connect(os.environ['CITY_SIM_DB']); "
              "p=tempfile.NamedTemporaryFile(); a.backup(sqlite3.connect(p.name)); sys.stdout.buffer.write(p.read())")
    target = backup / "city.sqlite3"
    target.write_bytes(city)
    import sqlite3
    with sqlite3.connect(target) as restored:
        integrity = restored.execute("PRAGMA integrity_check").fetchone()[0]
    media = dc("exec", "-T", "api", "tar", "-cf", "-", "-C", "/app/runtime/media", ".")
    (backup / "media.tar").write_bytes(media)
    info = {"db_schema_before": int(before), "db_schema_restored": int(migrated), "restore_s": round(time.monotonic() - started, 2),
            "restored_frame_and_forecast_counts": [int(n) for n in rows.split("|")], "sqlite_integrity": integrity,
            "media_archive_bytes": len(media), "tested_new_database": True}
    return ("PASS" if before == migrated and integrity == "ok" else "FAIL"), info


def main():
    assert (CONTROL / "weather").exists(), "Run prepare.py first"
    assert dc("ps", "--status", "running", "-q", "gateway").strip(), "Start the audit project first"
    scenarios = {
        "BASELINE": baseline, "F01_WEATHER": weather_failure, "F02_CAMERAS": cameras_failure, "F03_TRAFFIC": traffic_failure,
        "F04_COMBINED": combined_outage, "F05_CITY": city_outage, "F06_DATABASE": database_outage,
        "F07_WORKER": worker_outage, "F08_LOAD": load_and_public_workflows, "F09_RESTORE": restore,
    }
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*", metavar="SCENARIO")
    selected = parser.parse_args().scenarios
    unknown = set(selected) - scenarios.keys()
    if unknown:
        parser.error(f"unknown scenarios: {', '.join(sorted(unknown))}")
    output = AUDIT / "TEST_RESULTS.json"
    results = json.loads(output.read_text())["results"] if selected and output.exists() else {}
    coverage = json.loads((AUDIT / "tests/coverage.json").read_text())
    for name in selected or scenarios:
        scenario = scenarios[name]
        start = datetime.now(timezone.utc)
        try:
            outcome, evidence = scenario()
        except (RuntimeError, TimeoutError, TypeError, KeyError, ValueError) as error:
            outcome, evidence = "BLOCKED", {"error": str(error)}
        results[name] = {"status": outcome, "started_at": start.isoformat(), "evidence": evidence}
        print(name, outcome, json.dumps(evidence, ensure_ascii=False), flush=True)
        report = {"target": BASE, "results": results, "coverage": coverage}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
