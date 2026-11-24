"""Calibration from a measured map anchor instead of five popups per frame."""
import math
import numpy as np
from PIL import Image
import pytest
from traffic_service.geo import (world, geographic, project, scale_for_zoom,
                                 transform_from_anchor, transform_from_meta)
from traffic_service.analysis import color_mask

# Real capture 23.11.2026: tile-00-02, zoom 15, 4096x4096, center from the URL.
CENTER = [158.7912317348358, 52.9357169174551]
ANCHOR = [2258.0, 2058.0]
CONTROLS = [
    ([650, 260], [158.722224, 52.982312]),
    ([1050, 260], [158.739390, 52.982312]),
    ([650, 600], [158.722224, 52.973505]),
    ([1050, 600], [158.739390, 52.973505]),
]


def test_zoom_scale_is_exact_world_pixels():
    assert scale_for_zoom(15) == pytest.approx(2 ** 23 / (2 * math.pi))
    assert scale_for_zoom(16) == pytest.approx(2 * scale_for_zoom(15))


def test_center_projects_to_the_measured_anchor():
    origin = world(CENTER)
    transform = transform_from_anchor(ANCHOR, 15)
    assert project(CENTER, origin, transform) == pytest.approx(ANCHOR, abs=1e-6)


def test_anchor_reproduces_real_ui_control_points():
    """The one-point anchor must place the recorded popup coordinates sub-pixel."""
    origin = world(CENTER)
    transform = transform_from_anchor(ANCHOR, 15)
    for pixel, lon_lat in CONTROLS:
        assert project(lon_lat, origin, transform) == pytest.approx(pixel, abs=0.6)


def test_meta_prefers_anchor_and_keeps_five_point_fit_compatible():
    meta = {'center': CENTER, 'zoom': 15, 'anchor': ANCHOR}
    origin, transform = transform_from_meta(meta)
    assert project(CENTER, origin, transform) == pytest.approx(ANCHOR, abs=1e-6)
    legacy = {'control_points': [{'pixel': p, 'lon_lat': ll} for p, ll in CONTROLS],
              'zoom': None}
    origin, transform = transform_from_meta(legacy)
    for pixel, lon_lat in CONTROLS:
        assert project(lon_lat, origin, transform) == pytest.approx(pixel, abs=1e-6)


def test_yandex_ribbon_colours_are_classified_and_casing_ignored():
    off = Image.new('RGB', (7, 1), (230, 230, 230))
    on = Image.new('RGB', (7, 1))
    pixels = [(55, 203, 82), (94, 205, 76), (255, 187, 51), (235, 50, 50),
              (255, 255, 255), (245, 243, 241), (230, 230, 230)]
    for x, colour in enumerate(pixels):
        on.putpixel((x, 0), colour)
    assert color_mask(off, on).tolist() == [[1, 1, 2, 3, 0, 0, 0]]
