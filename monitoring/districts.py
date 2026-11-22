from dataclasses import dataclass
import json
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
import re

from shapely import prepare
from shapely.errors import ShapelyError
from shapely.geometry import MultiPolygon, Point, Polygon, shape


DEFAULT_DISTRICTS = Path(__file__).with_name("kamchatka_districts.geojson")
CITY_DISTRICTS = Path(__file__).with_name("petropavlovsk_districts.geojson")
PETROPAVLOVSK = "petropavlovsk-kamchatsky"
MAX_CENTER_DISTANCE_M = 900
UNASSIGNED = "unassigned"
DISTRICT_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"


@dataclass(frozen=True)
class District:
    id: str
    name: str
    boundary: Polygon | MultiPolygon | Point
    parent_id: str | None = None


def load_districts(path: Path | None) -> tuple[District, ...]:
    if path is not None:
        return _load_districts(path)
    return _load_districts(DEFAULT_DISTRICTS) + _load_districts(CITY_DISTRICTS, parent_id=PETROPAVLOVSK)


def _load_districts(path: Path, *, parent_id: str | None = None) -> tuple[District, ...]:
    allowed = (Polygon, MultiPolygon, Point) if parent_id else (Polygon, MultiPolygon)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["type"] != "FeatureCollection" or not isinstance(data["features"], list) or not data["features"]:
            raise ValueError("expected a populated GeoJSON FeatureCollection")
        districts = {}
        for feature in data["features"]:
            district_id = feature["properties"]["id"]
            name = feature["properties"]["name"].strip()
            boundary = shape(feature["geometry"])
            if (feature["type"] != "Feature" or not re.fullmatch(DISTRICT_ID_PATTERN, district_id)
                    or district_id == UNASSIGNED or district_id in districts
                    or not name or len(name) > 200 or "\x00" in name):
                raise ValueError("each district needs a unique id and a name")
            if not isinstance(boundary, allowed) or boundary.is_empty or not boundary.is_valid:
                raise ValueError(f"{district_id}: expected a valid district geometry")
            west, south, east, north = boundary.bounds
            if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
                raise ValueError(f"{district_id}: expected WGS84 longitude and latitude")
            if not isinstance(boundary, Point):
                prepare(boundary)
            districts[district_id] = District(district_id, name, boundary, parent_id)
        return tuple(districts.values())
    except (OSError, ValueError, TypeError, AttributeError, KeyError, ShapelyError) as exc:
        raise ValueError(f"Invalid CITIZEN_DISTRICTS_FILE {path}: {exc}") from exc


def distance_m(first: Point, second: Point) -> float:
    lat1, lat2 = radians(first.y), radians(second.y)
    delta_lon = radians(second.x - first.x)
    half_chord = sin((lat2 - lat1) / 2)**2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2)**2
    return 2 * 6_371_000 * asin(min(1, sqrt(half_chord)))


def locate_district(districts: tuple[District, ...], latitude: float, longitude: float) -> District | None:
    point = Point(longitude, latitude)
    municipality = next((district for district in districts
                         if district.parent_id is None and district.boundary.covers(point)), None)
    if municipality is None:
        return None
    children = [district for district in districts if district.parent_id == municipality.id]
    covering = [district for district in children if not isinstance(district.boundary, Point)
                and district.boundary.covers(point)]
    if covering:
        return min(covering, key=lambda district: (district.boundary.area, district.id))
    centers = [(distance_m(point, district.boundary), district.id, district)
               for district in children if isinstance(district.boundary, Point)]
    if centers:
        distance, _, closest = min(centers)
        if distance <= MAX_CENTER_DISTANCE_M:
            return closest
    return municipality
