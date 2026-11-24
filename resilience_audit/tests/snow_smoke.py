"""Submit a small synthetic snow scenario to both local City APIs."""
import concurrent.futures
import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

PATH = "/api/v1/operations/city/runs"
REQUEST = {"source": "manual", "hours": 1, "wind_from_deg": 270, "wind_speed_ms": 8, "snowfall_cm_h": 2,
           "zone": [[158.641, 53.036], [158.643, 53.036], [158.642, 53.037]]}


def call(base, path, payload=None):
    request = Request(base + path, data=json.dumps(payload).encode() if payload else None,
                      headers={"Content-Type": "application/json"})
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=20)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def run(port):
    start = time.monotonic()
    base = f"http://127.0.0.1:{port}"
    code, body = call(base, PATH, REQUEST)
    if code != 202:
        return {"status": "FAIL", "submit_http": code, "body": body}
    path = f"{PATH}/{body['run_id']}"
    timeouts = 0
    while time.monotonic() - start < 160:
        try:
            code, result = call(base, path)
        except TimeoutError:
            timeouts += 1
            continue
        if code != 200 or result["status"] == "failed":
            return {"status": "FAIL", "http": code, "body": result}
        if result["status"] == "completed":
            sample_status, sample = call(base, path + "/sample?lon=158.642&lat=53.0363")
            return {"status": "PASS" if sample_status == 200 else "FAIL", "run_id": body["run_id"],
                    "seconds": round(time.monotonic() - start, 2), "sample_http": sample_status, "poll_timeouts": timeouts,
                    "snow_cm": sample.get("snow_cm"), "completed_tiles": result.get("completed_tiles")}
        time.sleep(2)
    return {"status": "BLOCKED", "reason": "Local wait exceeded 160 s", "run_id": body["run_id"]}


if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = dict(zip(("gateway", "standalone"), executor.map(run, (18088, 18089))))
    output = Path(__file__).resolve().parents[1] / "evidence" / "snow-smoke.json"
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print(output)
