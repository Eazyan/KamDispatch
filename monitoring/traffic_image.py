"""Conservative corridor observations from paired, UI-georeferenced screenshots.

Colors are provider display classes, never measured speeds or travel directions.
"""
from collections import Counter
from datetime import datetime
import math

import numpy as np

RADIUS_PX = 4
MIN_COVERAGE = 0.6
MIN_SUPPORTED_PX = 40
LABELS = {1: "free", 2: "slow", 3: "congested"}


def mercator(point):
    lon, lat = map(float, point)
    if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -85 < lat < 85:
        raise ValueError("Coordinates must be finite WGS84 lon/lat within WebMercator")
    return np.array([math.radians(lon), math.asinh(math.tan(math.radians(lat)))])


def calibration(capture):
    """Fit three control points and independently verify a fourth point."""
    points = capture["control_points"]
    if len(points) != 4:
        raise ValueError("Exactly four independent UI control points are required")
    geo = np.array([mercator(p["lon_lat"]) for p in points])
    pixel = np.array([p["pixel"] for p in points], dtype=float)
    if pixel.shape != (4, 2) or not np.isfinite(pixel).all():
        raise ValueError("Invalid control point pixels")
    origin = geo[0]
    design = np.column_stack((geo - origin, np.ones(4)))
    if np.linalg.matrix_rank(design[:3]) != 3:
        raise ValueError("Control points are collinear")
    transform = np.linalg.solve(design[:3], pixel[:3])
    residual = float(np.linalg.norm(design[3] @ transform - pixel[3]))
    if not math.isfinite(residual) or residual > 1:
        raise ValueError("Independent calibration error exceeds one pixel")
    # Rotated or tilted views cannot support the axis-aligned corridor review.
    if (transform[0, 0] <= 0 or transform[1, 1] >= 0
            or abs(transform[0, 1]) > .01 * transform[0, 0]
            or abs(transform[1, 0]) > .01 * abs(transform[1, 1])):
        raise ValueError("Use a north-up, untilted map")
    return origin, transform, residual


def project(point, origin, transform):
    return np.append(mercator(point) - origin, 1) @ transform


def color_mask(before, after):
    """Ignore unchanged basemap colors, including parks and ordinary roads."""
    old = np.asarray(before.convert("RGB"), dtype=np.int16)
    new = np.asarray(after.convert("RGB"), dtype=np.int16)
    changed = np.max(np.abs(new - old), axis=2) >= 35
    r, g, b = new.transpose(2, 0, 1)
    labels = np.zeros(r.shape, dtype=np.uint8)
    labels[changed & (g > 100) & (g > r * 1.3) & (g > b * 1.25)] = 1
    labels[changed & (r > 190) & (g > 135) & (b < 135) & (r > g * .95)] = 2
    labels[changed & (r > 170) & (g < 135) & (b < 135)] = 3
    # One-pixel text strokes are not road ribbons. Require local thickness.
    filtered = np.zeros_like(labels)
    for label in LABELS:
        mask = (labels == label).astype(np.uint8)
        padded = np.pad(mask, 1)
        neighbors = sum(padded[dy:dy+mask.shape[0], dx:dx+mask.shape[1]]
                        for dy in range(3) for dx in range(3))
        filtered[(labels == label) & (neighbors >= 6)] = label
    return filtered


def screen_samples(points):
    for a, b in zip(points, points[1:]):
        length = float(np.linalg.norm(b - a))
        if length > 100000:
            raise ValueError("Road geometry exceeds the review extent")
        count = max(1, math.ceil(length / 4))
        for i in range(count):
            yield a + (b - a) * ((i + .5) / count), length / count


def analyze_roads(before, after, capture, roads):
    if before.size != after.size or max(before.size) > 4096:
        raise ValueError("Screenshot pair must have equal dimensions, at most 4096 pixels")
    timestamp = datetime.fromisoformat(capture["captured_at"])
    if timestamp.tzinfo is None:
        raise ValueError("Capture time requires a UTC offset")
    origin, transform, residual = calibration(capture)
    x, y, width, height = map(float, capture["analysis_region"])
    if not all(map(math.isfinite, (x, y, width, height))) or not (
        0 <= x < x + width <= before.width and 0 <= y < y + height <= before.height
    ):
        raise ValueError("Analysis region must fit inside the screenshots")
    labels = color_mask(before, after)
    features = []
    for road in roads:
        coords = road["coordinates"]
        if len(coords) < 2:
            raise ValueError("Roads require at least two coordinates")
        pixels = [project(point, origin, transform) for point in coords]
        # Sampling also clips crossing roads whose endpoints are outside the image.
        votes, total, visible = Counter(), 0., 0.
        for (px, py), weight in screen_samples(pixels):
            total += weight
            if not (x + RADIUS_PX <= px < x + width - RADIUS_PX
                    and y + RADIUS_PX <= py < y + height - RADIUS_PX):
                continue
            visible += weight
            col, row = round(float(px)), round(float(py))
            patch = labels[row-RADIUS_PX:row+RADIUS_PX+1, col-RADIUS_PX:col+RADIUS_PX+1]
            counts = np.bincount(patch.ravel(), minlength=4)[1:]
            count = int(counts.sum())
            if count < 3 or max(counts) < .75 * count:
                continue
            votes[int(np.argmax(counts)) + 1] += weight
        if not visible:
            continue
        coverage = sum(votes.values()) / total if total else 0
        dominant, amount = votes.most_common(1)[0] if votes else (0, 0)
        status, reason = "unknown", "insufficient_changed_traffic_pixels"
        if coverage >= MIN_COVERAGE and sum(votes.values()) >= MIN_SUPPORTED_PX:
            if amount >= .75 * sum(votes.values()):
                status, reason = LABELS[dominant], "paired_color_estimate"
            else:
                reason = "mixed_or_ambiguous_colors"
        features.append({"type": "Feature", "id": road["id"],
                         "geometry": {"type": "LineString", "coordinates": coords},
                         "properties": {"id": road["id"], "name": road.get("name", road["id"]),
                                        "osm_way": road.get("osm_way"), "traffic_status": status,
                                        "direction": "unresolved", "evidence_scope": "road_corridor",
                                        "source": "2gis_screenshot", "observed_at": capture["captured_at"],
                                        "provider_updated_at": None, "reason": reason,
                                        "coverage_fraction": round(coverage, 4),
                                        "validation": "experimental_unvalidated"}})
    return {"type": "FeatureCollection", "features": features,
            "metadata": {"calibration_holdout_error_px": round(residual, 4),
                         "method": "paired_display_colors_v1", "speed_estimate": None,
                         "direction": "unresolved", "not_for_automatic_dispatch": True}}
