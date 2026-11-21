"""Collect snow simulations and score configured road centerlines."""
from base64 import b64encode
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from io import BytesIO
import json
import logging
import math

import httpx
from PIL import Image
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, FiniteFloat

from .config import ROOT
from .db import connect

LOGGER = logging.getLogger(__name__)
ZOOM = 17
HOURS = 3
RESOLUTION = 64
REFRESH_SECONDS = 1800
STALE_AFTER = timedelta(seconds=2 * REFRESH_SECONDS)
SAMPLE_STEP_M = 4
MIN_EXCESS_CM = 1.0


class Segment(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    osm_way: int | None = Field(default=None, gt=0)
    camera_id: str | None = None
    coordinates: list[tuple[FiniteFloat, FiniteFloat]] = Field(min_length=2, max_length=200)


def tile_at(lon, lat):
    count = 2**ZOOM
    return (int((lon + 180) / 360 * count),
            int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * count))


def read_segments(path):
    segments = [Segment.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    if not 1 <= len(segments) <= 20 or len({item.id for item in segments}) != len(segments):
        raise ValueError("Configure 1–20 unique snow pilot segments")
    for segment in segments:
        if any(not (-180 <= lon < 180 and -85 <= lat <= 85) for lon, lat in segment.coordinates):
            raise ValueError(f"Invalid snow segment coordinates: {segment.id}")
        distance = sum(distance_m(a, b) for a, b in zip(segment.coordinates, segment.coordinates[1:]))
        if not 0 < distance <= 2000:
            raise ValueError(f"Snow segment length must be within 2 km: {segment.id}")
    return segments


def distance_m(a, b):
    lon_a, lat_a = a
    lon_b, lat_b = b
    scale = math.cos(math.radians((lat_a + lat_b) / 2))
    return 111_195 * math.hypot((lon_b - lon_a) * scale, lat_b - lat_a)


def road_samples(segment):
    # Sample edge midpoints at nearly uniform spacing without counting junctions twice.
    for a, b in zip(segment.coordinates, segment.coordinates[1:]):
        length = distance_m(a, b)
        if not length:
            continue
        count = math.ceil(length / SAMPLE_STEP_M)
        for i in range(count):
            ratio = (i + 0.5) / count
            yield (a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio, length / count)


class WeatherHour(BaseModel):
    end_utc: datetime
    wind_from_deg: FiniteFloat = Field(ge=0, lt=360)
    wind_speed_ms: FiniteFloat = Field(ge=0)
    snowfall_cm: FiniteFloat = Field(ge=0)


class SnowTile(BaseModel):
    tile: dict[str, int]
    bbox_wgs84: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    snow_depth_cm: list[Sequence[FiniteFloat | None]]
    forecast_snowfall_cm: FiniteFloat = Field(ge=0)
    weather: list[WeatherHour]
    weather_source: str
    model: str

    def excess(self, lon, lat):
        west, south, east, north = self.bbox_wgs84
        if not (west <= lon <= east and south <= lat <= north):
            return None
        rows, cols = len(self.snow_depth_cm), len(self.snow_depth_cm[0])
        row = min(rows - 1, int((north - lat) / (north - south) * rows))
        col = min(cols - 1, int((lon - west) / (east - west) * cols))
        depth = self.snow_depth_cm[row][col]
        return None if depth is None or math.isnan(depth) else depth - self.forecast_snowfall_cm

    def heatmap(self):
        # Use an indexed PNG to keep archived snapshots compact.
        colors = [(0, 0, 0, 0), (237, 171, 74, 100), (226, 117, 29, 142), (166, 62, 14, 180)]
        rows = self.snow_depth_cm
        pixels = []
        for row in rows:
            for depth in row:
                excess = 0 if depth is None or math.isnan(depth) else depth - self.forecast_snowfall_cm
                pixels.append(sum(excess >= threshold for threshold in (MIN_EXCESS_CM, 3, 6)))
        image = Image.new("P", (len(rows[0]), len(rows)))
        image.putpalette([channel for color in colors for channel in color[:3]])
        image.putdata(pixels)
        buffer = BytesIO()
        image.save(buffer, format="PNG", transparency=bytes(color[3] for color in colors))
        return {"bbox_wgs84": self.bbox_wgs84, "png": b64encode(buffer.getvalue()).decode("ascii")}


def fetch_tile(client, url, tile, start):
    x, y = tile
    try:
        response = client.post(f"{url}/api/snow", json={
            "tile": {"z": ZOOM, "x": x, "y": y},
            "hours": HOURS, "resolution": RESOLUTION, "city_cache_only": True,
            "use_foam": True, "run_foam": False,
        }, timeout=12)
        if response.status_code == 204:
            return None
        response.raise_for_status()
        result = SnowTile.model_validate(response.json())
        west, south, east, north = result.bbox_wgs84
        expected_times = [start + timedelta(hours=i) for i in range(1, HOURS + 1)]
        if (result.tile != {"z": ZOOM, "x": x, "y": y} or not (west < east and south < north)
                or [hour.end_utc for hour in result.weather] != expected_times
                or not 1 <= len(result.snow_depth_cm) <= RESOLUTION
                or not 1 <= len(result.snow_depth_cm[0]) <= RESOLUTION
                or any(len(row) != len(result.snow_depth_cm[0]) for row in result.snow_depth_cm)):
            raise ValueError("Unexpected snow forecast geometry or time window")
        return result
    except (httpx.HTTPError, ValueError, TypeError) as error:
        LOGGER.warning("Snow tile %s/%s unavailable (%s)", x, y, type(error).__name__)
        return None


def summarize_segment(segment, samples, tiles):
    if any(tiles[tile_at(lon, lat)] is None for lon, lat, _ in samples):
        return score_segment(segment, samples, lambda _lon, _lat: None)
    return score_segment(segment, samples, lambda lon, lat: tiles[tile_at(lon, lat)].excess(lon, lat))


def score_segment(segment, samples, excess_at):
    result = {**segment.model_dump(mode="json"), "status": "unavailable", "excess_cm": None}
    covered = []
    for lon, lat, distance in samples:
        excess = excess_at(lon, lat)
        if excess is not None:
            covered.append((excess, distance))
    length = sum(distance for _, _, distance in samples)
    covered_length = sum(distance for _, distance in covered)
    if not covered or covered_length < 0.75 * length:
        return result
    # Length-weighted upper quartile reflects sustained accumulation along the road.
    cumulative = 0
    for excess, distance in sorted(covered):
        cumulative += distance
        if cumulative >= 0.75 * covered_length:
            break
    return {**result, "status": "watch" if excess >= MIN_EXCESS_CM else "no_signal",
            "excess_cm": round(excess, 2)}


def calculate(client, url, segments, started):
    start = started.replace(minute=0, second=0, microsecond=0)
    samples = [(segment, list(road_samples(segment))) for segment in segments]
    keys = sorted({tile_at(lon, lat) for _, points in samples for lon, lat, _ in points})
    with ThreadPoolExecutor(max_workers=4) as pool:
        tiles = dict(zip(keys, pool.map(lambda tile: fetch_tile(client, url, tile, start), keys)))
    return forecast_payload(samples, tiles, start)


def forecast_counts(rows):
    return {status: sum(row["status"] == status for row in rows)
            for status in ("watch", "no_signal", "unavailable")}


def forecast_payload(samples, tiles, start, *, city=False):
    rows = [summarize_segment(segment, points, tiles) for segment, points in samples]
    known = sum(row["status"] != "unavailable" for row in rows)
    raster_tiles = {tile_key(x, y): tile.heatmap() for (x, y), tile in tiles.items() if tile}
    return {
        "status": "ready" if known == len(rows) else "partial" if known else "unavailable",
        "start": start.isoformat(), "end": (start + timedelta(hours=HOURS)).isoformat(),
        "weather_sources": sorted({tile.weather_source for tile in tiles.values() if tile}),
        "models": sorted({tile.model for tile in tiles.values() if tile}),
        **({} if city else {"weather_by_tile": [
            {"tile": tile.tile, "weather_source": tile.weather_source,
             "model": tile.model, "forecast_snowfall_cm": tile.forecast_snowfall_cm,
             "hours": [hour.model_dump(mode="json") for hour in tile.weather]}
            for tile in tiles.values() if tile
        ]}),
        "raster_tiles": raster_tiles,
        "tiles_by_segment": {
            segment.id: sorted({tile_key(*tile_at(lon, lat)) for lon, lat, _ in points})
            for segment, points in samples
        },
        "segments": rows,
    }


def tile_key(x, y):
    return f"{x}/{y}"


def snow_tick(settings, run_id, client, segments):
    started = datetime.now(timezone.utc)
    payload = calculate(client, settings.snow_sim_url, segments, started)
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO snow_forecasts (run_id,calculated_at,status,payload) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (run_id, started, payload["status"], Jsonb(payload)),
        )


