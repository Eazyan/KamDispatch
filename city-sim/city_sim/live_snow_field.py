"""Interpolate regional forcing and accumulate a directional snow response for each cell."""
from collections import defaultdict
from datetime import timedelta

import numpy as np

NEAR_MINUTES = 10
MAX_HOURS = 16 * 24
DIRECTIONS = 16
ANGLE = 360 / DIRECTIONS
INFLUENCE_M = 15_000


def frame_minutes(hours):
    if hours <= 6:
        return list(range(0, hours * 60 + 1, NEAR_MINUTES))
    end = hours * 60
    return sorted({*range(0, 181, NEAR_MINUTES), *range(360, min(end, 7*1440)+1, 360),
                   *range(7*1440+720, end, 720), end})


def provider_series(snapshot, start, hours):
    times = [start+timedelta(hours=i) for i in range(1, hours+1)]
    if any(t not in snapshot["frames"] for t in times):
        raise ValueError("Для этого срока не помещается весь интервал. Выберите более ранний час.")
    locations = defaultdict(list)
    by_hour = [{row["id"]: row for row in snapshot["frames"][t]} for t in times]
    for key, row in by_hour[0].items():
        series = [frame.get(key) for frame in by_hour]
        if all(item and all(item[v] is not None for v in ("snow", "wind_speed", "wind_deg"))
               for item in series):
            locations[tuple(row.get("provider_coordinates", row["coordinates"]))].append(series)
    if not locations:
        raise ValueError("В прямом прогнозе нет одновременно снега и ветра.")
    return times, locations


class LiveSnowField:
    def __init__(self, snapshot, start, hours):
        times, locations = provider_series(snapshot, start, hours)
        self.coordinates = np.asarray(list(locations))
        self.minutes = frame_minutes(hours)
        self.stamps = [(start+timedelta(minutes=minute)).isoformat() for minute in self.minutes]
        coefficients, winds, snowfall = [], [], []
        for recurring in locations.values():
            readings = np.asarray([[[row["snow"], row["wind_speed"], row["wind_deg"]] for row in series]
                                   for series in recurring])
            snow, speed, direction = readings.transpose(2, 0, 1)
            snow, speed = np.maximum(snow, 0), np.maximum(speed, 0)
            position = (direction % 360) / ANGLE
            first = position.astype(int)
            fraction = position-first
            strength = snow * np.clip((speed-2)/10, 0, 1)
            sectors = np.arange(DIRECTIONS)
            weights = (first[..., None] == sectors) * (1-fraction[..., None])
            weights += (((first+1) % DIRECTIONS)[..., None] == sectors) * fraction[..., None]
            directional = (weights * strength[..., None]).mean(axis=0)
            coefficients.append(np.column_stack((snow.mean(axis=0), directional)))
            radians = np.deg2rad(direction)
            winds.append(np.stack(((speed*np.sin(radians)).mean(axis=0), (speed*np.cos(radians)).mean(axis=0))))
            snowfall.append(snow.mean(axis=0))
        hourly = np.asarray(coefficients, dtype=np.float32)
        self.hourly_snow = np.asarray(snowfall)
        self.winds = np.asarray(winds)
        cumulative = np.concatenate((np.zeros_like(hourly[:, :1]), np.cumsum(hourly, axis=1)), axis=1)
        offsets = np.asarray(self.minutes)
        low = offsets // 60
        high = np.minimum(low+1, len(times))
        fraction = (offsets % 60) / 60
        self.fields = (cumulative[:, low] + (cumulative[:, high] - cumulative[:, low]) *
                       fraction[None, :, None]).astype(np.float32)

    def weights(self, lon, lat):
        east = (np.asarray(lon)[..., None]-self.coordinates[:, 0]) * (111_200*np.cos(np.deg2rad(lat))[..., None])
        north = (np.asarray(lat)[..., None]-self.coordinates[:, 1]) * 111_200
        distances = np.hypot(east, north)
        taper = np.maximum(0, 1-(distances/INFLUENCE_M)**2)
        weights = taper**2 / (distances**2+900**2)
        total = weights.sum(axis=-1, keepdims=True)
        return np.divide(weights, total, out=np.zeros_like(weights), where=total > 0).astype(np.float32), distances

    def raster(self, bounds, cells, response):
        w, s, e, n = bounds
        offsets = (np.arange(cells)+.5)/cells
        lon, lat = np.meshgrid(w+(e-w)*offsets, n-(n-s)*offsets)
        weights, distances = self.weights(lon, lat)
        # Compute only the directions used in the returned forecast.
        active = np.flatnonzero(np.any(self.fields[:, -1, 1:] > 0, axis=0))
        baseline = self.fields[:, :, 0].T @ weights.reshape(-1, weights.shape[-1]).T
        snow = baseline.copy()
        reverse = baseline[-1].copy()
        wind = np.hypot(self.winds[:, 0], self.winds[:, 1]).max(axis=1)
        peak = weights @ wind
        if active.size:
            # Interpolate the wind-forcing coefficients spatially before adding building response.
            direct = np.stack([response(int(index)) for index in active])
            opposite = np.stack([response((int(index)+DIRECTIONS//2) % DIRECTIONS) for index in active])
            weighted = np.einsum("pfd,xyp->fdxy", self.fields[:, :, active+1], weights, optimize=True)
            snow += np.einsum("fdxy,dxy->fxy", weighted, np.nan_to_num(direct)).reshape(snow.shape)
            reverse += np.einsum("dxy,dxy->xy", weighted[-1], np.nan_to_num(opposite)).ravel()
            ground = np.isfinite(direct[0])
        else:
            ground = np.isfinite(response(None))
        valid = ground & (weights.sum(axis=-1) > 0)
        shape = (-1, cells, cells)
        return (np.maximum(0, snow.reshape(shape)), baseline.reshape(shape),
                np.maximum(0, reverse.reshape(cells, cells)), peak, distances.min(axis=-1), valid)

    def point_wind(self, lon, lat):
        weights, distances = self.weights(lon, lat)
        offsets = np.maximum(0, (np.asarray(self.minutes)-1)//60)
        vectors = np.einsum("p,pch->ch", weights, self.winds)
        snow = weights @ self.hourly_snow
        distance = float(distances.min())
        return [{"wind_from_deg": float(np.degrees(np.arctan2(vectors[0,i], vectors[1,i])) % 360),
                 "wind_speed_ms": float(np.hypot(*vectors[:,i])), "snowfall_cm": float(snow[i]),
                 "distance_to_source_m": round(distance)} for i in offsets]
