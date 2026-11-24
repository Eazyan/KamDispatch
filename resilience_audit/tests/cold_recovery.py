"""Restore the synthetic backup into fresh containers, networks and volumes."""
import json
import os
import secrets
import subprocess
import time
from datetime import datetime, timezone
from urllib.request import ProxyHandler, build_opener

from chaos import AUDIT, STATE

PORT = 18090
PROJECT = "kamdispatch-resilience-recovery-" + secrets.token_hex(4)
SOURCE = STATE / "source"
BACKUP = STATE / "backup"
CONFIG = STATE / "cold-recovery.compose.yaml"
DOCKER = ["docker", "compose", "--project-name", PROJECT, "--project-directory", SOURCE,
          "--env-file", STATE / "local.env", "-f", CONFIG]


def compose(*args, input=None):
    return subprocess.run([*DOCKER, *args], input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}, check=True).stdout


def fetch(path):
    with build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{PORT}" + path, timeout=8) as response:
        return response.status, response.read()


def restore():
    CONFIG.write_text((AUDIT / "deploy/local.compose.yaml").read_text().replace("18088", str(PORT)))
    started = time.monotonic()
    try:
        compose("up", "-d", "--wait", "--wait-timeout", "30", "db")
        compose("exec", "-T", "db", "pg_restore", "-U", "monitor", "-d", "monitor", "--exit-on-error",
                input=(BACKUP / "monitor.dump").read_bytes())
        compose("run", "--rm", "--no-deps", "-T", "--entrypoint", "sh", "city-api", "-c",
                "cat > /state/city-sim.sqlite3", input=(BACKUP / "city.sqlite3").read_bytes())
        compose("run", "--rm", "--no-deps", "-T", "--entrypoint", "sh", "api", "-c",
                "tar --no-same-owner -xf - -C /app/runtime/media",
                input=(BACKUP / "media.tar").read_bytes())
        compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "120", "gateway")
        _, payload = fetch("/api/v1/dashboard")
        dashboard = json.loads(payload)
        frame = next(camera["frame_id"] for camera in dashboard["cameras"] if camera["frame_id"])
        health, raw_city = fetch("/api/v1/health")
        city = json.loads(raw_city)
        frame_status, jpeg = fetch("/api/v1/frames/" + frame)
        assert (health == frame_status == 200 and city["terrain_loaded"] and city["snow_solver_connected"]
                and jpeg.startswith(b"\xff\xd8") and dashboard["forecasts"])
        return {"fresh_volumes": True, "rto_s": round(time.monotonic() - started, 2),
                "forecast_points": len(dashboard["forecasts"]), "frame_http": frame_status,
                "validated_jpeg_bytes": len(jpeg), "city_health": city}
    finally:
        compose("down", "-v", "--remove-orphans")


def main():
    for name in ("monitor.dump", "city.sqlite3", "media.tar"):
        if not (BACKUP / name).is_file():
            raise SystemExit("Run chaos.py F09_RESTORE before cold_recovery.py")
    output = AUDIT / "TEST_RESULTS.json"
    results = json.loads(output.read_text())
    start = datetime.now(timezone.utc)
    try:
        value = {"status": "PASS", "evidence": restore()}
    except subprocess.CalledProcessError as error:
        value = {"status": "BLOCKED", "evidence": {"error": error.stderr.decode(errors="replace")[-1200:]}}
    except (OSError, ValueError, AssertionError, StopIteration) as error:
        value = {"status": "FAIL", "evidence": {"error": str(error)}}
    value["started_at"] = start.isoformat()
    results["results"]["F10_COLD_RECOVERY"] = value
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(value, ensure_ascii=False))


if __name__ == "__main__":
    main()
