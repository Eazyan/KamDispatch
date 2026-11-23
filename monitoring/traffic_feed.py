"""Read-only display projection of the Windows worker's graph observations."""

from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from functools import lru_cache
import json
from math import cos, hypot, isfinite, radians
from threading import Lock
from time import monotonic

import httpx

from .config import ROOT
from .traffic_graph import aware, export_graph

GRAPH_FILE = ROOT / "snegoplan/mapprep/out/graph_v3.json"
CLASSES = {"free", "slow", "congested"}
MAX_FEED_BYTES = 12 * 1024 * 1024


@lru_cache(maxsize=1)
def graph():
    return export_graph(GRAPH_FILE.read_bytes())


def interval_line(points, distances, start, end):
    def interpolate(distance):
        index = min(bisect_right(distances, distance) - 1, len(points) - 2)
        left, right = points[index:index + 2]
        span = distances[index + 1] - distances[index]
        fraction = (distance - distances[index]) / span if span else 0
        return [a + (b - a) * fraction for a, b in zip(left, right)]

    return [interpolate(start), *points[bisect_right(distances, start):bisect_left(distances, end)], interpolate(end)]


def project(latest, roads):
    if latest["graph_sha256"] != roads["graph_sha256"]:
        raise ValueError("Traffic feed uses a different graph")
    if latest["source"] != "yandex_maps_ui" or latest["status"] not in {"complete", "partial"}:
        raise ValueError("Unexpected traffic publication")

    segments = []
    expires_at = None
    for road in roads["segments"]:
        observations = [row for row in latest["segments"].get(road["id"], []) if row["status"] in CLASSES]
        if not observations:
            continue
        points = road["coordinates"]
        scale = cos(radians(points[0][1]))
        distances = [0.0]
        for (lon1, lat1), (lon2, lat2) in zip(points, points[1:]):
            distances.append(distances[-1] + hypot((lon2 - lon1) * scale, lat2 - lat1))
        if distances[-1] == 0:
            continue
        unit = distances[-1] / road["length_m"]
        for row in observations:
            a, b = map(float, row["interval_m"])
            if not (isfinite(a) and isfinite(b) and -1e-6 <= a < b <= road["length_m"] + 1e-6):
                raise ValueError("Invalid traffic interval")
            a, b = max(a, 0), min(b, road["length_m"])
            if b - a < 1e-6:
                continue
            observed = aware(row["timestamp"])
            expires_at = observed if expires_at is None else min(expires_at, observed)
            segments.append({
                "corridor_id": road["id"],
                "status": row["status"],
                "coordinates": interval_line(points, distances, a * unit, min(b * unit, distances[-1])),
            })
    published = aware(latest["published_at"])
    return {
        "published_at": published.isoformat(),
        "valid_until": datetime.fromtimestamp((expires_at or published).timestamp() + 600, timezone.utc).isoformat(),
        "status": latest["status"],
        "segments": segments,
    }


class TrafficFeed:
    def __init__(self, url):
        self.url = url
        self.lock = Lock()
        self.next_fetch = 0
        self.result = None

    def latest(self):
        with self.lock:
            if monotonic() >= self.next_fetch:
                headers = {"Host": "127.0.0.1:8765", "Accept": "application/json"}
                with httpx.Client(timeout=5, trust_env=False) as client:
                    with client.stream("GET", self.url + "/api/latest", headers=headers) as response:
                        response.raise_for_status()
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_FEED_BYTES:
                                raise ValueError("Traffic publication exceeds the size limit")
                latest = json.loads(body)
                self.result = project(latest, graph()) if latest is not None else None
                self.next_fetch = monotonic() + 20
            return self.result
