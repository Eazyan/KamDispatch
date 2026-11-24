"""Read-only display projection of the Windows worker's graph observations."""

from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
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
MAX_GAP_M = 250
MAX_FEED_BYTES = 12 * 1024 * 1024
DEFAULT_TTL = 3600
FEED_ERRORS = (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError)


class TrafficUnavailable(Exception):
    pass


@lru_cache(maxsize=1)
def graph():
    raw = GRAPH_FILE.read_bytes()
    corridors = export_graph(raw)
    edges = {edge["id"]: edge for edge in json.loads(raw)["edges"]}
    for road in corridors["segments"]:
        edge = edges[road["id"]]
        road["nodes"] = (edge["u"], edge["v"])
        road["street"] = (edge.get("name") or "").casefold().strip()
    return corridors


def interval_line(points, distances, start, end):
    def interpolate(distance):
        index = min(bisect_right(distances, distance) - 1, len(points) - 2)
        left, right = points[index:index + 2]
        span = distances[index + 1] - distances[index]
        fraction = (distance - distances[index]) / span if span else 0
        return [a + (b - a) * fraction for a, b in zip(left, right)]

    return [interpolate(start), *points[bisect_right(distances, start):bisect_left(distances, end)], interpolate(end)]


def road_chains(roads):
    """Follow graph nodes only where there is one unambiguous continuation."""
    adjacency = defaultdict(list)
    for index, road in enumerate(roads):
        for end, node in enumerate(road.get("nodes", ())):
            adjacency[node].append((index, end))
    visited = set()
    for index, road in enumerate(roads):
        if index in visited:
            continue
        visited.add(index)
        chain = deque([(index, False)])
        for side, node in enumerate(road.get("nodes", ())):
            current = index
            while True:
                branches = adjacency[node]
                street = roads[current].get("street")
                if len(branches) != 2:
                    if not street:
                        break
                    branches = [entry for entry in branches if roads[entry[0]].get("street") == street]
                if len(branches) != 2:
                    break
                continuation = [entry for entry in branches if entry[0] != current]
                if len(continuation) != 1:
                    break
                neighbor, near_end = continuation[0]
                if neighbor in visited:
                    break
                visited.add(neighbor)
                if side == 0:
                    chain.appendleft((neighbor, near_end == 0))
                else:
                    chain.append((neighbor, near_end == 1))
                current = neighbor
                node = roads[neighbor]["nodes"][1 - near_end]
        yield chain


def interpolated_intervals(roads, observations):
    for chain in road_chains(roads):
        previous = None
        pending = []
        for index, reverse in chain:
            length = roads[index]["length_m"]
            intervals = observations.get(index, ())
            if reverse:
                intervals = [(length - end, length - start, color, observed) for start, end, color, observed in reversed(intervals)]
            cursor = 0
            for start, end, color, observed in intervals:
                if previous is not None and start > cursor:
                    pending.append((index, reverse, cursor, start))
                size = sum(end_m - start_m for _, _, start_m, end_m in pending)
                limit = MAX_GAP_M if all(roads[edge].get("street") for edge, *_ in pending) else 100
                if previous is not None and 1e-6 < size <= limit:
                    previous_color, previous_observed = previous
                    split = size if previous_color == color else size / 2
                    for edge, backwards, left, right in pending:
                        edge_length = roads[edge]["length_m"]
                        cut = min(right, left + max(0, split))
                        split -= right - left
                        for a, b, status in ((left, cut, previous_color), (cut, right, color)):
                            if status != "free" and b - a > 1e-6:
                                at = min(observed, previous_observed)
                                yield (edge, edge_length - b, edge_length - a, status, at) if backwards else (edge, a, b, status, at)
                pending.clear()
                previous = color, observed
                cursor = end
            if previous is not None and cursor < length:
                pending.append((index, reverse, cursor, length))
                if sum(b - a for _, _, a, b in pending) > MAX_GAP_M:
                    previous = None
                    pending.clear()


