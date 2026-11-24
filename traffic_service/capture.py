"""One bounded public-UI Yandex Maps capture, preserving the desktop browser.

Calibration is measured once per run from the map URL, the map container box and
one UI control point. The pixel transform is then reused for every tile, because
zoom and viewport stay fixed; only the map center changes.
"""
from datetime import datetime, timezone
import base64
import io
import json
import math
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import numpy as np
from PIL import Image
from traffic_service.geo import scale_for_zoom, transform_from_anchor, world
from scripts.capture_traffic import digest, write_json
from scripts.probe_yandex_maps import camera, traffic_enabled

# Yandex reserves a 20 px strip at the top of the map surface. The projection
# center is therefore 10 px below the geometric center of .map-container.
# Measured 2026-11-23 on 2048x2048, 4096x2048 and 4096x4096 (dpr 1): X equals the
# container center exactly, Y equals height/2 + 10 within 0.02 px.
TOP_INSET_PX = 20
GEOMETRY_TOLERANCE_PX = 2.0


def cdp_session(page):
    """Raw CDP screenshots skip Playwright's animation/font waits and are ~5x faster here."""
    return page.context.new_cdp_session(page)


def screenshot(session, path=None, clip=None, fmt='png', quality=None):
    params = {'format': fmt, 'captureBeyondViewport': False}
    if clip:
        params['clip'] = {**clip, 'scale': 1}
    if quality is not None:
        params['quality'] = quality
    data = base64.b64decode(session.send('Page.captureScreenshot', params)['data'])
    if path:
        Path(path).write_bytes(data)
    return data


