"""Manually capture 2GIS traffic and review conservative corridor estimates on OSM."""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
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


class CaptureBlockedError(RuntimeError):
    """Provider refused access; never retried automatically."""


class CaptureTransientError(RuntimeError):
    """A temporary provider response permits one bounded retry."""


def write_text_atomic(path, text):
    descriptor, temporary = tempfile.mkstemp(prefix="."+path.name+".",dir=path.parent)
    try:
        with os.fdopen(descriptor,"w",encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path, payload):
    write_text_atomic(path,json.dumps(payload,ensure_ascii=False,indent=2)+"\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def chrome_page(executable):
    # Same isolated Chrome + CDP approach as LAB's public-card collector.
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix="kamdispatch-traffic-", ignore_cleanup_errors=True) as directory:
        process = subprocess.Popen([
            executable, "--user-data-dir=" + directory, "--remote-debugging-port=0",
            "--no-first-run", "--no-default-browser-check", "--force-device-scale-factor=1",
            "--disable-backgrounding-occluded-windows", "about:blank",
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
    if response and response.status in (403,429):
        raise CaptureBlockedError("2GIS blocked the request; capture stopped without bypass")
    if response and response.status >= 500:
        raise CaptureTransientError(f"2GIS returned HTTP {response.status}")
    if response and not response.ok:
        raise RuntimeError(f"2GIS returned HTTP {response.status}")
    title=page.title().lower()
    if any(marker in title for marker in ("captcha","капча","доступ ограничен")):
        raise CaptureBlockedError("2GIS blocked the request; capture stopped without bypass")


def navigate(page, url):
    from playwright.sync_api import TimeoutError as BrowserTimeoutError
    try:
        response=page.goto(url,wait_until="domcontentloaded",timeout=45000)
    except BrowserTimeoutError as exc:
        check_block(page)
        raise CaptureTransientError("2GIS main-page navigation timed out") from exc
    check_block(page,response)
    # UI timeouts may indicate provider changes or an unrecognized challenge; do not retry them.
    page.get_by_role("button",name="Пробки",exact=True).wait_for()
    page.wait_for_timeout(1800)
    check_block(page)


def wait_map_resources(page):
    """Allow map requests/rendering to settle; PNG content is verified separately."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    network_idle=True
    try:
        page.wait_for_load_state("networkidle",timeout=12000)
    except PlaywrightTimeoutError:
        network_idle=False
    page.wait_for_timeout(10000)
    check_block(page)
    return {"network_idle":network_idle,"settle_seconds":10,
            "content_verification":"validate_basemap_pair on saved PNGs"}


def capture(output, chrome, center, zoom, viewport=(1440,1000)):
    from monitoring.traffic_image import calibration, mercator, validate_basemap_pair, validate_capture_geometry
    from PIL import Image
    mercator(center)
    if not 16 <= zoom <= 18:
        raise ValueError("Use zoom 16–18")
    viewport = tuple(viewport)
    if len(viewport)!=2 or not (1440 <= viewport[0] <= 4096 and 1000 <= viewport[1] <= 4096):
        raise ValueError("Viewport must be 1440–4096 by 1000–4096 CSS pixels")
    region = [460,220,880,460] if viewport==(1440,1000) else [460,220,viewport[0]-560,viewport[1]-320]
    if viewport==(1440,1000):
        control_pixels = ((600,300),(1200,300),(600,750),(1200,750))
    else:
        x,y,width,height = region
        left,right = round(x+.2*width),round(x+.8*width)
        top,bottom = round(y+.2*height),round(y+.8*height)
        control_pixels = ((left,top),(right,top),(left,bottom),(right,bottom))
    output.mkdir(parents=True, exist_ok=False)
    url = f"https://2gis.ru/p_kamchatskiy?m={center[0]}%2C{center[1]}%2F{zoom}"
    try:
        with chrome_page(chrome) as page:
            page.set_viewport_size({"width":viewport[0],"height":viewport[1]})
            controls = []
            for x, y in control_pixels:
                navigate(page, url)
                if not controls:
                    page.wait_for_timeout(5000)
                page.mouse.click(x, y, button="right")
                page.get_by_text("Что здесь?", exact=True).click()
                page.wait_for_url(re.compile(r"/geo/[^/]+/[-\d.]+(?:%2C|,)[-\d.]+"))
                check_block(page)
                point = re.search(r"/geo/[^/]+/([-\d.]+),([-\d.]+)", unquote(page.url))
                if point is None:
                    raise RuntimeError("Cannot read UI control-point coordinates")
                controls.append({"pixel": [x, y], "lon_lat": [float(point[1]), float(point[2])]})
                print(f"UI control point {len(controls)}/4", flush=True)
            metadata = {"schema_version": 1, "source": "2gis_screenshot", "source_url": url,
                        "center": center, "zoom": zoom, "viewport": list(viewport), "device_scale_factor":page.evaluate("window.devicePixelRatio"),
                        "analysis_region": region, "control_points": controls,
                        "provider_updated_at": None, "direction": "unresolved",
                        "validation": "experimental_unvalidated"}
            _, _, residual = calibration(metadata)
            metadata["calibration_holdout_error_px"] = residual
            navigate(page, url)
            if "traffic" in parse_qs(urlsplit(page.url).query, keep_blank_values=True):
                raise RuntimeError("Expected traffic disabled for the reference screenshot")
            camera = parse_qs(urlsplit(page.url).query).get("m")
            reference_ready = wait_map_resources(page)
            metadata["reference_captured_at"] = datetime.now(timezone.utc).isoformat()
            page.screenshot(path=str(output / "traffic-off.png"), animations="disabled", scale="css")
            page.get_by_role("button", name="Пробки", exact=True).click()
            page.wait_for_url(lambda u: "traffic" in parse_qs(urlsplit(u).query, keep_blank_values=True))
            page.wait_for_timeout(3000)
            check_block(page)
            if parse_qs(urlsplit(page.url).query).get("m") != camera:
                raise RuntimeError("Camera changed while enabling traffic")
            traffic_ready = wait_map_resources(page)
            metadata["captured_at"] = datetime.now(timezone.utc).isoformat()
            metadata["traffic_url"] = page.url
            page.screenshot(path=str(output / "traffic-on.png"), animations="disabled", scale="css")
            metadata["image_sha256"] = {name: digest(output / name) for name in ("traffic-off.png", "traffic-on.png")}
            with Image.open(output/"traffic-off.png") as before, Image.open(output/"traffic-on.png") as after:
                validate_capture_geometry(before,after,metadata)
                metadata["capture_quality"] = validate_basemap_pair(before,after,region)
            metadata["render_readiness"] = {"reference":reference_ready,"traffic":traffic_ready}
            write_json(output / "capture.json", metadata)
    except (Exception,KeyboardInterrupt) as exc:
        write_json(output / "capture-failure.json", {"status": "failed", "error": str(exc),
                                                   "at": datetime.now(timezone.utc).isoformat()})
        raise


REVIEW_HTML = r'''<!doctype html>
<html lang="ru"><meta charset="utf-8"><title>Проверка пробок на графе OSM</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>body{margin:0;font:16px system-ui;color:#182c35}header{padding:14px 22px;background:#f2f5f6}h1{font-size:20px;margin:0 0 8px}p{margin:6px 0}#map{height:76vh}.legend{display:flex;gap:16px;flex-wrap:wrap}.swatch{display:inline-block;width:20px;height:5px;vertical-align:middle}</style>
<header><h1>Пробки на дорожном графе OSM · учебная оценка</h1>
<p id="capture"></p><p>Статичный снимок. Тонкие линии — выделенные фрагменты 2ГИС, толстые — подтверждённые фрагменты OSM. Пунктир — модельная оценка по связанным дорогам, не наблюдение 2ГИС. Направление и скорость неизвестны.</p>
<p class="legend"><span>🟢 Свободно</span><span>🟡 Замедление</span><span>🔴 Сильное замедление</span><span>⚪ Нет надёжной оценки</span></p><p id="counts"></p></header><div id="map"></div>
<script id="data" type="application/json">__DATA__</script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const data=JSON.parse(document.getElementById('data').textContent);
const labels={free:'Свободно',slow:'Замедление',congested:'Сильное замедление',unknown:'Нет надёжной оценки'};
const colors={free:'#26863c',slow:'#d29b00',congested:'#d53939',unknown:'#88939a'};
const counts={};for(const f of data.features){const s=f.properties.traffic_status;counts[s]=(counts[s]||0)+1;}
const modelFeatures=data.model_estimates?.features??[];
const supportedModel=modelFeatures.filter(f=>f.properties.estimated_status!=='unknown'&&f.properties.basis!=='sample_prior_only');
const observedCount=data.features.filter(f=>f.properties.traffic_status!=='unknown').length;
document.getElementById('counts').textContent='Оценены: '+(observedCount+supportedModel.length)+' / '+data.features.length+' · Наблюдаемые целиком: '+observedCount+' · Модельные: '+supportedModel.length+' · Без оценки: '+(data.features.length-observedCount-supportedModel.length);
document.getElementById('capture').textContent='Получено: '+data.metadata.captured_at+' · Контроль геопривязки: '+data.metadata.calibration_holdout_error_px+' px';
const map=L.map('map').setView([53.045,158.65],16);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(map);
map.createPane('model');map.getPane('model').style.zIndex=390;
const roads=L.geoJSON(data,{style:f=>({color:colors[f.properties.traffic_status],weight:5,opacity:.85}),onEachFeature:(f,layer)=>{
 const p=f.properties,box=document.createElement('div');
 box.textContent=p.name+' · '+labels[p.traffic_status]+' · Покрытие цветовым сигналом '+Math.round(p.coverage_fraction*100)+'% · '+p.reason;
 layer.bindPopup(box);
}});
const matched=L.geoJSON(data.matched_sections??{type:'FeatureCollection',features:[]},{style:f=>({color:colors[f.properties.traffic_status],weight:6,opacity:.95}),onEachFeature:(f,layer)=>{const p=f.properties,box=document.createElement('div');box.textContent=p.name+' · '+labels[p.traffic_status]+' · Только показанный фрагмент · '+p.road_id;layer.bindPopup(box);}}).addTo(map);
const basisLabels={graph_neighbors:'По соседним дорогам графа',partial_observation_and_graph:'По частичному наблюдению и соседям',sample_prior_only:'Нет локальной опоры'};
const model=L.geoJSON({type:'FeatureCollection',features:modelFeatures},{pane:'model',style:f=>({color:colors[f.properties.estimated_status],weight:4,dashArray:'8 6',opacity:f.properties.estimated_status==='unknown'?.5:.7}),onEachFeature:(f,layer)=>{const p=f.properties,box=document.createElement('div');box.textContent=p.name+' · МОДЕЛЬ: '+labels[p.estimated_status]+' · '+basisLabels[p.basis]+' · Расстояние по графу: '+(p.nearest_observation_m===null?'нет':p.nearest_observation_m+' м')+' · Влияние локальных наблюдений: '+Math.round(p.graph_evidence_share*100)+'% (не вероятность точности) · Веса классов: '+Object.entries(p.class_scores).map(([k,v])=>labels[k]+' '+Math.round(v*100)+'%').join(', ')+' · По снимку '+p.based_on_observations_at;layer.bindPopup(box);}}).addTo(map);
const source=L.geoJSON(data.traffic_lines??{type:'FeatureCollection',features:[]},{style:f=>({color:colors[f.properties.traffic_status],weight:2,opacity:.9}),onEachFeature:(f,layer)=>{const box=document.createElement('div');box.textContent='Выделенный фрагмент 2ГИС · '+labels[f.properties.traffic_status]+' · '+f.id;layer.bindPopup(box);}});
L.control.layers({}, {'Наблюдаемые фрагменты OSM':matched,'Модельные оценки OSM (пунктир)':model,'Оценка всей дороги OSM':roads,'Выделенные линии 2ГИС':source},{collapsed:false}).addTo(map);
if(data.features.length)map.fitBounds(roads.getBounds(),{padding:[24,24],maxZoom:17});
</script></html>'''


@contextmanager
def verified_capture(output):
    """Read immutable capture inputs and independently verify content and geometry."""
    from PIL import Image
    from monitoring.traffic_image import validate_capture_geometry,validate_basemap_pair
    metadata=json.loads((output/"capture.json").read_text())
    if metadata.get("schema_version")!=1:
        raise ValueError("Unsupported capture schema")
    if set(metadata["image_sha256"])!={"traffic-off.png","traffic-on.png"}:
        raise ValueError("Both screenshot hashes are required")
    for name,expected in metadata["image_sha256"].items():
        if digest(output/name)!=expected:
            raise ValueError("Screenshot hash mismatch")
    with Image.open(output/"traffic-off.png") as before, Image.open(output/"traffic-on.png") as after:
        validate_capture_geometry(before,after,metadata)
        quality=validate_basemap_pair(before,after,metadata["analysis_region"])
        yield {**metadata,"capture_quality":quality},before,after


def analyze(output, roads_file, include_model=True):
    from PIL import Image
    from monitoring.traffic_image import analyze_roads, estimate_traffic, validate_basemap_pair
    raw = roads_file.read_bytes()
    payload = json.loads(gzip.decompress(raw) if roads_file.suffix == ".gz" else raw)
    roads = payload["segments"]
    with verified_capture(output) as (metadata,before,after):
        result = analyze_roads(before, after, metadata, roads)
        result["metadata"]["capture_quality"] = metadata["capture_quality"]
    result["metadata"].update(captured_at=metadata["captured_at"], source_url=metadata["source_url"],
                              roads_sha256=hashlib.sha256(raw).hexdigest(), image_sha256=metadata["image_sha256"],
                              algorithm_sha256=digest(ROOT/"monitoring/traffic_image.py"),capture_sha256=digest(output/"capture.json"),
                              analysis_pipeline_sha256=digest(Path(__file__)),
                              analysis_region=metadata["analysis_region"])
    result["model_estimates"] = estimate_traffic(roads,result) if include_model else {"type":"FeatureCollection","features":[]}
    if "target_road_ids" in metadata:
        targets = metadata["target_road_ids"]
        if (not isinstance(targets,list) or not targets or not all(isinstance(i,str) for i in targets)
                or len(set(targets)) != len(targets)):
            raise ValueError("Invalid target road IDs")
        targets = set(targets)
        if targets - {f["id"] for f in result["features"]}:
            raise ValueError("Requested target roads are missing from the captured analysis region")
        result["metadata"]["original_view_road_count"] = len(result["features"])
        result["metadata"]["target_ids_sha256"] = hashlib.sha256(json.dumps(sorted(targets),separators=(",",":")).encode()).hexdigest()
        result["features"] = [f for f in result["features"] if f["id"] in targets]
        result["matched_sections"]["features"] = [f for f in result["matched_sections"]["features"] if f["properties"]["road_id"] in targets]
        result["model_estimates"]["features"] = [f for f in result["model_estimates"]["features"] if f["id"] in targets]
    exports={"traffic-lines.geojson":result["traffic_lines"],"matched-sections.geojson":result["matched_sections"],
             "model-estimates.geojson":result["model_estimates"]}
    for name,payload in exports.items():
        write_json(output/name,payload)
    encoded = json.dumps(result, ensure_ascii=False).replace("<", "\\u003c")
    write_text_atomic(output/"review.html",REVIEW_HTML.replace("__DATA__",encoded))
    result["metadata"]["artifact_sha256"]={name:digest(output/name) for name in [*exports,"review.html"]}
    # The result JSON is the analysis publication marker; write it last.
    write_json(output/"roads.geojson",result)
    counts = Counter(f["properties"]["traffic_status"] for f in result["features"])
    print(json.dumps({"features": len(result["features"]), "classes": counts,
                      "holdout_error_px": result["metadata"]["calibration_holdout_error_px"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture", "analyze", "plan-city", "capture-city", "analyze-city"))
    parser.add_argument("output", type=Path, help="New output directory for capture; existing directory for analyze")
    parser.add_argument("--roads", type=Path, default=ROOT / "monitoring/snow_roads.json.gz")
    parser.add_argument("--chrome", default=shutil.which("google-chrome") or shutil.which("chromium")
                        or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    parser.add_argument("--center", nargs=2, type=float, default=[158.65, 53.045], metavar=("LON", "LAT"))
    parser.add_argument("--zoom", type=int, default=16)
    parser.add_argument("--viewport",nargs=2,type=int,default=[1440,1000],metavar=("WIDTH","HEIGHT"))
    parser.add_argument("--boundary",type=Path,help="City GeoJSON Polygon/Feature for plan-city")
    parser.add_argument("--seed",type=Path,help="Verified large capture directory for plan-city")
    parser.add_argument("--require-full",action="store_true",help="Fail analyze-city unless all reference territory and roads are in verified captures")
    args = parser.parse_args()
    if args.require_full and args.command!="analyze-city":
        parser.error("--require-full is only valid with analyze-city")
    try:
        if args.command == "plan-city":
            if args.boundary is None or args.seed is None:
                parser.error("plan-city requires --boundary and --seed")
            from monitoring.traffic_coverage import prepare_city
            plan=prepare_city(args.output,args.boundary,args.roads,args.seed)
            print(f"Planned {len(plan['tiles'])} captures, including the reused seed")
        elif args.command == "capture-city":
            from monitoring.traffic_coverage import capture_city
            capture_city(args.output,args.chrome)
        elif args.command == "analyze-city":
            from monitoring.traffic_coverage import analyze_city
            analyze_city(args.output,args.require_full)
        else:
            if args.command == "capture":
                capture(args.output, args.chrome, args.center, args.zoom, args.viewport)
            analyze(args.output, args.roads)
    except Exception as exc:
        parser.exit(1, f"Traffic experiment failed: {exc}\n")
