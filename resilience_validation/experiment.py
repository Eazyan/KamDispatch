"""Run experimental loads and failures against the fixed loopback validation stack."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import partial
import json
from pathlib import Path
import secrets
import subprocess
import time
from uuid import uuid4

import aiohttp
import httpx

HERE = Path(__file__).resolve().parent
STATE = HERE.parent / ".resilience-validation"
BASE = "http://127.0.0.1:18190"
STATUS = "/api/v1/status"
DASH = "/api/v1/dashboard"
CITY = "/api/v1/health"
SNOW = "/api/v1/operations/city/runs"
REPORTS = "/api/v1/citizens/reports"
TRAFFIC = "/api/v1/traffic"
SERVICES = ["api", "db", "worker", "city-api", "gateway", "fixture", "public-dashboard"]
EXPERIMENT = uuid4().hex[:12]


def dc(*args):
    return subprocess.check_output([str(HERE / "compose"), *args], text=True)


def toggle(source, mode):
    (STATE / "control" / source).write_text(mode)


def sql(query):
    return dc("exec", "-T", "db", "psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-U", "monitor", "-d", "monitor",
              "-c", query).strip()


def docker_stats():
    names = ["kamdispatch-hardening-" + service + "-1" for service in SERVICES]
    result = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}", *names],
                            capture_output=True, text=True, timeout=8)
    return [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]


async def measured_samples(fn):
    samples = []
    done = asyncio.Event()

    async def sampler():
        while not done.is_set():
            try:
                samples.append({"at": datetime.now(timezone.utc).isoformat(),
                                "containers": await asyncio.to_thread(docker_stats)})
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                await asyncio.wait_for(done.wait(), timeout=1)
            except TimeoutError:
                pass

    task = asyncio.create_task(sampler())
    try:
        result = await fn()
    finally:
        done.set()
        await task
    return {"measurements": result, "resources": samples}


async def request(client, method, path, **kwargs):
    started = time.monotonic()
    try:
        response = await client.request(method, path, **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = None
        return response.status_code, time.monotonic() - started, data
    except httpx.RequestError as error:
        return 0, time.monotonic() - started, type(error).__name__


def metrics(records, elapsed):
    durations = sorted(record[1] * 1000 for record in records)
    status = dict(sorted(Counter(record[0] for record in records).items()))
    result = {"requests": len(records), "seconds": round(elapsed, 3),
              "requests_per_second": round(len(records) / elapsed, 2),
              "http": status, "errors": sum(n for code, n in status.items() if code >= 400 or code == 0),
              "client_errors": dict(Counter(record[2] for record in records if record[0] == 0))}
    for p in (50, 95, 99):
        index = min(len(durations) - 1, (len(durations) * p + 99) // 100 - 1)
        result[f"p{p}_ms"] = round(durations[index], 2) if durations else None
    return result


async def until(condition, timeout=40, interval=.3):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        value = await condition()
        if value:
            return value, round(time.monotonic() - start, 2)
        await asyncio.sleep(interval)
    raise TimeoutError("Condition exceeded test deadline")


async def parts(client):
    res = await request(client, "GET", STATUS)
    return (res[2] or {}).get("components", {}) if res[0] == 200 else {}


async def expect_component(client, name, status, timeout=40):
    async def check():
        state = (await parts(client)).get(name)
        return state if state and state["status"] == status else None
    return await until(check, timeout)


async def load(n, seconds, paths=(DASH, DASH, DASH, STATUS)):
    began = time.monotonic()
    records = {path: [] for path in paths}
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(base_url=BASE, timeout=timeout,
                                     connector=aiohttp.TCPConnector(limit=n)) as session:
        async def user(index):
            path = paths[index % len(paths)]
            while time.monotonic() - began < seconds:
                start = time.monotonic()
                try:
                    async with session.get(path) as response:
                        await response.read()
                        record = (response.status, time.monotonic() - start, None)
                except (aiohttp.ClientError, TimeoutError) as error:
                    record = (0, time.monotonic() - start, type(error).__name__)
                records[path].append(record)

        await asyncio.gather(*(user(i) for i in range(n)))
    elapsed = time.monotonic() - began
    return {"users": n, "window_s": seconds, "driver": "aiohttp",
            "by_path": {path: metrics(rows, elapsed) for path, rows in records.items()},
            **metrics([row for rows in records.values() for row in rows], elapsed)}


async def citizens(client):
    description = "Synthetic validation " + EXPERIMENT
    arrivals = time.monotonic()
    ids = [(str(uuid4()), str(uuid4())) for _ in range(1000)]

    async def report(index):
        await asyncio.sleep(max(0, arrivals + index * .060 - time.monotonic()))
        actor, token = ids[index]
        return await request(client, "POST", REPORTS, headers={"X-Citizen-Browser-Id": actor},
                             data={"description": description, "latitude": "53.04", "longitude": "158.64",
                                   "request_id": token})

    initial = await asyncio.gather(*(report(i) for i in range(1000)))
    rate = metrics(initial, time.monotonic() - arrivals)
    repeat_start = time.monotonic()
    replay = await asyncio.gather(*(report(i) for i in range(100)))
    rate["replay"] = metrics(replay, time.monotonic() - repeat_start)
    rate["returned_same_ids"] = sum(a[0] == b[0] == 201 and a[2]["id"] == b[2]["id"]
                                    for a, b in zip(initial, replay))
    stored, distinct = map(int, (await asyncio.to_thread(sql,
        f"SELECT count(*),count(DISTINCT request_id) FROM citizen_reports WHERE description='{description}'")).split("|"))
    rate["stored"] = stored
    rate["duplicates"] = stored - distinct
    rate["missing"] = len(ids) - distinct
    return rate


async def generic_status(client, paths):
    pairs = await asyncio.gather(*(request(client, "GET", path) for path in paths))
    return {path: value[0] for path, value in zip(paths, pairs)}


async def weather_poll():
    await asyncio.to_thread(dc, "exec", "-T", "worker", "python", "-c",
                            "from monitoring.worker import forecast_tick,Settings; import httpx;"
                            "with_client=httpx.Client(); forecast_tick(Settings(),'preview',with_client)")


async def sources(client):
    await expect_component(client, "weather", "healthy")
    await expect_component(client, "buses", "healthy")
    previous = (await request(client, "GET", DASH))[2]["forecasts"]
    output = {}
    try:
        for mode in ("down", "invalid", "stale", "fallback"):
            toggle("weather", mode)
            started = time.monotonic()
            await weather_poll()
            status, _, dashboard = await request(client, "GET", "/public/dashboard")
            output[mode] = {"http": status, "elapsed_s": round(time.monotonic() - started, 2),
                            "kept_previous": dashboard["forecasts"] == previous,
                            "status": dashboard["weather_source"]["status"],
                            "reason": dashboard["weather_source"]["reason"],
                            "horizons": sorted({p["horizon"] for point in dashboard["forecasts"] for p in point["periods"]})}
        toggle("weather", "ok")
        await weather_poll()
        _, output["weather_recover_s"] = await expect_component(client, "weather", "healthy")
        for mode in ("mixed", "stale", "wrong-graph"):
            toggle("traffic", mode)
            async def traffic_check():
                code, _, traffic = await request(client, "GET", TRAFFIC)
                if code != 200 or not traffic:
                    return None
                counts = dict(Counter(s["status"] for s in traffic["segments"]))
                matched = ({"stale", "congested"} <= counts.keys() if mode == "mixed" else
                           set(counts) == {"stale"} if mode == "stale" else traffic["source"]["reason"] == "invalid_publication")
                return {"http": code, "intervals": counts, "source_reason": traffic["source"]["reason"]} if matched else None
            item, item_time = await until(traffic_check)
            output["traffic_" + mode] = {**item, "detect_s": item_time}
        toggle("traffic", "ok")
        _, output["traffic_recover_s"] = await expect_component(client, "traffic", "healthy")
        toggle("buses", "frozen")
        # RNIS reports 200 but its timestamp no longer advances.
        frozen, output["bus_detect_s"] = await expect_component(client, "buses", "stale", 110)
        output["bus_stale"] = frozen
        output["bus_map_positions"] = len((await request(client, "GET", DASH))[2]["bus_map"]["positions"])
        # Deliberately let the last successful weather poll exceed the 75s fixture TTL.
        status, _, dashboard = await request(client, "GET", "/public/dashboard")
        output["aged_weather"] = {"http": status, "status": dashboard["weather_source"]["status"],
                                  "stored_points": len(dashboard["forecasts"])}
    finally:
        for source in ("weather", "traffic", "buses"):
            toggle(source, "ok")
        await weather_poll()
    _, output["bus_recover_s"] = await expect_component(client, "buses", "healthy", 55)
    return output


async def cascade(client):
    async def running():
        return await load(100, 9, paths=("/public/dashboard", DASH, STATUS, CITY))
    await expect_component(client, "worker", "healthy")
    worker_started = time.monotonic()
    await asyncio.to_thread(dc, "stop", "-t", "2", "worker")
    _, _ = await expect_component(client, "worker", "stale", 55)
    worker_detection = round(time.monotonic() - worker_started, 2)
    before = await generic_status(client, ("/", "/healthz", DASH, "/public/dashboard", CITY, STATUS))
    try:
        for source in ("weather", "traffic", "buses"):
            toggle(source, "down")
        await asyncio.to_thread(dc, "stop", "-t", "2", "db")
        _, db_detection = await expect_component(client, "postgresql", "unavailable", 20)
        during = await generic_status(client, ("/", "/healthz", DASH, "/public/dashboard", CITY, STATUS))
        duty = await running()
        # Cold monitoring process must still start with PostgreSQL stopped.
        await asyncio.to_thread(dc, "restart", "api")
        _, reboot_detection = await expect_component(client, "postgresql", "unavailable", 30)
        cold = await generic_status(client, ("/", DASH, "/public/dashboard", CITY, STATUS))
        await asyncio.to_thread(dc, "stop", "-t", "2", "city-api")
        _, city_detection = await expect_component(client, "city_sim", "unavailable", 25)
        tail_status = (await request(client, "GET", STATUS))[0]
    finally:
        for source in ("weather", "traffic", "buses"):
            toggle(source, "ok")
        await asyncio.to_thread(dc, "start", "db", "city-api", "worker")
    _, db_recovery = await expect_component(client, "postgresql", "healthy", 45)
    _, city_recovery = await expect_component(client, "city_sim", "healthy", 45)
    _, worker_recovery = await expect_component(client, "worker", "healthy", 50)
    return {"before": before, "during": during, "after_api_reboot": cold, "load": duty,
            "after_two_internal_failures_status_http": tail_status,
            "detected_s": {"worker_from_stop": worker_detection, "postgresql_after_stop": db_detection,
                           "status_available_after_reboot": reboot_detection, "city_after_stop": city_detection},
            "recovered_s_after_start": {"postgresql": db_recovery, "city": city_recovery, "worker": worker_recovery}}


async def dispatch(client):
    path = "/api/v1/dispatch"
    label = "Synthetic task " + EXPERIMENT
    report = await request(client, "POST", path + "/reports", json={
        "id": str(uuid4()), "district": "Synthetic district", "address": label,
        "kind": "drift", "channel": "phone", "description": label,
    })
    assert report[0] == 200, report
    order = await request(client, "POST", path + "/tasks", json={
        "location_key": report[2]["location_key"], "kind": "drift", "work_kind": "inspect",
    })
    assert order[0] == 200, order
    task = path + "/tasks/" + order[2]["id"]
    assignment = {"version": 0, "assignee": "Synthetic crew",
                  "due_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()}
    first = await request(client, "PUT", task + "/assignment", json=assignment)
    assert first[0] == 200, first
    # Discard first acknowledgment then resend the original optimistic version after reconnection.
    await asyncio.to_thread(dc, "stop", "-t", "2", "api")
    unavailable = (await request(client, "PUT", task + "/assignment", json=assignment))[0]
    await asyncio.to_thread(dc, "start", "api")
    await expect_component(client, "postgresql", "healthy", 30)
    repeated = await request(client, "PUT", task + "/assignment", json=assignment)
    confirmed = await request(client, "PUT", task + "/receipt", json={"version": 1})
    duplicate = await request(client, "PUT", task + "/receipt", json={"version": 1})
    history = await request(client, "GET", task + "/history")
    return {"during_disconnect_http": unavailable, "first_assignment_http": first[0],
            "replay_old_version_http": repeated[0], "accept_http": confirmed[0],
            "accept_repeat_identical": duplicate[2] == confirmed[2], "history_http": history[0],
            "history_actions": [row["action"] for row in (history[2] or [])]}


async def snow(client):
    results = {}
    freshness = secrets.randbelow(10000) / 100000
    for count in (1, 5, 10):
        start = time.monotonic()
        pending = {}
        payloads = [{"source": "manual", "hours": 1, "wind_speed_ms": 7 + freshness,
                     "wind_from_deg": 200 + count + index, "snowfall_cm_h": 2,
                     "zone": [[158.641, 53.036], [158.643, 53.036], [158.642, 53.037]]}
                    for index in range(count)]
        requests = await asyncio.gather(*(request(client, "POST", SNOW, json=payload) for payload in payloads))
        for code, _, body in requests:
            if code == 202:
                pending[body["run_id"]] = body["status"]
        summary = metrics(requests, time.monotonic() - start)
        while any(state in {"pending", "running"} for state in pending.values()) and time.monotonic() - start < 120:
            await asyncio.sleep(1)
            active = [job for job in pending if pending[job] in {"pending", "running"}]
            polled = await asyncio.gather(*(request(client, "GET", f"{SNOW}/{job}") for job in active))
            for job, (code, _, result) in zip(active, polled):
                if code == 200:
                    pending[job] = result["status"]
        summary["jobs"] = dict(Counter(pending.values()))
        summary["seconds_to_terminal"] = round(time.monotonic() - start, 2)
        results[str(count)] = summary
    invalid = await request(client, "POST", SNOW, json={"source": "manual", "hours": 99, "wind_speed_ms": -8})
    await asyncio.to_thread(dc, "pause", "city-api")
    try:
        paused = await request(client, "GET", CITY, timeout=2)
        independent = await generic_status(client, ("/", STATUS))
    finally:
        await asyncio.to_thread(dc, "unpause", "city-api")
    _, recovery = await expect_component(client, "city_sim", "healthy", 25)
    return {"jobs": results, "invalid_http": invalid[0], "paused": {"http": paused[0], "seconds": round(paused[1], 2),
             "error": paused[2], "independent": independent, "recover_s": recovery}}


async def main(args):
    results_path = HERE / "evidence/experiments.json"
    results = json.loads(results_path.read_text()) if results_path.exists() else {}
    async with httpx.AsyncClient(base_url=BASE, timeout=10, trust_env=False,
                                 limits=httpx.Limits(max_connections=600, max_keepalive_connections=500)) as client:
        await expect_component(client, "postgresql", "healthy", 40)
        options = {"load": lambda: load_series(args.seconds), "citizens": partial(citizens, client),
                   "sources": partial(sources, client), "dispatch": partial(dispatch, client),
                   "snow": partial(snow, client), "cascade": partial(cascade, client)}
        for name in args.scenarios or options:
            print(f"Starting {name}", flush=True)
            if name == "load":
                data = await options[name]()
            else:
                try:
                    data = await measured_samples(options[name])
                except (AssertionError, TimeoutError, subprocess.SubprocessError, TypeError, KeyError) as error:
                    data = {"blocked": str(error)}
            results[name] = data
            results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
            print(f"Recorded {name}", flush=True)


async def load_series(seconds):
    out = []
    for n in (10, 50, 100, 500):
        out.append(await measured_samples(partial(load, n, seconds)))
        print(f"Load {n}: {out[-1]['measurements']['http']}", flush=True)
        await asyncio.sleep(2)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*", choices=("load", "citizens", "sources", "dispatch", "snow", "cascade"))
    parser.add_argument("--seconds", type=float, default=8)
    asyncio.run(main(parser.parse_args()))