class ProviderBlocked(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat()


def current_url(page):
    return page.evaluate("location.href")


def check(page, response=None):
    title = page.title().lower()
    url = current_url(page)
    if (response and response.status in (403, 429)) or any(
            s in (title + ' ' + url).lower()
            for s in ('captcha', 'showcaptcha', 'капча', 'доступ ограничен')):
        raise ProviderBlocked('Источник запросил проверку или ограничил доступ. Проверьте открытую вкладку вручную.')
    if response and not response.ok:
        raise RuntimeError(f'Источник вернул HTTP {response.status}')


def map_rect(page):
    box = page.evaluate(
        "()=>{const e=document.querySelector('.map-container');"
        "if(!e)return null;const b=e.getBoundingClientRect();"
        "return [b.x,b.y,b.width,b.height]}")
    if not box or min(box[2], box[3]) < 200:
        raise RuntimeError('Карта не найдена или окно слишком мало. Откройте Яндекс Карты в рабочем окне.')
    return [float(v) for v in box]


def geometry_anchor(page):
    x, y, w, h = map_rect(page)
    inner = page.evaluate('({w:innerWidth,h:innerHeight})')
    return [x + w / 2.0, inner['h'] / 2.0 + TOP_INSET_PX / 2.0], [x, y, w, h]


def open_whats_here(page, px, py):
    """Yandex ignores a synthetic DOM `click` on the menu item; Enter activates it."""
    page.mouse.click(px, py, button='right')
    item = page.get_by_role('menuitem', name='Что здесь?', exact=True)
    item.wait_for(state='visible', timeout=6000)
    item.press('Enter', timeout=5000)
    page.wait_for_function(
        "()=>new URL(location.href).searchParams.has('whatshere[point]')", timeout=10000)
    query = parse_qs(urlsplit(current_url(page)).query)
    lon, lat = query['whatshere[point]'][0].split(',')
    return [float(lon), float(lat)]


def close_card(page):
    try:
        page.locator('[aria-label="Закрыть"]').first.evaluate('(e)=>e.click()')
    except Exception:
        page.keyboard.press('Escape')
    page.wait_for_function(
        "()=>!new URL(location.href).searchParams.has('whatshere[point]')", timeout=6000)


def camera_state(page):
    return camera(current_url(page))


def navigate(page, center, zoom, cancel):
    if cancel():
        raise Cancelled('Съёмка отменена')
    url = f'https://yandex.ru/maps/78/petropavlovsk/?ll={center[0]:.9f}%2C{center[1]:.9f}&z={zoom}'
    response = page.goto(url, wait_until='domcontentloaded', timeout=45000)
    check(page, response)
    page.wait_for_selector('.map-container', state='attached', timeout=20000)
    try:
        page.wait_for_load_state('networkidle', timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(1200)
    state = camera_state(page)
    if not state.get('ll') or int(float(state['z'][0])) != int(zoom):
        raise RuntimeError('Карта не перешла в заданную камеру')
    return state


def readiness(page, region, cancel):
    """Two similar non-blank map-only thumbnails; bounded, no fixed long sleep."""
    x, y, w, h = region
    side = int(min(400, w, h))
    clip = {'x': x + (w - side) / 2, 'y': y + (h - side) / 2, 'width': side, 'height': side}
    session = cdp_session(page)
    previous = None
    deadline = time.monotonic() + 16
    while time.monotonic() < deadline:
        if cancel():
            raise Cancelled('Съёмка отменена')
        check(page)
        data = screenshot(session, clip=clip, fmt='jpeg', quality=35)
        frame = np.asarray(Image.open(io.BytesIO(data)).convert('RGB'), dtype=np.int16)
        # Sea and forest are legitimately low-variance; only a uniform light
        # surface means the map canvas has not rendered yet.
        if float(frame.std()) < 4 and float(frame.mean()) > 225:
            raise RuntimeError('Область карты пуста или не загрузилась')
        if previous is not None and float(np.mean(np.max(np.abs(frame - previous), axis=2) > 24)) < .015:
            return
        previous = frame
        page.wait_for_timeout(600)
    raise RuntimeError('Карта не стабилизировалась за отведённое время')


def calibrate(page, zoom, reference=(158.65, 53.045), cancel=lambda: False):
    """Measure the pixel anchor of the map center with one independent UI point."""
    navigate(page, reference, zoom, cancel)
    anchor, rect = geometry_anchor(page)
    state = camera_state(page)
    lon, lat = map(float, state['ll'][0].split(','))
    x, y, w, h = rect
    px, py = x + w * 0.30, y + h * 0.40
    point = open_whats_here(page, px, py)
    close_card(page)
    scale = scale_for_zoom(zoom)
    origin = world([lon, lat])
    target = world(point)
    measured = [px - (target[0] - origin[0]) * scale,
                py + (target[1] - origin[1]) * scale]
    delta = math.hypot(measured[0] - anchor[0], measured[1] - anchor[1])
    if delta > GEOMETRY_TOLERANCE_PX:
        raise RuntimeError(
            f'Геопривязка не совпала с геометрией карты на {delta:.2f} px; '
            'изменилась вёрстка источника, съёмка остановлена')
    return {'anchor': measured, 'rect': rect, 'residual_px': round(delta, 4),
            'reference_center': [lon, lat], 'zoom': zoom,
            'control_point': {'pixel': [px, py], 'lon_lat': point}}


def capture(page, output, center, zoom, viewport, calibration, cancel=lambda: False):
    """One off/on pair in the current camera. Blueprint, not yet accepted data."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    region = [460, 140, viewport[0] - 620, viewport[1] - 360]
    if min(region[2:]) < 500:
        raise ValueError('Viewport too small')
    anchor = calibration['anchor']
    transform = transform_from_anchor(anchor, zoom)
    start = time.monotonic()
    meta = dict(source='yandex_maps_ui', schema_version=1,
                projection='wgs84_ellipsoidal_mercator', center=list(center),
                zoom=zoom, viewport=list(viewport), analysis_region=region,
                anchor=list(anchor), transform=transform.tolist(),
                anchor_residual_px=calibration.get('residual_px'),
                calibration_control_point=calibration.get('control_point'),
                provider_updated_at=None, direction='unresolved',
                control_points=[], validation='ui_observation_not_field_validated')
    try:
        if cancel():
            raise Cancelled('Съёмка отменена')
        navigate(page, center, zoom, cancel)
        state = camera_state(page)
        actual = [float(v) for v in state['ll'][0].split(',')]
        meta['center'] = actual
        base_camera = {k: state.get(k) for k in ('ll', 'z')}
        readiness(page, region, cancel)
        meta.update(browser_version=page.context.browser.version,
                    source_url=current_url(page), reference_captured_at=utc())
        session = cdp_session(page)
        screenshot(session, output / 'traffic-off.png')
        page.locator('[aria-label^="Пробки в "]').evaluate('(e)=>e.click()')
        page.wait_for_function(
            "()=>{const u=new URL(location.href);return u.pathname.includes('/probki/')"
            "||(u.searchParams.get('l')||'').includes('trf')}", timeout=15000)
        if {k: camera_state(page).get(k) for k in ('ll', 'z')} != base_camera:
            raise RuntimeError('Камера изменилась при включении пробок')
        readiness(page, region, cancel)
        meta.update(traffic_url=current_url(page), captured_at=utc())
        screenshot(session, output / 'traffic-on.png')
        meta.update(duration_s=time.monotonic() - start,
                    image_sha256={n: digest(output / n)
                                  for n in ('traffic-off.png', 'traffic-on.png')})
        write_json(output / 'capture.json', meta)
        return meta
    except BaseException as exc:
        write_json(output / 'failure.json',
                   {'at': utc(), 'error': str(exc), 'duration_s': time.monotonic() - start})
        try:
            screenshot(cdp_session(page), output / 'failure.png')
        except Exception:
            pass
        raise
