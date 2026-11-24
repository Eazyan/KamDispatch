"""Prepare the standalone City Sim from its pinned Git base and deployed patch."""
import hashlib
import subprocess
import tarfile

from prepare import DEPLOY, STATE, windows_line_endings

REFERENCE = "7d567eb0a1732895f0e5443ee0e10bfe90f7b69d"
UPSTREAM = "https://github.com/NDaVS/kamcha-snow-sim.git"
STANDALONE = STATE / "standalone"
CHECKOUT = STANDALONE / "source"
BUILD = STANDALONE / "build"


def git(*args):
    return subprocess.check_output(["git", "-C", str(CHECKOUT), *args])


def main():
    if not (CHECKOUT / ".git").exists():
        STANDALONE.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "--branch", "Neverrie", UPSTREAM, str(CHECKOUT)], check=True)
    if subprocess.run(["git", "-C", str(CHECKOUT), "cat-file", "-e", REFERENCE], capture_output=True).returncode:
        git("fetch", "--depth", "1", "origin", REFERENCE)
    BUILD.mkdir(parents=True, exist_ok=True)
    with subprocess.Popen(["git", "-C", str(CHECKOUT), "archive", REFERENCE], stdout=subprocess.PIPE) as archive:
        with tarfile.open(fileobj=archive.stdout, mode="r|") as files:
            files.extractall(BUILD, filter="data")
        if archive.wait():
            raise RuntimeError("git archive failed")
    subprocess.run(["patch", "-s", "-p1", "-i", str(DEPLOY / "windows-standalone.patch")], cwd=BUILD, check=True)
    for raw in git("ls-tree", "-r", "--name-only", "-z", REFERENCE).split(b"\0"):
        if not raw:
            continue
        path = BUILD / raw.decode()
        if path.relative_to(BUILD).parts[0] in {"snowmap", "kernels"}:
            continue
        if path.is_file() and not path.is_symlink():
            before = path.read_bytes()
            after = windows_line_endings(before)
            if before != after:
                path.write_bytes(after)
    for path, expected in {
        "city_sim/api.py": "e551a266971af425eda58c205f334826a3cb3898d3821f6c498c9d3873a77ac5",
        "city_sim/city_snow.py": "71a24da96e741287799b4cf551f4b0d6e9f068a53ff002c63ec3931d18e9e8d9",
        "city_sim/web/index.html": "d65d539c35f27d3e52780a966f56b3fc7df3d213fcd4cf62e4dd7c38a2bcb1b2",
        "city_sim/web/map.js": "9b71e5eeb6ae10949620915391cdc5157e1426fc9cc9fc8679e0d3f085dc155d",
    }.items():
        assert hashlib.sha256((BUILD / path).read_bytes()).hexdigest() == expected, path
    print(f"Verified standalone source {REFERENCE} and deployed snow patch")


if __name__ == "__main__":
    main()
