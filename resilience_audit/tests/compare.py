"""Read-only comparison of static HTTP artifacts and readiness endpoints."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

HTTP = build_opener(ProxyHandler({}))
BASES = {
    "previous_mac": ("http://127.0.0.1:8080", "http://127.0.0.1:8008"),
    "isolated_mac": ("http://127.0.0.1:18088", "http://127.0.0.1:18088"),
}
ASSET = re.compile(rb'["\'](/assets/[^"\']+)["\']')


def fetch(url):
    try:
        response = HTTP.open(url, timeout=12)
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        return {"http": response.status, "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest()}, body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("public_kamdispatch", help="HTTP origin of the public Windows gateway")
    parser.add_argument("public_city_sim", help="HTTP origin of the standalone public City Sim")
    args = parser.parse_args()
    public = args.public_kamdispatch.rstrip("/")
    out = {}
    for name, (base, city) in {"public_windows": (public, public), **BASES}.items():
        root, body = fetch(base + "/")
        paths = [path.decode() for path in ASSET.findall(body)]
        out[name] = {"/": root}
        for path in paths:
            out[name][path] = fetch(base + path)[0]
        for path in ["/static/ops/index.html", "/static/ops/map.js", "/static/ops/traffic-volume.js", "/api/v1/health"]:
            out[name][path] = fetch(city + path)[0]
        out[name]["city_landing"] = fetch(city + ("/" if name == "previous_mac" else "/city/"))[0]
        out[name]["/healthz"] = fetch(base + "/healthz")[0]
    standalone_paths = ("/", "/static/map.js", "/static/app.js", "/static/app.css", "/api/v1/health")
    for name, origin in (("separate_public_city_sim", args.public_city_sim.rstrip("/")),
                         ("isolated_standalone_city_sim", "http://127.0.0.1:18089")):
        out[name] = {path: fetch(origin + path)[0] for path in standalone_paths}
    assert out["public_windows"] == out["isolated_mac"], "Gateway HTTP artifacts differ"
    assert out["separate_public_city_sim"] == out["isolated_standalone_city_sim"], "Standalone HTTP artifacts differ"
    destination = Path(__file__).resolve().parents[1] / "evidence" / "http-parity.json"
    destination.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(destination)


if __name__ == "__main__":
    main()
