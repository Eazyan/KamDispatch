"""Render the offline city overview from the bundled Neverrie elevation and road data."""
import gzip
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
TERRAIN = ROOT / "kamcha-snow-sim-Neverrie/city_sim/data/terrain.npz"
WEST, SOUTH, EAST, NORTH = 158.48, 52.91, 158.80, 53.16
WIDTH, HEIGHT = 1500, 1940


def mercator(latitude):
    return np.arcsinh(np.tan(np.radians(latitude)))


def main():
    manifest = json.loads(TERRAIN.with_suffix(".json").read_text())
    lon = np.linspace(WEST, EAST, WIDTH)
    top, bottom = mercator(NORTH), mercator(SOUTH)
    lat = np.degrees(np.arctan(np.sinh(np.linspace(top, bottom, HEIGHT))))
    elevations = np.zeros((HEIGHT, WIDTH))
    with np.load(TERRAIN) as tiles:
        for tile in manifest["tiles"]:
            samples = tiles[tile["id"]]
            col = np.clip(np.rint((lon - tile["west"]) / tile["step_lon"]).astype(int), 0, samples.shape[1] - 1)
            row = np.rint((tile["north"] - lat) / tile["step_lat"]).astype(int)
            valid = (row >= 0) & (row < samples.shape[0])
            elevations[valid] = samples[row[valid, None], col[None, :]]
    elevations = np.maximum(elevations, 0)
    dy, dx = np.gradient(elevations)
    shade = np.clip((dx + dy) * .72, -17, 24)
    ramp = np.clip(elevations / 440, 0, 1)
    land = np.stack((35 + 30*ramp + shade, 58 + 26*ramp + shade, 67 + 23*ramp + shade), axis=-1)
    sea = np.array([14, 36, 52])
    rgb = np.where((elevations > .6)[..., None], land, sea).astype("uint8")
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    with gzip.open(ROOT / "monitoring/snow_roads.json.gz", "rt") as handle:
        roads = json.load(handle)
    for road in roads["segments"]:
        xy = [((x - WEST) / (EAST - WEST) * WIDTH, (top - mercator(y)) / (top-bottom) * HEIGHT)
              for x, y in road["coordinates"]]
        draw.line(xy, fill="#597880" if road.get("name") else "#46656e", width=1)
    image.save(ROOT / "web/public/overview/city.webp", quality=82)


if __name__ == "__main__":
    main()
