"""Reconstruct the Windows checkout and generate local-only configuration."""
import hashlib
import json
import os
import secrets
import subprocess
import tarfile
from pathlib import Path

BASE = "1c3cdd82d29958242c1bcd91d861cf082b493c8f"
TREE = "adc5e525b4b7e1c8a78dcaa1ea398fedb45b3502"
OVERLAY = ("city-map/index.html", "city-map/map.js", "city-map/traffic-volume.js")
DEPLOY = Path(__file__).resolve().parent
ROOT = DEPLOY.parents[1]
STATE = ROOT / ".resilience-local"
SOURCE = STATE / "source"


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def object_id(kind, data):
    return hashlib.sha1(f"{kind} {len(data)}\0".encode() + data).hexdigest()


def deployed_tree(tree):
    rows = []
    for record in git("ls-tree", "-z", tree).split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        if name == b"city-map" and kind == b"tree":
            entries = {}
            for row in git("ls-tree", "-z", oid.decode()).split(b"\0"):
                if row:
                    meta, path = row.split(b"\t", 1)
                    entry_mode, _, entry_oid = meta.split()
                    entries[path] = (entry_mode.lstrip(b"0"), entry_oid)
            for path in OVERLAY:
                entries[Path(path).name.encode()] = (b"100644", object_id("blob", (SOURCE / path).read_bytes()).encode())
            data = b"".join(
                entry_mode + b" " + path + b"\0" + bytes.fromhex(entry_oid.decode())
                for path, (entry_mode, entry_oid) in sorted(
                    entries.items(), key=lambda item: item[0] + (b"/" if item[1][0] == b"40000" else b"")
                )
            )
            oid = object_id("tree", data).encode()
        rows.append(mode.lstrip(b"0") + b" " + name + b"\0" + bytes.fromhex(oid.decode()))
    return object_id("tree", b"".join(rows))


def windows_line_endings(data):
    if b"\x00" in data[:8000] or b"\r\n" in data:
        return data
    return data.replace(b"\n", b"\r\n")


def main():
    SOURCE.mkdir(parents=True, exist_ok=True)
    with subprocess.Popen(["git", "-C", str(ROOT), "archive", BASE], stdout=subprocess.PIPE) as archive:
        with tarfile.open(fileobj=archive.stdout, mode="r|") as files:
            files.extractall(SOURCE, filter="data")
        if archive.wait():
            raise RuntimeError("git archive failed")
    (SOURCE / OVERLAY[-1]).unlink(missing_ok=True)
    subprocess.run(["patch", "-s", "-p1", "-i", str(DEPLOY / "windows-map.patch")], cwd=SOURCE, check=True)
    assert deployed_tree(BASE) == TREE, "Reconstructed source differs from the deployed Git tree"
    for raw in git("ls-tree", "-r", "--name-only", "-z", BASE).split(b"\0"):
        if not raw:
            continue
        path = SOURCE / raw.decode()
        if path.is_file() and not path.is_symlink():
            before = path.read_bytes()
            after = windows_line_endings(before)
            if before != after:
                path.write_bytes(after)
    for path in OVERLAY:
        (SOURCE / path).write_bytes(windows_line_endings((SOURCE / path).read_bytes()))
    for name, expected in {
        "monitoring/api.py": "53f10f7ea85c733564835b40a698c21d385c1af1ecb83a13734164c891668847",
        "city-sim/city_sim/api.py": "e02952ffd3d1761f96632f13f3d9375bfdd99801f2ee8489550ae8b327c52b82",
        "city-map/map.js": "87e1f3728f04c404ab6e7f5c3964f841952364b56597376a7bb4d02735dfc611",
    }.items():
        assert hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() == expected, name
    (SOURCE / "runtime/citizens").mkdir(parents=True, exist_ok=True)
    subprocess.run(["python3", "fixtures/prepare_demo.py", *[f"fixtures/sample/{i}.png" for i in range(1, 5)]],
                   cwd=SOURCE, check=True)
    (STATE / "public_dashboard.py").write_bytes((DEPLOY / "public_dashboard.py").read_bytes().replace(
        b"__PUBLIC_CITY_MAP_URL__", b"http://127.0.0.1:18088/city/"))
    env = STATE / "local.env"
    if not env.exists():
        with open(env, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as output:
            output.write(f"POSTGRES_PASSWORD={secrets.token_hex(24)}\n")
    control = STATE / "control"
    control.mkdir(exist_ok=True)
    cameras = json.loads((SOURCE / "monitoring/cameras.json").read_bytes())
    for camera in cameras:
        camera.update(kind="jpeg", url=f"http://fixtures:8081/camera/{camera['id']}", interval_seconds=10)
    (control / "cameras.json").write_text(json.dumps(cameras, ensure_ascii=False), encoding="utf-8")
    for name in ("weather", "cameras", "traffic"):
        mode = control / name
        if not mode.exists():
            mode.write_text("ok")
    certs = STATE / "certs"
    certs.mkdir(exist_ok=True)
    if not (certs / "server.key").exists():
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                        "-subj", "/CN=api.openweathermap.org", "-addext", "subjectAltName=DNS:api.openweathermap.org",
                        "-keyout", str(certs / "server.key"), "-out", str(certs / "server.pem")],
                       check=True, capture_output=True)
        # The fixture process runs as UID 10001 inside a private bind mount.
        (certs / "server.key").chmod(0o644)
    print(f"Verified reconstructed Windows Git tree {TREE}")


if __name__ == "__main__":
    main()