def project(latest, roads, ttl=DEFAULT_TTL):
    if latest["graph_sha256"] != roads["graph_sha256"]:
        raise ValueError("Traffic feed uses a different graph")
    if latest["source"] != "yandex_maps_ui" or latest["status"] not in {"complete", "partial"}:
        raise ValueError("Unexpected traffic publication")

    segments = []
    published = aware(latest["published_at"]).astimezone(timezone.utc)
    roads = roads["segments"]
    intervals = {}
    for index, road in enumerate(roads):
        rows = []
        for row in latest["segments"].get(road["id"], []):
            if row["status"] not in CLASSES:
                continue
            a, b = map(float, row["interval_m"])
            if not (isfinite(a) and isfinite(b) and -1e-6 <= a < b <= road["length_m"] + 1e-6):
                raise ValueError("Invalid traffic interval")
            a, b = max(a, 0), min(b, road["length_m"])
            if b - a < 1e-6:
                continue
            observed = aware(row["timestamp"]).astimezone(timezone.utc)
            if observed > published:
                raise ValueError("Traffic observation is newer than its publication")
            rows.append((a, b, row["status"], observed))
        if rows:
            intervals[index] = sorted(rows)

    @lru_cache(maxsize=None)
    def geometry(index):
        points = roads[index]["coordinates"]
        scale = cos(radians(points[0][1]))
        distances = [0.0]
        for (lon1, lat1), (lon2, lat2) in zip(points, points[1:]):
            distances.append(distances[-1] + hypot((lon2 - lon1) * scale, lat2 - lat1))
        return points, distances

    def add_segment(index, start, end, color, observed, estimated=False):
        points, distances = geometry(index)
        if distances[-1] == 0:
            return
        unit = distances[-1] / roads[index]["length_m"]
        segment = {
            "corridor_id": roads[index]["id"], "status": color,
            "observed_at": observed.isoformat(),
            "valid_until": (observed + timedelta(seconds=ttl)).isoformat(),
            "coordinates": interval_line(points, distances, start * unit, min(end * unit, distances[-1])),
        }
        if estimated:
            segment["estimated"] = True
        segments.append(segment)

    for index, rows in intervals.items():
        for row in rows:
            add_segment(index, *row)
    for gap in interpolated_intervals(roads, intervals):
        add_segment(*gap, estimated=True)
    return {
        "published_at": published.isoformat(),
        "valid_until": min((row["valid_until"] for row in segments), default=None),
        "status": latest["status"],
        "segments": segments,
    }


def classify(publication, now):
    timestamp = now.astimezone(timezone.utc).isoformat()
    return {**publication, "segments": [
        {**row, "status": "stale"} if row["valid_until"] <= timestamp else row
        for row in publication["segments"]
    ]}


class TrafficFeed:
    def __init__(self, url, ttl=DEFAULT_TTL):
        self.url = url
        self.ttl = ttl
        self.lock = Lock()
        self.next_fetch = 0
        self.result = None
        self.last_success = None
        self.last_attempt = None
        self.reason = None

    def fetch(self):
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
        if latest is None:
            return None
        result = project(latest, graph(), self.ttl)
        if aware(result["published_at"]) > datetime.now(timezone.utc) + timedelta(seconds=30):
            raise ValueError("Traffic publication timestamp is in the future")
        return result

    def refresh(self):
        with self.lock:
            if monotonic() >= self.next_fetch:
                try:
                    self.result = self.fetch()
                except FEED_ERRORS as error:
                    if isinstance(error, httpx.HTTPStatusError):
                        self.reason = f"upstream_http_{error.response.status_code}"
                    elif isinstance(error, httpx.TimeoutException):
                        self.reason = "upstream_timeout"
                    elif isinstance(error, (httpx.HTTPError, OSError)):
                        self.reason = "upstream_connection_failed"
                    else:
                        self.reason = "invalid_publication"
                else:
                    self.reason = None
                    self.last_success = datetime.now(timezone.utc)
                self.last_attempt = datetime.now(timezone.utc)
                self.next_fetch = monotonic() + (5 if self.reason else 20)
            return self.result, self.last_success, self.last_attempt, self.reason

    def latest(self):
        result, success, attempt, reason = self.refresh()
        if result is None:
            if reason:
                raise TrafficUnavailable(reason)
            return None
        return {**classify(result, datetime.now(timezone.utc)),
                "source": {"last_success_at": success, "checked_at": attempt, "reason": reason}}
