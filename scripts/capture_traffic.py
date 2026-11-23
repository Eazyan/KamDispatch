"""Manually capture 2GIS traffic and review conservative corridor estimates on OSM."""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def chrome_page(executable):
    # Same isolated Chrome + CDP approach as LAB's public-card collector.
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix="kamdispatch-traffic-", ignore_cleanup_errors=True) as directory:
        process = subprocess.Popen([
            executable, "--user-data-dir=" + directory, "--remote-debugging-port=0",
            "--no-first-run", "--no-default-browser-check", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port_file = Path(directory) / "DevToolsActivePort"
            deadline = time.monotonic() + 15
            while not port_file.exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Chrome did not start a local DevTools endpoint")
                time.sleep(.1)
            port = int(port_file.read_text().splitlines()[0])
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=15000)
                try:
                    page = browser.contexts[0].pages[0]
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    page.set_default_timeout(20000)
                    yield page
                finally:
                    browser.close()
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def check_block(page, response=None):
    title = page.title().lower()
    if (response and response.status in (403, 429)) or any(s in title for s in ("captcha", "капча", "доступ ограничен")):
        raise RuntimeError("2GIS blocked the request; capture stopped without bypass")
    if response and not response.ok:
        raise RuntimeError(f"2GIS returned HTTP {response.status}")


def navigate(page, url):
    response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    check_block(page, response)
    page.get_by_role("button", name="Пробки", exact=True).wait_for()
    page.wait_for_timeout(4000)
    check_block(page)


def capture(output, chrome, center, zoom):
    from monitoring.traffic_image import calibration, mercator
    mercator(center)
    if not 16 <= zoom <= 18:
        raise ValueError("Use zoom 16–18")
    output.mkdir(parents=True, exist_ok=False)
    url = f"https://2gis.ru/p_kamchatskiy?m={center[0]}%2C{center[1]}%2F{zoom}"
    try:
        with chrome_page(chrome) as page:
            controls = []
            for x, y in ((600, 300), (1200, 300), (600, 750), (1200, 750)):
                navigate(page, url)
                # Map controls can appear before the canvas context-menu handler.
                # One bounded retry handles that startup race, never a block/CAPTCHA.
                menu = page.get_by_text("Что здесь?", exact=True)
                for attempt in range(2):
                    page.mouse.click(x, y, button="right")
                    try:
                        menu.wait_for(state="visible", timeout=3000)
                        break
                    except Exception:
                        check_block(page)
                        if attempt:
                            raise
                        page.wait_for_timeout(1500)
                menu.click()
                page.wait_for_url(re.compile(r"/geo/[^/]+/[-\d.]+(?:%2C|,)[-\d.]+"))
                check_block(page)
                point = re.search(r"/geo/[^/]+/([-\d.]+),([-\d.]+)", unquote(page.url))
                if point is None:
                    raise RuntimeError("Cannot read UI control-point coordinates")
                controls.append({"pixel": [x, y], "lon_lat": [float(point[1]), float(point[2])]})
                print(f"UI control point {len(controls)}/4", flush=True)
            metadata = {"schema_version": 1, "source": "2gis_screenshot", "source_url": url,
                        "center": center, "zoom": zoom, "viewport": [1440, 1000],
                        "analysis_region": [460, 220, 880, 460], "control_points": controls,
                        "provider_updated_at": None, "direction": "unresolved",
                        "validation": "experimental_unvalidated"}
            _, _, residual = calibration(metadata)
            metadata["calibration_holdout_error_px"] = residual
            navigate(page, url)
            if "traffic" in parse_qs(urlsplit(page.url).query, keep_blank_values=True):
                raise RuntimeError("Expected traffic disabled for the reference screenshot")
            camera = parse_qs(urlsplit(page.url).query).get("m")
            metadata["reference_captured_at"] = datetime.now(timezone.utc).isoformat()
            page.screenshot(path=str(output / "traffic-off.png"), animations="disabled", scale="css")
            page.get_by_role("button", name="Пробки", exact=True).click()
            page.wait_for_url(lambda u: "traffic" in parse_qs(urlsplit(u).query, keep_blank_values=True))
            page.wait_for_timeout(3000)
            check_block(page)
            if parse_qs(urlsplit(page.url).query).get("m") != camera:
                raise RuntimeError("Camera changed while enabling traffic")
            metadata["captured_at"] = datetime.now(timezone.utc).isoformat()
            metadata["traffic_url"] = page.url
            page.screenshot(path=str(output / "traffic-on.png"), animations="disabled", scale="css")
            metadata["image_sha256"] = {name: digest(output / name) for name in ("traffic-off.png", "traffic-on.png")}
            write_json(output / "capture.json", metadata)
    except Exception as exc:
        write_json(output / "capture-failure.json", {"status": "failed", "error": str(exc),
                                                   "at": datetime.now(timezone.utc).isoformat()})
        raise


REVIEW_HTML = r'''<!doctype html>
<html lang="ru"><meta charset="utf-8"><title>Проверка пробок на графе OSM</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>body{margin:0;font:16px system-ui;color:#182c35}header{padding:14px 22px;background:#f2f5f6}h1{font-size:20px;margin:0 0 8px}p{margin:6px 0}#map{height:76vh}.legend{display:flex;gap:16px;flex-wrap:wrap}.swatch{display:inline-block;width:20px;height:5px;vertical-align:middle}</style>
<header><h1>Пробки на дорожном графе OSM · учебная оценка</h1>
<p id="capture"></p><p>Статичный снимок. Тонкие линии — выделенные фрагменты 2ГИС, толстые — сопоставленные дороги OSM. Направление и скорость неизвестны.</p>
<p class="legend"><span>🟢 Свободно</span><span>🟡 Замедление</span><span>🔴 Сильное замедление</span><span>⚪ Нет надёжной оценки</span></p><p id="counts"></p></header><div id="map"></div>
<script id="data" type="application/json">__DATA__</script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const data=JSON.parse(document.getElementById('data').textContent);
const labels={free:'Свободно',slow:'Замедление',congested:'Сильное замедление',unknown:'Нет надёжной оценки'};
const colors={free:'#26863c',slow:'#d29b00',congested:'#d53939',unknown:'#88939a'};
const counts={};for(const f of data.features){const s=f.properties.traffic_status;counts[s]=(counts[s]||0)+1;}
document.getElementById('counts').textContent=Object.entries(counts).map(([k,v])=>labels[k]+': '+v).join(' · ')+' · Фрагментов 2ГИС: '+(data.traffic_lines?.features.length??0);
document.getElementById('capture').textContent='Получено: '+data.metadata.captured_at+' · Контроль геопривязки: '+data.metadata.calibration_holdout_error_px+' px';
const map=L.map('map').setView([53.045,158.65],16);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(map);
const roads=L.geoJSON(data,{style:f=>({color:colors[f.properties.traffic_status],weight:5,opacity:.85}),onEachFeature:(f,layer)=>{
 const p=f.properties,box=document.createElement('div');
 box.textContent=p.name+' · '+labels[p.traffic_status]+' · Покрытие цветовым сигналом '+Math.round(p.coverage_fraction*100)+'% · '+p.reason;
 layer.bindPopup(box);
}}).addTo(map);
const source=L.geoJSON(data.traffic_lines??{type:'FeatureCollection',features:[]},{style:f=>({color:colors[f.properties.traffic_status],weight:2,opacity:.9}),onEachFeature:(f,layer)=>{const box=document.createElement('div');box.textContent='Выделенный фрагмент 2ГИС · '+labels[f.properties.traffic_status]+' · '+f.id;layer.bindPopup(box);}}).addTo(map);
L.control.layers({}, {'Сопоставление OSM':roads,'Выделенные линии 2ГИС':source},{collapsed:false}).addTo(map);
if(data.features.length)map.fitBounds(roads.getBounds(),{padding:[24,24],maxZoom:17});
</script></html>'''


def analyze(output, roads_file):
    from PIL import Image
    from monitoring.traffic_image import analyze_roads
    metadata = json.loads((output / "capture.json").read_text())
    if metadata.get("schema_version") != 1:
        raise ValueError("Unsupported capture schema")
    for name, expected in metadata["image_sha256"].items():
        if name not in {"traffic-off.png", "traffic-on.png"} or digest(output / name) != expected:
            raise ValueError("Screenshot hash mismatch")
    if set(metadata["image_sha256"]) != {"traffic-off.png", "traffic-on.png"}:
        raise ValueError("Both screenshot hashes are required")
    raw = roads_file.read_bytes()
    payload = json.loads(gzip.decompress(raw) if roads_file.suffix == ".gz" else raw)
    roads = payload["segments"]
    with Image.open(output / "traffic-off.png") as before, Image.open(output / "traffic-on.png") as after:
        result = analyze_roads(before, after, metadata, roads)
    result["metadata"].update(captured_at=metadata["captured_at"], source_url=metadata["source_url"],
                              roads_sha256=hashlib.sha256(raw).hexdigest(), image_sha256=metadata["image_sha256"],
                              analysis_region=metadata["analysis_region"])
    write_json(output / "roads.geojson", result)
    write_json(output / "traffic-lines.geojson", result["traffic_lines"])
    encoded = json.dumps(result, ensure_ascii=False).replace("<", "\\u003c")
    (output / "review.html").write_text(REVIEW_HTML.replace("__DATA__", encoded), encoding="utf-8")
    counts = Counter(f["properties"]["traffic_status"] for f in result["features"])
    print(json.dumps({"features": len(result["features"]), "classes": counts,
                      "holdout_error_px": result["metadata"]["calibration_holdout_error_px"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture", "analyze"))
    parser.add_argument("output", type=Path, help="New output directory for capture; existing directory for analyze")
    parser.add_argument("--roads", type=Path, default=ROOT / "monitoring/snow_roads.json.gz")
    parser.add_argument("--chrome", default=shutil.which("google-chrome") or shutil.which("chromium")
                        or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    parser.add_argument("--center", nargs=2, type=float, default=[158.65, 53.045], metavar=("LON", "LAT"))
    parser.add_argument("--zoom", type=int, default=16)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            capture(args.output, args.chrome, args.center, args.zoom)
        analyze(args.output, args.roads)
    except Exception as exc:
        parser.exit(1, f"Traffic experiment failed: {exc}\n")
