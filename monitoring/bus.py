"""Time completed bus passages and evaluate corroborated delays."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import atan2, cos, degrees, hypot, radians
from statistics import median
from zoneinfo import ZoneInfo

LOCAL_TIME = ZoneInfo("Asia/Kamchatka")
MAX_POINT_GAP = timedelta(seconds=45)
MAX_SOURCE_AGE = timedelta(seconds=90)
MATCH_RADIUS_METRES = 25
CAMERA_FRESHNESS = timedelta(minutes=5)
DIRECTIONS = ("forward", "reverse")
COMPASS = ("На север", "На северо-восток", "На восток", "На юго-восток",
           "На юг", "На юго-запад", "На запад", "На северо-запад")


@dataclass(frozen=True)
class Position:
    vehicle: str
    lon: float
    lat: float
    measured_at: datetime
    received_at: datetime
    route: str | None = None


@dataclass(frozen=True)
class Passage:
    section: str
    direction: str
    vehicle: str
    entered_at: datetime
    ended_at: datetime
    received_at: datetime

    @property
    def seconds(self):
        return (self.ended_at - self.entered_at).total_seconds()


@dataclass(frozen=True)
class Confirmation:
    section: str
    source: str
    observed_at: datetime
    direction: str | None = None


class Section:
    def __init__(self, section_id, name, coordinates, entry_fraction, exit_fraction):
        self.id = section_id
        self.name = name
        self.origin = coordinates[0]
        self.lon_scale = 111_195 * cos(radians(self.origin[1]))
        self.vertices = [self.xy(*point) for point in coordinates]
        self.lengths = [hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(self.vertices, self.vertices[1:])]
        self.length = sum(self.lengths)
        self.entry = self.length * entry_fraction
        self.exit = self.length * exit_fraction
        east, north = self.vertices[-1]
        bearing = int((degrees(atan2(east, north)) + 22.5) // 45) % 8
        self.direction_labels = dict(zip(DIRECTIONS, (COMPASS[bearing], COMPASS[(bearing + 4) % 8])))

    def xy(self, lon, lat):
        return (lon - self.origin[0]) * self.lon_scale, (lat - self.origin[1]) * 111_195

    def project(self, lon, lat):
        x, y = self.xy(lon, lat)
        offset = 0.0
        candidates = []
        for (ax, ay), (bx, by), length in zip(self.vertices, self.vertices[1:], self.lengths):
            if length == 0:
                continue
            fraction = max(0, min(1, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / length**2))
            distance = hypot(x - ax - fraction * (bx - ax), y - ay - fraction * (by - ay))
            candidates.append((distance, offset + fraction * length))
            offset += length
        distance, along = min(candidates)
        if distance > MATCH_RADIUS_METRES:
            return None
        # A nearby parallel branch may have a substantially different along-road coordinate.
        if any(other <= distance + 5 and abs(along - progress) > 50 for other, progress in candidates):
            return None
        return along


def crossings(track, section, direction):
    entry, exit = (section.entry, section.exit) if direction == "forward" else (-section.exit, -section.entry)
    entered = None
    furthest = None
    previous = None
    available_at = None
    for point, along in track:
        progress = along if along is None or direction == "forward" else -along
        if progress is None:
            entered = furthest = previous = None
            continue
        if previous is not None:
            before, start = previous
            elapsed = point.measured_at - before.measured_at
            if elapsed > MAX_POINT_GAP or abs(progress - start) > elapsed.total_seconds() * 35:
                entered = furthest = None
            elif furthest is not None and progress < furthest - 15:
                entered = furthest = None
            else:
                if start < entry <= progress:
                    entered = before.measured_at + elapsed * ((entry - start) / (progress - start))
                    furthest = progress
                    available_at = max(before.received_at, point.received_at)
                if entered is not None:
                    furthest = max(furthest, progress)
                    available_at = max(available_at, point.received_at)
                    if start < exit <= progress:
                        ended = before.measured_at + elapsed * ((exit - start) / (progress - start))
                        yield Passage(section.id, direction, point.vehicle, entered, ended, available_at)
                        entered = furthest = None
        previous = point, progress


def measure_passages(sections, positions):
    # Deduplicate by measurement time. Conflicting coordinates break the trace.
    tracks = defaultdict(dict)
    for point in sorted(positions, key=lambda item: (item.received_at, item.measured_at)):
        if not usable_position(point):
            continue
        track = tracks[point.vehicle]
        existing = track.setdefault(point.measured_at, point)
        if existing and (existing.lon, existing.lat) != (point.lon, point.lat):
            track[point.measured_at] = None

    for track in tracks.values():
        matched = []
        for _, point in sorted(track.items()):
            if point is None:
                matched.append((None, {}))
                continue
            matches = {}
            for section in sections:
                progress = section.project(point.lon, point.lat)
                if progress is not None:
                    matches[section.id] = progress
            matched.append((point, matches if len(matches) == 1 else {}))
        for section in sections:
            section_track = [(point, matches.get(section.id)) for point, matches in matched]
            for direction in DIRECTIONS:
                yield from crossings(section_track, section, direction)


def usable_position(point):
    return (
        bool(point.vehicle) and -180 <= point.lon <= 180 and -90 <= point.lat <= 90
        and timedelta(0) <= point.received_at - point.measured_at <= MAX_SOURCE_AGE
    )


def cohort(passage):
    local = passage.entered_at.astimezone(LOCAL_TIME)
    return (passage.section, passage.direction, local.hour, local.weekday() >= 5), local.date()


def baselines(reference):
    cohorts = defaultdict(list)
    for passage in reference:
        key, day = cohort(passage)
        cohorts[key].append((day, passage.seconds))
    return {
        key: median(duration for _, duration in values)
        for key, values in cohorts.items()
        if len(values) >= 10 and len({day for day, _ in values}) >= 3
    }


def observations(sections, passages, baseline, confirmations, now, last_position_at, window_minutes=10):
    window = timedelta(minutes=window_minutes)
    cutoff = now - window
    recent = defaultdict(list)
    if last_position_at is not None and now - MAX_SOURCE_AGE <= last_position_at <= now:
        for passage in passages:
            if cutoff < passage.ended_at <= passage.received_at <= now:
                recent[passage.section, passage.direction].append(passage)
    local = now.astimezone(LOCAL_TIME)
    rows = []
    for section in sections:
        for direction in DIRECTIONS:
            sample = recent[section.id, direction]
            usual = baseline.get((section.id, direction, local.hour, local.weekday() >= 5))
            slow = []
            for passage in sample:
                expected = baseline.get(cohort(passage)[0])
                if expected is not None and passage.seconds >= max(expected * 1.5, expected + 60):
                    slow.append(passage)
            supporting = {}
            for passage in sorted(slow, key=lambda item: item.ended_at):
                supporting[passage.vehicle] = passage
            confirmations_by_expiry = [
                (min(item.observed_at + CAMERA_FRESHNESS, passage.ended_at + window), item.source)
                for item in confirmations for passage in slow
                if item.source in ("camera", "report") and item.section == section.id
                and item.direction in (None, direction) and now - CAMERA_FRESHNESS < item.observed_at <= now
                and abs(item.observed_at - passage.ended_at) < CAMERA_FRESHNESS
            ]
            vehicle_count = len({item.vehicle for item in sample})
            if len(supporting) >= 2:
                latest = sorted((item.ended_at for item in supporting.values()), reverse=True)
                confirmations_by_expiry.append((latest[1] + window, "buses"))
            # At an hour boundary each passage keeps its own comparison cohort.
            until, evidence = max(confirmations_by_expiry, default=(None, None))
            if evidence:
                usual = median(baseline[cohort(item)[0]] for item in slow)
            elif sample and all(cohort(item)[0] in baseline for item in sample):
                usual = median(baseline[cohort(item)[0]] for item in sample)
            status = "check" if evidence else "observed" if usual is not None and vehicle_count >= 2 and all(
                cohort(item)[0] in baseline for item in sample
            ) else "insufficient"
            relevant = slow if evidence else sample
            rows.append({
                "section_id": section.id, "name": section.name, "direction": direction,
                "direction_label": section.direction_labels[direction],
                "status": status, "reason": evidence, "usual_seconds": round(usual) if usual is not None else None,
                "observed_seconds": round(median(item.seconds for item in relevant)) if relevant else None,
                "vehicle_count": len({item.vehicle for item in relevant}),
                "last_seen": max((item.ended_at for item in relevant), default=None),
                "valid_until": min(until, last_position_at + MAX_SOURCE_AGE) if until else None,
            })
    return rows
