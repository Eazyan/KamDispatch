from dataclasses import dataclass
import json
from pathlib import Path
import re

from shapely.errors import ShapelyError
from shapely.geometry import MultiPolygon, Point, Polygon, shape


UNASSIGNED = "unassigned"
DISTRICT_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"


@dataclass(frozen=True)
class District:
    id: str
    name: str
    boundary: Polygon | MultiPolygon


def load_districts(path: Path | None) -> tuple[District, ...]:
    if path is None:
        return ()
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
            if not isinstance(boundary, (Polygon, MultiPolygon)) or boundary.is_empty or not boundary.is_valid:
                raise ValueError(f"{district_id}: expected a valid Polygon or MultiPolygon")
            west, south, east, north = boundary.bounds
            if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
                raise ValueError(f"{district_id}: expected WGS84 longitude and latitude")
            districts[district_id] = District(district_id, name, boundary)
        return tuple(districts.values())
    except (OSError, ValueError, TypeError, AttributeError, KeyError, ShapelyError) as exc:
        raise ValueError(f"Invalid CITIZEN_DISTRICTS_FILE {path}: {exc}") from exc


def locate_district(districts: tuple[District, ...], latitude: float, longitude: float) -> District | None:
    point = Point(longitude, latitude)
    return next((district for district in districts if district.boundary.covers(point)), None)
