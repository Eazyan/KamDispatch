"""Geometric screenshot coverage, distinct from availability of traffic observations."""
import math
import time

import numpy as np
from shapely.geometry import Polygon,LineString,box,mapping,shape
from shapely.ops import transform as transform_geometry,unary_union

from monitoring.traffic_image import calibration,mercator,project,unproject


def map_geometry(geometry, point_function):
    def convert(xs,ys,z=None):
        coordinates=[point_function((x,y)) for x,y in zip(xs,ys)]
        return tuple(zip(*coordinates))
    return transform_geometry(convert,geometry)


def capture_footprint(capture, region=None):
    origin,transform,_=calibration(capture)
    x,y,width,height=region or capture['analysis_region']
    return Polygon([unproject(point,origin,transform) for point in
                    ((x,y),(x+width,y),(x+width,y+height),(x,y+height))])


def plan_city(boundary, seed, overlap_px=128):
    city=shape(boundary.get('geometry',boundary))
    if not city.is_valid or city.is_empty or city.geom_type not in ('Polygon','MultiPolygon'):
        raise ValueError('A valid city Polygon or MultiPolygon is required')
    origin,transform,_=calibration(seed)
    pixels=map_geometry(city,lambda p:project(p,origin,transform))
    x,y,width,height=seed['analysis_region']
    step_x,step_y=width-2*overlap_px,height-2*overlap_px
    if overlap_px<0 or min(step_x,step_y)<=0:
        raise ValueError('Overlap must leave a positive interior capture area')
    roi_center=np.array([x+width/2,y+height/2])
    camera=project(seed['center'],origin,transform)
    tiles=[]
    for col in range(math.floor(pixels.bounds[0]/step_x),math.floor(pixels.bounds[2]/step_x)+1):
        for row in range(math.floor(pixels.bounds[1]/step_y),math.floor(pixels.bounds[3]/step_y)+1):
            interior=box(col*step_x,row*step_y,(col+1)*step_x,(row+1)*step_y)
            if not interior.intersects(pixels):
                continue
            center=np.array([(col+.5)*step_x,(row+.5)*step_y])
            requested=unproject(center+camera-roi_center,origin,transform)
            tiles.append({'id':f'tile-{col:+03d}-{row:+03d}','center':requested})
    return {'version':1,'viewport':seed['viewport'],'zoom':seed['zoom'],
            'overlap_px':overlap_px,'seed':seed,'tiles':tiles}


def audit_geometry(boundary, roads, captures):
    """Measure actual calibrated footprints. Callers must verify PNGs and pair quality."""
    city=shape(boundary.get('geometry',boundary))
    latitude=math.radians(city.centroid.y)
    scale=6371008.8*math.cos(latitude)
    def metric(point):
        return mercator(point)*scale
    def geographic(point):
        x,y=np.asarray(point)/scale
        return [math.degrees(x),math.degrees(math.atan(math.sinh(y)))]
    metric_city=map_geometry(city,metric)
    footprints=[capture_footprint(capture) for capture in captures]
    metric_footprints=[map_geometry(p,metric) for p in footprints]
    covered=unary_union(metric_footprints)
    missing_city=metric_city.difference(covered)
    road_details=[]
    total_length=missing_length=0.
    for road in roads:
        line=map_geometry(LineString(road['coordinates']),metric)
        length=line.length
        missing=line.difference(covered).length
        full=line.difference(covered).is_empty
        total_length+=length;missing_length+=missing
        road_details.append({'road_id':road['id'],'captured_fraction':1-missing/length if length else 0,
                             'fully_captured':bool(length and full),'missing_length_m':round(missing,6)})
    area=metric_city.area
    detailed=[]
    for capture,footprint in zip(captures,metric_footprints):
        x,y,width,height=capture['analysis_region']
        blocks=capture.get('capture_quality',{}).get('low_detail_blocks',[])
        uncertain=[map_geometry(capture_footprint(capture,[x+col*width/8,y+row*height/8,width/8,height/8]),metric)
                   for col,row in blocks]
        detailed.append(footprint.difference(unary_union(uncertain)))
    detail_gaps=metric_city.difference(unary_union(detailed))
    def collection(geometry):
        return {'type':'FeatureCollection','features':[] if geometry.is_empty else
                [{'type':'Feature','properties':{},'geometry':mapping(map_geometry(geometry,geographic))}]}
    return {'boundary_fully_captured':missing_city.is_empty,
            'boundary_coverage_fraction':1-missing_city.area/area if area else 0,
            'boundary_area_km2':round(area/1e6,3),'uncovered_boundary_area_m2':round(missing_city.area,6),
            'road_count':len(roads),'fully_captured_road_count':sum(r['fully_captured'] for r in road_details),
            'road_length_km':round(total_length/1000,3),
            'road_length_coverage_fraction':1-missing_length/total_length if total_length else 0,
            'uncovered_road_length_m':round(missing_length,6),'road_coverage':road_details,
            'detail_verified_boundary_fraction':1-detail_gaps.area/area if area else 0,
            'metric_note':'WebMercator scaled at city centroid latitude, approximate local metres',
            'footprints':{'type':'FeatureCollection','features':[
                {'type':'Feature','id':capture.get('tile_id',str(i)),
                 'properties':{'tile_id':capture.get('tile_id',str(i)),'captured_at':capture['captured_at'],
                               'low_detail_block_count':len(capture.get('capture_quality',{}).get('low_detail_blocks',[]))},
                 'geometry':mapping(footprint)} for i,(capture,footprint) in enumerate(zip(captures,footprints))]},
            'uncovered_boundary':collection(missing_city),'detail_gaps':collection(detail_gaps)}


