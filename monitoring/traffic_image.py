"""Conservative corridor observations from paired, UI-georeferenced screenshots.

Colors are provider display classes, never measured speeds or travel directions.
"""
from collections import Counter
from datetime import datetime
import math

import numpy as np

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
    changed = np.max(np.abs(new - old), axis=2) >= 20
    r, g, b = new.transpose(2, 0, 1)
    labels = np.zeros(r.shape, dtype=np.uint8)
    labels[changed & (g > 100) & (g > r * 1.18) & (g > b * 1.18)] = 1
    labels[changed & (r > 190) & (g > 135) & (b < 135) & (r > g * .95)] = 2
    labels[changed & (r > 170) & (g < 135) & (b < 135)] = 3
    return labels


def extract_traffic_lines(before, after, analysis_region):
    """Vectorize source ribbons without receiving or consulting any OSM roads."""
    import cv2
    labels = color_mask(before, after)
    x, y, width, height = map(int, analysis_region)
    result = []
    for label, status in LABELS.items():
        mask = np.zeros(labels.shape, dtype=np.uint8)
        mask[y:y+height, x:x+width] = (labels[y:y+height, x:x+width] == label) * 255
        count, components, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        allowed = np.zeros(count, dtype=bool)
        widths, heights = stats[1:, cv2.CC_STAT_WIDTH], stats[1:, cv2.CC_STAT_HEIGHT]
        extent = np.maximum(widths, heights)
        aspect = extent / np.maximum(1, np.minimum(widths, heights))
        fill = stats[1:, cv2.CC_STAT_AREA] / (widths * heights)
        # Small compact glyphs/logos are not elongated traffic ribbons.
        allowed[1:] = (extent >= 10) & ~((extent < 40) & (aspect < 1.8) & (fill > .3))
        mask = (allowed[components] * 255).astype(np.uint8)
        # A thin ribbon remains a line. Unlike erosion, Hough preserves one-pixel strokes.
        lines = cv2.HoughLinesP(mask, 1, np.pi / 180, threshold=5, minLineLength=6, maxLineGap=3)
        if lines is None:
            continue
        unique = set()
        for raw in lines[:, 0]:
            a, b = np.asarray(raw[:2], dtype=float), np.asarray(raw[2:], dtype=float)
            if tuple(a) > tuple(b):
                a, b = b, a
            key = tuple(map(int, [*a, *b]))
            if key in unique:
                continue
            unique.add(key)
            # Reject a straight hypothesis that only bridges isolated glyphs.
            samples = np.linspace(a, b, max(2, math.ceil(np.linalg.norm(b-a))))
            hits = 0
            for px, py in samples:
                col, row = int(round(px)), int(round(py))
                hits += bool(np.any(mask[max(0,row-1):row+2, max(0,col-1):col+2]))
            if hits / len(samples) < .85:
                continue
            result.append({"id": f"traffic-{label}-{len(result)}", "traffic_status": status,
                           "pixels": [a.tolist(), b.tolist()]})
    return result


def unproject(pixel, origin, transform):
    coordinate = (np.asarray(pixel) - transform[2]) @ np.linalg.inv(transform[:2]) + origin
    return [math.degrees(coordinate[0]), math.degrees(math.atan(math.sinh(coordinate[1])))]