def snow_forecast(conn, settings, run_id, now):
    if run_id == "demo":
        demo = demo_forecast()
        return {key: value for key, value in demo.items() if key not in {"raster_tiles", "tiles_by_segment"}}
    if run_id not in {"live", "preview"} or not (settings.snow_sim_url or settings.city_snow):
        return None
    if not settings.city_snow and conn.execute("SELECT 1 FROM checks WHERE run_id=%s AND name='snow_sim' AND status='invalid_segments'",
                    (run_id,)).fetchone():
        return {"status": "configuration_error", "segments": []}
    scope = "city" if settings.city_snow else "pilot"
    row = conn.execute(
        """SELECT calculated_at, CASE WHEN scope='city' THEN summary
               ELSE payload - ARRAY['weather_by_tile','raster_tiles','tiles_by_segment'] END AS payload
           FROM snow_forecasts WHERE run_id=%s AND scope=%s ORDER BY calculated_at DESC LIMIT 1""",
        (run_id, scope),
    ).fetchone()
    if not row:
        city_check = conn.execute(
            "SELECT status FROM checks WHERE run_id=%s AND name='snow_city'", (run_id,),
        ).fetchone() if scope == "city" else None
        status = city_check["status"] if city_check and city_check["status"] in {"running", "unavailable"} else "pending"
        return {"status": status, "scope": scope, "segments": []}
    latest = {"calculated_at": row["calculated_at"], "segments": [], **row["payload"]}
    if now >= datetime.fromisoformat(latest["end"]) or now - row["calculated_at"] >= STALE_AFTER:
        latest["status"] = "stale"
    return latest


