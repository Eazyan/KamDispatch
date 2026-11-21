"""Run the city-wide snow model with a shared weather input per forecast zone."""
from array import array
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import gzip
import json
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import AwareDatetime, BaseModel, Field
from snowmap.model import estimate
from snowmap.sources import osm_buildings, tile_bounds

from .config import ROOT
from .snow import HOURS, RESOLUTION, ZOOM, Segment, SnowTile, WeatherHour, forecast_counts, forecast_payload, road_samples, tile_at

FORECAST_URL = os.getenv("SNOW_WEATHER_URL", "https://api.open-meteo.com/v1/forecast")
FORECAST_KEY = os.getenv("SNOW_WEATHER_KEY", "")
ROADS_FILE = Path(os.getenv("SNOW_ROADS_FILE", str(ROOT / "monitoring" / "snow_roads.json.gz")))
ZONE_LAT = 0.075
ZONE_LON = 0.125

app = FastAPI(title="Петропавловск · расчёт снега", docs_url=None, redoc_url=None, openapi_url=None)


class CityInput(BaseModel):
    start: AwareDatetime
    segment_ids: set[str] = Field(min_length=1, max_length=15_000)


@lru_cache(maxsize=1)
def network():
    with gzip.open(ROADS_FILE, "rt", encoding="utf-8") as file:
        data = json.load(file)
    segments = [Segment.model_validate(item) for item in data["segments"]]
    samples = [(segment, list(road_samples(segment))) for segment in segments]
    keys = {tile_at(lon, lat) for _, points in samples for lon, lat, _ in points}
    tile_zones, centers = {}, defaultdict(list)
    for key in sorted(keys):
        west, south, east, north = tile_bounds(ZOOM, *key)
        lon, lat = (west + east) / 2, (south + north) / 2
        zone = (int(lon // ZONE_LON), int(lat // ZONE_LAT))
        centers[zone].append((lon, lat))
        tile_zones[key] = zone
    points = {zone: tuple(sum(axis) / len(coords) for axis in zip(*coords)) for zone, coords in centers.items()}
    return samples, tile_zones, points, data.get("source_timestamp")


def read_weather(item, start):
    columns = item["hourly"]
    end_stamps = [int((start + timedelta(hours=i)).timestamp()) for i in range(1, HOURS + 1)]
    indices = {stamp: index for index, stamp in enumerate(columns["time"])}
    return [WeatherHour(
        end_utc=datetime.fromtimestamp(stamp, timezone.utc),
        wind_from_deg=columns["wind_direction_10m"][indices[stamp]] % 360,
        wind_speed_ms=columns["wind_speed_10m"][indices[stamp]],
        snowfall_cm=columns["snowfall"][indices[stamp]],
    ) for stamp in end_stamps]


def zone_weather(points, start):
    # The location IDs keep responses associated with their requested zone.
    zones = list(points)
    params = {
        "longitude": ",".join(f"{points[zone][0]:.6f}" for zone in zones),
        "latitude": ",".join(f"{points[zone][1]:.6f}" for zone in zones),
        "hourly": "wind_speed_10m,wind_direction_10m,snowfall",
        "wind_speed_unit": "ms", "timeformat": "unixtime", "timezone": "GMT",
        "forecast_hours": HOURS + 2, "cell_selection": "land",
    }
    if FORECAST_KEY:
        params["apikey"] = FORECAST_KEY
    response = httpx.get(FORECAST_URL, params=params, timeout=20)
    response.raise_for_status()
    items = response.json()
    if len(zones) == 1 and isinstance(items, dict):
        items = [items]
    result = {}
    for index, item in enumerate(items):
        location = item.get("location_id", index) if isinstance(item, dict) else None
        if type(location) is not int or not 0 <= location < len(zones):
            continue
        try:
            result[zones[location]] = read_weather(item, start)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    if not result:
        raise ValueError("No valid weather zones for the requested forecast window")
    return result


def city_tile(key, weather):
    bounds = tile_bounds(ZOOM, *key)
    buildings = osm_buildings(bounds, cache_only=True)
    if buildings is None:
        return None
    rows = [hour.model_dump() for hour in weather]
    result = estimate(bounds, buildings, rows, RESOLUTION, True)
    # Dense storage keeps the batch's tile rasters small; NaN marks building masks.
    masked = float("nan")
    depths = [array("d", (masked if value is None else value for value in row)) for row in result["snow_depth_cm"]]
    return SnowTile.model_construct(
        tile={"z": ZOOM, "x": key[0], "y": key[1]}, bbox_wgs84=bounds, weather=weather,
        weather_source="Open-Meteo", model=result["model"], snow_depth_cm=depths,
        forecast_snowfall_cm=sum(hour.snowfall_cm for hour in weather),
    )


@app.get("/healthz")
def health():
    network()
    return {"status": "ready"}


@app.post("/api/snow/city")
def calculate_city(request: CityInput | None = None):
    samples, tile_zones, points, roads_at = network()
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    if request:
        requested_at = request.start.astimezone(timezone.utc)
        if abs(requested_at-start) > timedelta(hours=1) or requested_at.minute or requested_at.second or requested_at.microsecond:
            raise HTTPException(422, "Expected a current hourly forecast window")
        start = requested_at
        samples = [(segment, road) for segment, road in samples if segment.id in request.segment_ids]
        if not samples:
            raise HTTPException(422, "No requested road IDs exist")
        keys = sorted({tile_at(lon, lat) for _, road in samples for lon, lat, _ in road})
        tile_zones = {key: tile_zones[key] for key in keys}
        points = {zone: points[zone] for zone in sorted(set(tile_zones.values()))}
    try:
        weather = zone_weather(points, start)
    except (httpx.HTTPError, TypeError, ValueError) as error:
        raise HTTPException(503, "Open-Meteo forecast unavailable") from error
    tiles = {key: city_tile(key, weather[zone]) if zone in weather else None for key, zone in tile_zones.items()}
    payload = forecast_payload(samples, tiles, start, city=True)
    counts = forecast_counts(payload["segments"])
    return {
        **payload, "scope": "city", "counts": counts, "roads_at": roads_at,
        "weather_by_zone": [
            {"coordinates": points[zone], "hours": [hour.model_dump(mode="json") for hour in hours]}
            for zone, hours in weather.items()
        ],
    }