def match_traffic_lines(lines, projected, roads, region):
    """Assign source samples to nearby, parallel OSM geometry before aggregation."""
    radius, cell_size = 12., 24.
    x, y, width, height = region
    grid, road_parts, lengths = {}, [], []
    assignments = [[] for _ in roads]
    for road_index, points in enumerate(projected):
        parts, cumulative = [], 0.
        for a, b in zip(points, points[1:]):
            vector = b-a
            length = float(np.linalg.norm(vector))
            if not length:
                continue
            part = (a, b, vector / length, length, cumulative, road_index)
            parts.append(part)
            west, north = np.minimum(a, b) - radius
            east, south = np.maximum(a, b) + radius
            if east >= x and west < x+width and south >= y and north < y+height:
                for col in range(math.floor(max(west,x-radius)/cell_size), math.floor(min(east,x+width+radius)/cell_size)+1):
                    for row in range(math.floor(max(north,y-radius)/cell_size), math.floor(min(south,y+height+radius)/cell_size)+1):
                        grid.setdefault((col,row), []).append(part)
            cumulative += length
        road_parts.append(parts)
        lengths.append(cumulative)
    for line in lines:
        a, b = np.asarray(line["pixels"], dtype=float)
        vector = b-a
        length = float(np.linalg.norm(vector))
        tangent = vector / length
        for point in np.linspace(a, b, max(2, math.ceil(length / 2)+1)):
            candidates = {}
            for start, end, direction, part_length, offset, index in grid.get(tuple(np.floor(point/cell_size).astype(int)), ()):
                alignment = abs(float(tangent @ direction))
                if alignment < math.cos(math.radians(20)):
                    continue
                along = float(np.clip((point-start) @ direction, 0, part_length))
                distance = float(np.linalg.norm(point-(start+direction*along)))
                if distance > radius:
                    continue
                score = distance + 12*(1-alignment)
                if index not in candidates or score < candidates[index][0]:
                    candidates[index] = (score, offset+along, distance)
            ordered = sorted(candidates, key=lambda index: candidates[index][0])
            if not ordered:
                continue
            best = ordered[0]
            # Closely competing parallel roads are unresolved rather than double painted.
            group = roads[best].get("osm_way", roads[best]["id"])
            ambiguous = any(roads[i].get("osm_way", roads[i]["id"]) != group
                            and candidates[i][0] < candidates[best][0]+2 for i in ordered[1:])
            if ambiguous:
                continue
            _, along, distance = candidates[best]
            assignments[best].append((along, line["traffic_status"], distance, line["id"]))
    results = []
    for index, parts in enumerate(road_parts):
        votes, total, visible, matched_ids = Counter(), lengths[index], 0., set()
        assigned = assignments[index]
        for start, end, direction, length, offset, _ in parts:
            count = max(1, math.ceil(length/2))
            weight = length/count
            for j in range(count):
                along = (j+.5)*weight
                px, py = start+direction*along
                if not (x <= px < x+width and y <= py < y+height):
                    continue
                visible += weight
                nearby = [p for p in assigned if abs(p[0]-(offset+along)) <= 3]
                if not nearby:
                    continue
                by_class = {}
                for _, status, distance, line_id in nearby:
                    by_class[status] = min(by_class.get(status, math.inf), distance)
                ranked = sorted(by_class, key=by_class.get)
                if len(ranked)>1 and by_class[ranked[1]]-by_class[ranked[0]] <= 3:
                    continue
                votes[ranked[0]] += weight
                matched_ids.update(p[3] for p in nearby if p[1] == ranked[0])
        if not visible:
            results.append(None)
            continue
        coverage = sum(votes.values())/total if total else 0.
        status, reason = "unknown", "insufficient_matched_line_coverage"
        if coverage >= .6 and sum(votes.values()) >= min(6, total*.6):
            dominant, amount = votes.most_common(1)[0]
            if amount >= .75*sum(votes.values()):
                status, reason = dominant, "geometry_matched_source_lines"
            else:
                reason = "mixed_or_ambiguous_colors"
        results.append({"traffic_status": status, "reason": reason,
                        "coverage_fraction": round(coverage,4),
                        "visible_fraction": round(visible/total,4) if total else 0,
                        "source_line_ids": sorted(matched_ids)})
    return results


def analyze_roads(before, after, capture, roads):
    if before.size != after.size or max(before.size) > 4096:
        raise ValueError("Screenshot pair must have equal dimensions, at most 4096 pixels")
    if "viewport" in capture and tuple(capture["viewport"]) != before.size:
        raise ValueError("Screenshot dimensions must match the CSS viewport")
    timestamp = datetime.fromisoformat(capture["captured_at"])
    if timestamp.tzinfo is None:
        raise ValueError("Capture time requires a UTC offset")
    origin, transform, residual = calibration(capture)
    x, y, width, height = map(float, capture["analysis_region"])
    if not all(map(math.isfinite, (x, y, width, height))) or not (
        0 <= x < x + width <= before.width and 0 <= y < y + height <= before.height
    ):
        raise ValueError("Analysis region must fit inside the screenshots")
    lines = extract_traffic_lines(before, after, capture["analysis_region"])
    projected = [[project(point, origin, transform) for point in road["coordinates"]] for road in roads]
    if any(len(points)<2 or any(np.linalg.norm(b-a)>100000 for a,b in zip(points,points[1:])) for points in projected):
        raise ValueError("Invalid road geometry")
    observations = match_traffic_lines(lines, projected, roads, (x,y,width,height))
    features = []
    for road, observed in zip(roads, observations):
        if observed is None:
            continue
        features.append({"type": "Feature", "id": road["id"],
                         "geometry": {"type": "LineString", "coordinates": road["coordinates"]},
                         "properties": {"id": road["id"], "name": road.get("name", road["id"]),
                                        "osm_way": road.get("osm_way"), **observed,
                                        "direction": "unresolved", "evidence_scope": "road_corridor",
                                        "source": "2gis_screenshot", "observed_at": capture["captured_at"],
                                        "provider_updated_at": None, "validation": "experimental_unvalidated"}})
    source = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": line["id"],
         "geometry": {"type": "LineString", "coordinates": [unproject(p, origin, transform) for p in line["pixels"]]},
         "properties": {"traffic_status": line["traffic_status"], "pixels": line["pixels"],
                        "source": "2gis_screenshot", "direction": "unresolved",
                        "observed_at": capture["captured_at"], "validation": "experimental_unvalidated",
                        "evidence_scope": "extracted_display_ribbon"}} for line in lines]}
    return {"type": "FeatureCollection", "features": features, "traffic_lines": source,
            "metadata": {"calibration_holdout_error_px": round(residual,4),
                         "method": "source_lines_geometry_match_v2", "source_line_count": len(lines),
                         "speed_estimate": None, "direction": "unresolved", "not_for_automatic_dispatch": True}}