@lru_cache(maxsize=1)
def demo_forecast():
    return json.loads((ROOT / "monitoring" / "snow_demo.json").read_text(encoding="utf-8"))


def snow_raster(conn, settings, run_id, calculated_at, segment_id, now):
    if calculated_at.tzinfo is None:
        return []
    if run_id == "demo":
        payload = demo_forecast()
        if calculated_at != datetime.fromisoformat(payload["calculated_at"]):
            return []
    else:
        if run_id not in {"preview", "live"} or not (settings.snow_sim_url or settings.city_snow) or now - calculated_at >= STALE_AFTER:
            return []
        row = conn.execute(
            "SELECT payload FROM snow_forecasts WHERE run_id=%s AND calculated_at=%s", (run_id, calculated_at),
        ).fetchone()
        if not row or now >= datetime.fromisoformat(row["payload"]["end"]):
            return []
        payload = row["payload"]
    if not any(segment["id"] == segment_id and segment["status"] != "unavailable" for segment in payload["segments"]):
        return []
    tiles = payload.get("raster_tiles", {})
    return [tiles[key] for key in payload.get("tiles_by_segment", {}).get(segment_id, []) if key in tiles]


def city_roads(conn, settings, run_id, calculated_at):
    if run_id not in {"preview", "live"} or not settings.city_snow or calculated_at.tzinfo is None:
        return None
    row = conn.execute(
        """SELECT payload->'segments' AS segments FROM snow_forecasts
           WHERE run_id=%s AND calculated_at=%s AND scope='city'""", (run_id, calculated_at),
    ).fetchone()
    return row["segments"] if row else None
