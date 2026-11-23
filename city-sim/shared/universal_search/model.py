"""Source-neutral search contracts. Coordinates always use WGS84 (lon, lat)."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math

TYPES = frozenset({'street', 'address', 'road_section', 'bus', 'equipment',
                   'camera', 'task', 'event', 'base', 'snow_site'})


def timestamp(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamps require timezone')
    return parsed.astimezone(timezone.utc)


def coordinates(value):
    if value is None:
        return None
    if len(value) != 2 or any(isinstance(x, bool) or not isinstance(x, (int, float))
                              or not math.isfinite(x) for x in value):
        raise ValueError('coordinates must be finite [longitude, latitude]')
    lon, lat = value
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise ValueError('coordinates outside WGS84')
    return (lon, lat)


@dataclass(frozen=True)
class Entity:
    id: str
    type: str
    name: str
    source: str
    coordinates: tuple | None = None
    aliases: tuple = ()
    updated_at: str | None = None
    origin: str = 'unknown'  # real / demo / synthetic / unknown

    def __post_init__(self):
        if self.type not in TYPES or any(not isinstance(x, str) or not x.strip()
                                       for x in (self.id, self.name, self.source)):
            raise ValueError('entity requires id, supported type, name and source')
        if self.origin not in {'real', 'demo', 'synthetic', 'unknown'}:
            raise ValueError('invalid data origin')
        if isinstance(self.aliases, str) or any(not isinstance(x, str) for x in self.aliases):
            raise ValueError('aliases must be a sequence of strings')
        object.__setattr__(self, 'coordinates', coordinates(self.coordinates))
        object.__setattr__(self, 'aliases', tuple(self.aliases))
        timestamp(self.updated_at)

    def dict(self):
        return asdict(self)


@dataclass(frozen=True)
class DynamicState:
    id: str
    type: str
    coordinates: tuple | None = None
    status: str | None = None
    observed_at: str | None = None

    def __post_init__(self):
        if self.type not in TYPES or not isinstance(self.id, str) or not self.id:
            raise ValueError('invalid state identity')
        object.__setattr__(self, 'coordinates', coordinates(self.coordinates))
        timestamp(self.observed_at)