def prepare_city(output, boundary_path, roads_path, seed_folder):
    """Freeze public reference inputs and reuse the validated seed image pair."""
    import gzip
    import json
    import shutil
    from datetime import datetime,timezone
    from scripts.capture_traffic import verified_capture,write_json,digest
    boundary=json.loads(boundary_path.read_text())
    with verified_capture(seed_folder) as (seed,_,__):
        plan=plan_city(boundary,seed)
    raw=roads_path.read_bytes()
    roads=json.loads(gzip.decompress(raw) if roads_path.suffix=='.gz' else raw)['segments']
    if not roads or len({r['id'] for r in roads})!=len(roads):
        raise ValueError('Reference roads must have distinct IDs')
    output.mkdir(parents=True,exist_ok=False)
    (output/'captures').mkdir()
    write_json(output/'boundary.geojson',boundary)
    (output/'roads.json.gz').write_bytes(raw if roads_path.suffix=='.gz' else gzip.compress(raw,mtime=0))
    seed_output=output/'captures'/'seed'
    seed_output.mkdir()
    for name in ('capture.json','traffic-off.png','traffic-on.png'):
        shutil.copy2(seed_folder/name,seed_output/name)
    plan['tiles'].sort(key=lambda tile:float(np.linalg.norm(mercator(tile['center'])-mercator(seed['center']))))
    plan['tiles'].insert(0,{'id':'seed','center':seed['center'],'reused_seed':True})
    plan.update(created_at=datetime.now(timezone.utc).isoformat(),
                scope='Entire reference city boundary, including sparse outskirts',
                boundary_sha256=digest(output/'boundary.geojson'),roads_sha256=digest(output/'roads.json.gz'))
    write_json(output/'plan.json',plan)
    return plan


def load_city_job(output):
    import json
    import re
    from scripts.capture_traffic import digest
    plan=json.loads((output/'plan.json').read_text())
    if plan.get('version')!=1:
        raise ValueError('Unsupported city plan version')
    for file,key in (('boundary.geojson','boundary_sha256'),('roads.json.gz','roads_sha256')):
        if digest(output/file)!=plan[key]:
            raise ValueError('City reference input hash mismatch')
    ids=[tile['id'] for tile in plan['tiles']]
    if len(set(ids))!=len(ids) or not all(re.fullmatch(r'[A-Za-z0-9_+-]+',key) for key in ids):
        raise ValueError('Invalid or duplicate capture IDs')
    return plan


