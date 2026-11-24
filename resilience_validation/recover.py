"""Rebuild a fresh Git checkout, then restore the synthetic validation stack."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tarfile
import time

from experiment import HERE, STATE, sql

ROOT = HERE.parent
PORT = "18192"


def local_compose(*args, root=ROOT, env=None, input=None):
    return subprocess.run([str(root / "resilience_validation/compose"), *args], input=input, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True, env=env).stdout


def fetch(path):
    from urllib.request import ProxyHandler, build_opener
    client = build_opener(ProxyHandler({}))
    with client.open("http://127.0.0.1:" + PORT + path, timeout=12) as response:
        return response.status, response.read()


def recover():
    tag = "cold-" + secrets.token_hex(4)
    root = STATE / tag
    root.mkdir()
    backups = root / ".backups"
    backups.mkdir()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    with subprocess.Popen(["git", "archive", head], cwd=ROOT, stdout=subprocess.PIPE) as archive:
        with tarfile.open(fileobj=archive.stdout, mode="r|") as files:
            files.extractall(root, filter="data")
        if archive.wait():
            raise RuntimeError("Could not unpack the Git revision")
    env = {**os.environ, "VALIDATION_TAG": tag, "VALIDATION_PORT": PORT,
           "VALIDATION_PROJECT": "kamdispatch-hardening-" + tag}
    target = lambda *args, **kwargs: local_compose(*args, root=root, env=env, **kwargs)
    backups.mkdir(exist_ok=True)
    local_compose("stop", "-t", "2", "worker", "city-api")
    try:
        expected = sql("SELECT count(*) FROM citizen_reports")
        dumps = {
            "monitor.dump": local_compose("exec", "-T", "db", "pg_dump", "-U", "monitor", "-d", "monitor", "-Fc"),
            "city.tar": local_compose("run", "--rm", "--no-deps", "-T", "--entrypoint", "tar", "city-api",
                                      "-cf", "-", "-C", "/state", "."),
            "media.tar": local_compose("exec", "-T", "api", "tar", "-cf", "-", "-C", "/app/runtime/media", "."),
        }
    finally:
        local_compose("start", "city-api", "worker")
    for name, data in dumps.items():
        (backups / name).write_bytes(data)
    subprocess.run([sys.executable, str(root / "resilience_validation/prepare.py")], cwd=root, check=True)
    begin = time.monotonic()
    try:
        with (root / ".resilience-validation/clean-build.log").open("wb") as log:
            subprocess.run([str(root / "resilience_validation/compose"), "build", "--pull", "--no-cache",
                            "api", "city-api"], stdout=log, stderr=subprocess.STDOUT, check=True, env=env)
        build_s = round(time.monotonic() - begin, 2)
        target("up", "--wait", "--wait-timeout", "50", "db")
        target("exec", "-T", "db", "pg_restore", "-U", "monitor", "-d", "monitor", "--exit-on-error",
               input=dumps["monitor.dump"])
        for service, name, path in (("city-api", "city.tar", "/state"), ("api", "media.tar", "/app/runtime/media")):
            target("run", "--rm", "--no-deps", "-T", "--entrypoint", "tar", service,
                   "--no-same-owner", "-xf", "-", "-C", path, input=dumps[name])
        target("up", "-d", "--no-build", "gateway", "fixture")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                health = json.loads(fetch("/api/v1/health")[1])
                dashboard = json.loads(fetch("/api/v1/dashboard?run=demo")[1])
                if health["terrain_loaded"] and health["snow_solver_connected"] and dashboard["cameras"]:
                    break
            except OSError:
                pass
            time.sleep(1)
        else:
            raise TimeoutError("Restored HTTP endpoints did not become ready")
        frame = next(camera["frame_id"] for camera in dashboard["cameras"] if camera["frame_id"])
        frame_code, jpeg = fetch(f"/api/v1/frames/{frame}?run=demo")
        restored = target("exec", "-T", "db", "psql", "-XAt", "-U", "monitor", "-d", "monitor",
                          "-c", "SELECT count(*) FROM citizen_reports").decode().strip()
        sqlite = target("exec", "-T", "city-api", "python", "-c",
                        "import sqlite3; print(sqlite3.connect('/state/city-sim.sqlite3').execute("
                        "'PRAGMA integrity_check').fetchone()[0])").decode().strip()
        assert restored == expected and frame_code == 200 and jpeg.startswith(b"\xff\xd8") and sqlite == "ok"
        return {"status": "PASS", "git_sha": head, "build_no_cache_s": build_s,
                "rto_including_build_s": round(time.monotonic() - begin, 2),
                "stored_reports_before": int(expected), "stored_reports_restored": int(restored),
                "restored_frame_bytes": len(jpeg), "sqlite_integrity": sqlite,
                "archives": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                             for name, data in dumps.items()}}
    finally:
        target("down", "-v", "--remove-orphans")


if __name__ == "__main__":
    try:
        result = recover()
    except (RuntimeError, AssertionError, subprocess.SubprocessError, OSError) as error:
        result = {"status": "BLOCKED", "error": str(error)}
    (HERE / "evidence/recovery.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
