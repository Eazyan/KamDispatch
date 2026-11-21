"""Convert the hosted simulator's asynchronous runs into CAMCHATKA forecasts."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import gzip
from io import BytesIO
import json
import math
import re
from threading import Event
from time import monotonic

from .config import ROOT
from .snow import HOURS, Segment, SnowTile, forecast_counts, road_samples, score_segment

RUNS = "/api/v1/operations/city/runs"
CHUNK = 256
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_JSON = 192 * 1024 * 1024


def parse_time(value):
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise ValueError("The forecast timestamp must include a time zone")
    return at.astimezone(timezone.utc)


@lru_cache(maxsize=1)
def road_network():
    with gzip.open(ROOT / "monitoring" / "snow_roads.json.gz", "rt", encoding="utf-8") as file:
        data = json.load(file)
    segments = [Segment.model_validate(item) for item in data["segments"]]
    return [(segment, list(road_samples(segment))) for segment in segments], data.get("source_timestamp")


def forecast_request(weather, now):
    terms = {parse_time(term): term for term in weather["terms"]}
    hour = now.replace(minute=0, second=0, microsecond=0)
    start = next((term for term in sorted(terms) if hour <= term <= hour + timedelta(hours=1)
                  and all(term + timedelta(hours=i) in terms for i in range(1, HOURS + 1))), None)
    if start is None or not weather.get("sha256"):
        raise ValueError("Hosted simulator has no current three-hour forecast window")
    return {"source": "live", "valid_at": terms[start], "weather_revision": weather["sha256"], "hours": HOURS}, start


def fetch_replay(client, url):
    blob = BytesIO()
    with client.stream("GET", url, timeout=45) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            if blob.tell()+len(chunk) > MAX_ARCHIVE:
                raise ValueError("Hosted replay exceeds the download limit")
            blob.write(chunk)
    blob.seek(0)
    with gzip.GzipFile(fileobj=blob) as archive:
        raw = archive.read(MAX_JSON+1)
    if len(raw) > MAX_JSON:
        raise ValueError("Hosted replay exceeds the decompressed size limit")
    return json.loads(raw)


class RemoteGrid:
    def __init__(self, replay):
        self.west, self.south, self.east, self.north = replay["bounds"]
        self.rows, self.cols = replay["rows"], replay["cols"]
        self.snow = replay["grids"]["snow_cm"]
        self.weather = replay["grids"]["weather_snow"]
        if (not (-180 <= self.west < self.east <= 180 and -90 <= self.south < self.north <= 90)
                or not (1 <= self.rows <= 4096 and 1 <= self.cols <= 4096)
                or any(len(grid) != self.rows or any(len(row) != self.cols for row in grid)
                       for grid in (self.snow, self.weather))):
            raise ValueError("Unexpected dimensions in hosted snow fields")
        self.dx = (self.east - self.west)/self.cols
        self.dy = (self.north - self.south)/self.rows

    def cell(self, lon, lat):
        if self.west <= lon <= self.east and self.south <= lat <= self.north:
            return min(self.rows-1, int((self.north-lat)/self.dy)), min(self.cols-1, int((lon-self.west)/self.dx))
        return None

    def excess(self, row, col):
        snow, base = self.snow[row][col], self.weather[row][col]
        if snow is not None and base is not None and math.isfinite(snow) and math.isfinite(base):
            return snow-base
        return None

    def excess_at(self, lon, lat):
        cell = self.cell(lon, lat)
        return self.excess(*cell) if cell is not None else None

    def png(self, row, col):
        bottom, right = min(row+CHUNK, self.rows), min(col+CHUNK, self.cols)
        depths = [[self.excess(y, x) for x in range(col, right)] for y in range(row, bottom)]
        bounds = (self.west+col*self.dx, self.north-bottom*self.dy,
                  self.west+right*self.dx, self.north-row*self.dy)
        tile = SnowTile.model_construct(snow_depth_cm=depths, bbox_wgs84=bounds, forecast_snowfall_cm=0)
        return tile.heatmap()


def convert_replay(replay, start, road_data=None):
    samples, roads_at = road_data if road_data is not None else road_network()
    grid = RemoteGrid(replay)
    rows, tile_index, rasters = [], {}, {}
    for segment, points in samples:
        row = score_segment(segment, points, grid.excess_at)
        rows.append(row)
        if row["status"] == "unavailable":
            continue
        row["calculation_source"] = "remote"
        tile_index[segment.id] = []
        chunks = {tuple(n//CHUNK*CHUNK for n in cell) for lon, lat, _ in points
                  if (cell := grid.cell(lon, lat)) is not None}
        for y, x in sorted(chunks):
            key = f"remote/{y}/{x}"
            tile_index[segment.id].append(key)
            if key not in rasters:
                rasters[key] = grid.png(y, x)
    counts = forecast_counts(rows)
    if counts["unavailable"] == len(rows):
        raise ValueError("Hosted calculation covers none of the configured roads")
    return {
        "scope": "city", "status": "partial" if counts["unavailable"] else "ready",
        "start": start.isoformat(), "end": (start+timedelta(hours=HOURS)).isoformat(),
        "counts": counts, "roads_at": roads_at, "weather_sources": ["3D-модель · Open-Meteo"],
        "remote_run_id": replay["run_id"], "field_validated": replay.get("field_validated", False),
        "segments": rows, "tiles_by_segment": tile_index, "raster_tiles": rasters,
    }


def remote_city(client, url, stop=None):
    stop = stop or Event()
    weather = client.get(f"{url}/api/v1/operations/atlas/live", timeout=20).raise_for_status().json()
    request, start = forecast_request(weather, datetime.now(timezone.utc))
    job = client.post(url+RUNS, json=request, timeout=30).raise_for_status().json()
    run_id = job["run_id"]
    if not re.fullmatch(r"[0-9a-f]{64}", run_id):
        raise ValueError("Invalid hosted simulation run ID")
    run_url = f"{url}{RUNS}/{run_id}"
    deadline = monotonic()+8*60
    while job["status"] in {"pending", "running"}:
        if stop.wait(5) or monotonic() >= deadline:
            raise TimeoutError("Hosted city simulation is still running")
        job = client.get(run_url, timeout=20).raise_for_status().json()
    if job["status"] != "completed":
        raise ValueError("Hosted city simulation failed")
    replay = fetch_replay(client, run_url+"/replay")
    result = replay["input"]
    if (replay["run_id"] != run_id or replay["status"] != "completed" or result["source"] != "live"
            or parse_time(result["valid_at"]) != start or result["hours"] != HOURS
            or result.get("weather_revision") != request["weather_revision"]):
        raise ValueError("Hosted result does not match the requested forecast window")
    return convert_replay(replay, start)


def merge_reserve(primary, reserve):
    if parse_time(primary["start"]) != parse_time(reserve["start"]) or parse_time(primary["end"]) != parse_time(reserve["end"]):
        raise ValueError("Hosted and local forecast windows differ")
    reserve_rows = {row["id"]: row for row in reserve["segments"] if row["status"] != "unavailable"}
    for index, row in enumerate(primary["segments"]):
        key = row["id"]
        if row["status"] != "unavailable" or key not in reserve_rows:
            continue
        primary["segments"][index] = {**reserve_rows[key], "calculation_source": "local"}
        ids = reserve["tiles_by_segment"][key]
        primary["tiles_by_segment"][key] = ids
        primary["raster_tiles"].update({tile: reserve["raster_tiles"][tile] for tile in ids if tile in reserve["raster_tiles"]})
    primary["counts"] = forecast_counts(primary["segments"])
    if any(row.get("calculation_source") == "local" for row in primary["segments"]):
        primary["weather_sources"].append("Резервная модель · Open-Meteo")
        primary["weather_by_zone"] = reserve.get("weather_by_zone", [])
    primary["status"] = "partial" if primary["counts"]["unavailable"] else "ready"
    return primary