def verify_planned_capture(folder,tile,plan):
    from scripts.capture_traffic import verified_capture
    with verified_capture(folder) as (metadata,_,__):
        if metadata['center']!=tile['center'] or metadata['viewport']!=plan['viewport'] or metadata['zoom']!=plan['zoom']:
            raise ValueError('Capture does not match the city plan')
        seed_origin,seed_transform,_=calibration(plan['seed'])
        origin,transform,_=calibration(metadata)
        expected_scale=np.diag(seed_transform[:2])
        if not np.allclose(np.diag(transform[:2]),expected_scale,rtol=.002,atol=0):
            raise ValueError('Actual map scale differs from the planned zoom')
        expected_camera=project(plan['seed']['center'],seed_origin,seed_transform)
        if np.linalg.norm(project(tile['center'],origin,transform)-expected_camera)>2:
            raise ValueError('Actual camera position differs from the requested city tile')
        return metadata


def _capture_tile(output,tile,plan,chrome):
    """Publish only sealed input pairs; retain failed attempts for manual recovery."""
    import uuid
    from scripts.capture_traffic import capture,write_json,CaptureTransientError
    final=output/'captures'/tile['id']
    attempts=output/'attempts'/tile['id']
    attempts.mkdir(parents=True,exist_ok=True)
    if final.exists():
        try:
            verify_planned_capture(final,tile,plan)
            return
        except (ValueError,KeyError,OSError) as exc:
            archived=attempts/('unsealed-'+uuid.uuid4().hex)
            final.rename(archived)
            write_json(archived/'attempt-outcome.json',{'status':'invalid_existing_capture','error':str(exc)})
    # A previous process may have sealed its PNGs and died before publication.
    for marker in sorted(attempts.glob('*/capture.json')):
        try:
            verify_planned_capture(marker.parent,tile,plan)
        except (ValueError,KeyError,OSError) as exc:
            write_json(marker.parent/'recovery-error.json',{'error':str(exc)})
            continue
        marker.parent.rename(final)
        return
    for index in range(2):
        attempt=attempts/('attempt-'+uuid.uuid4().hex)
        try:
            capture(attempt,chrome,tile['center'],plan['zoom'],plan['viewport'])
            verify_planned_capture(attempt,tile,plan)
            attempt.rename(final)
            return
        except (Exception,KeyboardInterrupt) as exc:
            attempt.mkdir(parents=True,exist_ok=True)
            retryable=isinstance(exc,CaptureTransientError)
            write_json(attempt/'attempt-outcome.json',{'status':'interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',
                'error_type':type(exc).__name__,'error':str(exc),'attempt':index+1,'retryable':retryable})
            if retryable and index==0:
                print(f"RETRY {tile['id']} after temporary failure",flush=True)
                time.sleep(2)
                continue
            raise


def capture_city(output, chrome):
    """Collect under one OS lock; reuse completed pairs and retry only transient failures."""
    import fcntl
    from datetime import datetime,timezone
    from scripts.capture_traffic import write_json,CaptureBlockedError
    plan=load_city_job(output)
    with (output/'.capture.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another city collector is already running') from exc
        completed=0
        for tile in plan['tiles']:
            print(f"START {completed+1}/{len(plan['tiles'])} {tile['id']}",flush=True)
            try:
                _capture_tile(output,tile,plan,chrome)
            except (Exception,KeyboardInterrupt) as exc:
                status='interrupted' if isinstance(exc,KeyboardInterrupt) else 'blocked' if isinstance(exc,CaptureBlockedError) else 'failed'
                write_json(output/'collection-status.json',{'status':status,'completed_count':completed,
                    'planned_count':len(plan['tiles']),'failed_tile':tile['id'],'error_type':type(exc).__name__,
                    'error':str(exc),'at':datetime.now(timezone.utc).isoformat()})
                raise
            completed+=1
            write_json(output/'collection-status.json',{'status':'complete' if completed==len(plan['tiles']) else 'running',
                'completed_count':completed,'planned_count':len(plan['tiles']),'last_tile':tile['id'],
                'at':datetime.now(timezone.utc).isoformat()})
            print(f"COMPLETE {completed}/{len(plan['tiles'])} {tile['id']}",flush=True)


CITY_REVIEW_HTML = r'''<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Покрытие Петропавловска-Камчатского снимками</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>body{margin:0;font:16px system-ui;color:#182c35}header{padding:14px 22px;background:#f2f5f6}h1{font-size:21px;margin:0 0 8px}p{margin:6px 0}#map{height:76vh}.leaflet-popup-content a{display:block}</style>
<header><h1>Петропавловск-Камчатский · охват снимками</h1><p id="coverage"></p><p id="traffic"></p><p id="time"></p>
<p>Синие рамки — области снимков. Серые дороги — нет достоверного наблюдения пробок. Зелёный, жёлтый и красный — классы с изображения, не измеренные скорости. Охват территории не означает наличие дорожных данных везде.</p>
<p>Граница OSM условная, по примечанию источника совпадает с городским округом. Клик по рамке открывает исходные снимки и локальное сопоставление.</p></header><div id="map"></div>
<script id="data" type="application/json">__DATA__</script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const data=JSON.parse(document.getElementById('data').textContent),r=data.report;
const pct=x=>(100*x).toFixed(2)+'%';
document.getElementById('coverage').textContent='Принято кадров: '+r.valid_capture_count+' / '+r.planned_capture_count+' · Территория в кадрах: '+pct(r.boundary_coverage_fraction)+' · Дороги полностью в кадрах: '+r.fully_captured_road_count+' / '+r.road_count+' · Длина дорог: '+pct(r.road_length_coverage_fraction);
document.getElementById('traffic').textContent='Наблюдаемый класс дороги: '+r.observed_road_count+' · Без надёжного наблюдения: '+r.unknown_road_count+' · Конфликт между кадрами: '+r.conflicting_road_count+' · Подтверждённая детализация снимков: '+pct(r.detail_verified_boundary_fraction);
const date=x=>new Date(x).toLocaleString('ru-RU',{timeZone:'Asia/Vladivostok'});
document.getElementById('time').textContent=r.capture_time_range.length?'Последовательный сбор: '+r.capture_time_range.map(date).join(' — ')+' (Владивосток) · Кадры не одновременны.':'Снимков пока нет.';
const map=L.map('map',{preferCanvas:true});
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(map);
map.createPane('footprints');map.getPane('footprints').style.zIndex=360;
const city=L.geoJSON(data.boundary,{style:{color:'#183c52',weight:3,fill:false}}).addTo(map);
const frames=L.geoJSON(data.footprints,{pane:'footprints',style:{color:'#147abd',weight:1,fillOpacity:.025},onEachFeature:(f,l)=>{const p=f.properties,b=document.createElement('div');b.textContent=p.tile_id+' · '+date(p.captured_at);for(const [file,label] of [['traffic-on.png','Снимок с пробками'],['traffic-off.png','Снимок без пробок'],['review.html','Сопоставление этого кадра']]){const a=document.createElement('a');a.textContent=label;a.href='captures/'+encodeURIComponent(p.tile_id)+'/'+file;a.target='_blank';b.appendChild(a);}l.bindPopup(b);}}).addTo(map);
const colors={free:'#26863c',slow:'#d29b00',congested:'#d53939',unknown:'#858b90'};
const reasonNames={consistent_screenshot_observations:'Оценка по цветным линиям снимка',conflicting_capture_classes:'Кадры дают разные классы, требуется проверка',no_reliable_screenshot_observation:'Достоверной оценки по снимкам нет'};
const names={free:'Свободно',slow:'Замедление',congested:'Сильное замедление',unknown:'Нет надёжного наблюдения пробок'};
function roads(known){return L.geoJSON(data.roads,{filter:f=>(f.properties.traffic_status!=='unknown')===known,style:f=>({color:colors[f.properties.traffic_status],weight:known?4:2,opacity:known?.95:.7}),onEachFeature:(f,l)=>{const p=f.properties,b=document.createElement('div');b.textContent=p.name+' · '+names[p.traffic_status]+' · В кадрах '+pct(p.captured_fraction)+' · '+reasonNames[p.reason];for(const id of p.capture_ids){const a=document.createElement('a');a.textContent='Кадр '+id;a.href='captures/'+encodeURIComponent(id)+'/review.html';a.target='_blank';b.appendChild(a);}l.bindPopup(b);}}).addTo(map);}
const observed=roads(true),unknown=roads(false);
const gaps=L.geoJSON(data.uncovered_boundary,{style:{color:'#ce36a1',fillOpacity:.45}}).addTo(map);
const details=L.geoJSON(data.detail_gaps,{style:{color:'#cf8c15',fillOpacity:.18},onEachFeature:(f,l)=>l.bindPopup('Малодетальная область: снимок сделан, наличие деталей не подтверждено. Это не свидетельство свободной дороги.')});
L.control.layers({}, {'Граница города OSM':city,'Рамки принятых снимков':frames,'Дороги с наблюдаемым классом':observed,'Нет наблюдения пробок':unknown,'Неснятая территория':gaps,'Малодетальные области снимков':details},{collapsed:false}).addTo(map);
map.fitBounds(city.getBounds(),{padding:[24,24]});
</script></html>'''


def analyze_city(output, require_full=False):
    """Recheck every PNG, analyze observations, and publish actual coverage and gaps."""
    import gzip
    import hashlib
    import json
    from collections import Counter
    from datetime import datetime,timezone
    from pathlib import Path
    from scripts.capture_traffic import analyze,verified_capture,write_json,write_text_atomic,digest,ROOT
    plan=load_city_job(output)
    boundary=json.loads((output/'boundary.geojson').read_text())
    roads=json.loads(gzip.decompress((output/'roads.json.gz').read_bytes()))['segments']
    algorithm_sha=digest(ROOT/'monitoring/traffic_image.py')
    captures=[];missing=[];invalid=[];evidence={r['id']:[] for r in roads};manifest=[]
    for tile in plan['tiles']:
        folder=output/'captures'/tile['id']
        if not (folder/'capture.json').exists():
            missing.append(tile['id']);continue
        try:
            capture=verify_planned_capture(folder,tile,plan)
            capture['tile_id']=tile['id'];captures.append(capture)
        except (ValueError,KeyError,OSError) as exc:
            invalid.append({'tile_id':tile['id'],'error':str(exc)});continue
        result=None
        if (folder/'roads.geojson').exists():
            try:
                cached=json.loads((folder/'roads.geojson').read_text())
                if (not isinstance(cached,dict) or cached.get('type')!='FeatureCollection'
                        or not isinstance(cached.get('features'),list) or not isinstance(cached.get('metadata'),dict)):
                    raise ValueError('Invalid analysis cache structure')
            except (ValueError,OSError) as exc:
                print('INVALID_CACHE',tile['id'],type(exc).__name__,flush=True)
                cached={}
            meta=cached.get('metadata',{})
            if (meta.get('algorithm_sha256')==algorithm_sha and meta.get('roads_sha256')==plan['roads_sha256']
                    and meta.get('image_sha256')==capture['image_sha256'] and meta.get('capture_sha256')==digest(folder/'capture.json')
                    and meta.get('analysis_pipeline_sha256')==digest(ROOT/'scripts/capture_traffic.py')
                    and isinstance(meta.get('artifact_sha256'),dict)
                    and 'review.html' in meta.get('artifact_sha256',{})
                    and all(Path(name).name==name and (folder/name).is_file() and digest(folder/name)==expected
                            for name,expected in meta.get('artifact_sha256',{}).items())):
                result=cached
        if result is None:
            print('ANALYZE',tile['id'],flush=True)
            analyze(folder,output/'roads.json.gz',include_model=False)
            result=json.loads((folder/'roads.geojson').read_text())
        for feature in result['features']:
            prop=feature['properties']
            if prop['traffic_status'] in ('free','slow','congested'):
                evidence[feature['id']].append({'tile_id':tile['id'],'captured_at':capture['captured_at'],
                    'traffic_status':prop['traffic_status'],'coverage_fraction':prop['coverage_fraction']})
        manifest.append({'tile_id':tile['id'],'captured_at':capture['captured_at'],
            'image_sha256':capture['image_sha256'],'capture_quality':capture['capture_quality'],
            'calibration_holdout_error_px':result['metadata']['calibration_holdout_error_px']})
    report=audit_geometry(boundary,roads,captures)
    coverage={p['road_id']:p for p in report['road_coverage']}
    footprints=[capture_footprint(c) for c in captures]
    features=[];conflicting=0
    for road in roads:
        classes={p['traffic_status'] for p in evidence[road['id']]}
        status=next(iter(classes)) if len(classes)==1 else 'unknown'
        if len(classes)>1:
            conflicting+=1
        reason='consistent_screenshot_observations' if len(classes)==1 else 'conflicting_capture_classes' if classes else 'no_reliable_screenshot_observation'
        line=LineString(road['coordinates'])
        capture_ids=[c['tile_id'] for c,p in zip(captures,footprints) if p.intersects(line)]
        features.append({'type':'Feature','id':road['id'],'geometry':{'type':'LineString','coordinates':road['coordinates']},
            'properties':{'name':road.get('name',road['id']),'osm_way':road.get('osm_way'),
                'traffic_status':status,'reason':reason,'captured_fraction':coverage[road['id']]['captured_fraction'],
                'capture_ids':capture_ids,'observations':evidence[road['id']],'direction':'unresolved',
                'source':'2gis_screenshot','validation':'experimental_unvalidated','not_for_automatic_dispatch':True}})
    classes=Counter(f['properties']['traffic_status'] for f in features)
    times=sorted(c['captured_at'] for c in captures)
    report.update(method='full_city_calibrated_capture_audit_v1',audit_time_utc=datetime.now(timezone.utc).isoformat(),
        planned_capture_count=len(plan['tiles']),valid_capture_count=len(captures),missing_capture_ids=missing,
        invalid_captures=invalid,capture_time_range=[times[0],times[-1]] if times else [],
        observed_road_count=len(features)-classes['unknown'],unknown_road_count=classes['unknown'],
        conflicting_road_count=conflicting,road_classes=dict(classes),capture_manifest=manifest,
        boundary_sha256=plan['boundary_sha256'],roads_sha256=plan['roads_sha256'],plan_sha256=digest(output/'plan.json'),
        road_ids_sha256=hashlib.sha256(json.dumps(sorted(evidence),separators=(',',':')).encode()).hexdigest(),
        algorithm_sha256=algorithm_sha,coverage_code_sha256=digest(Path(__file__)),
        boundary_reference=boundary.get('properties',{}),
        coverage_scope='Capture footprints, not traffic availability or physical completeness of OpenStreetMap roads')
    report['full_capture_passed']=bool(not missing and not invalid and report['boundary_fully_captured']
                                      and report['fully_captured_road_count']==len(roads))
    city_roads={'type':'FeatureCollection','features':features}
    write_json(output/'city-roads.geojson',city_roads)
    for field,file in (('footprints','capture-footprints.geojson'),('uncovered_boundary','uncovered-city.geojson'),('detail_gaps','detail-gaps.geojson')):
        write_json(output/file,report[field])
    data={'report':{k:v for k,v in report.items() if k not in ('road_coverage','capture_manifest','footprints','uncovered_boundary','detail_gaps')},
          'boundary':boundary,'roads':city_roads,'footprints':report['footprints'],
          'uncovered_boundary':report['uncovered_boundary'],'detail_gaps':report['detail_gaps']}
    encoded=json.dumps(data,ensure_ascii=False).replace('<','\\u003c')
    write_text_atomic(output/'review.html',CITY_REVIEW_HTML.replace('__DATA__',encoded))
    write_json(output/'coverage-report.json',report)
    print(json.dumps({k:report[k] for k in ('valid_capture_count','planned_capture_count','boundary_coverage_fraction',
        'fully_captured_road_count','road_count','road_length_coverage_fraction','observed_road_count','unknown_road_count','full_capture_passed')},ensure_ascii=False),flush=True)
    if require_full and not report['full_capture_passed']:
        raise ValueError('Full-city capture verification failed; see coverage-report.json for missing territory/roads')
    return report
