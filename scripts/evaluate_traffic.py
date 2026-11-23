"""Evaluate fixed, manually reviewed screenshot references without claiming citywide accuracy."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from monitoring.traffic_image import analyze_roads, calibration, project


def samples(points):
    for a, b in zip(points, points[1:]):
        a, b = np.asarray(a,float), np.asarray(b,float)
        length = float(np.linalg.norm(b-a))
        if not length:
            continue
        count = max(1, math.ceil(length/2))
        for i in range(count):
            yield a+(b-a)*(i+.5)/count, (b-a)/length, length/count


def coverage(reference, segments, tolerance, aligned=True):
    hit, total = 0., 0.
    for point, tangent, weight in samples(reference):
        total += weight
        for a, b in segments:
            vector = b-a
            length = float(np.linalg.norm(vector))
            if not length:
                continue
            direction = vector/length
            if aligned and abs(tangent@direction)<math.cos(math.radians(20)):
                continue
            along = np.clip((point-a)@direction,0,length)
            if np.linalg.norm(point-(a+direction*along))<=tolerance:
                hit += weight
                break
    return hit,total


def segments(features, field="pixels"):
    grouped = {s:[] for s in ("free","slow","congested")}
    for feature in features:
        status = feature["properties"]["traffic_status"]
        if status not in grouped:
            continue
        points = feature["properties"][field]
        grouped[status].extend((np.array(a,float),np.array(b,float)) for a,b in zip(points,points[1:]))
    return grouped


def evaluate(reference, result, capture):
    origin, transform, _ = calibration(capture)
    roads = [{**f,"properties":{**f["properties"],"pixels":[project(p,origin,transform).tolist() for p in f["geometry"]["coordinates"]]}} for f in result["features"]]
    osm = segments(roads)
    extracted = segments(result.get("traffic_lines",{}).get("features",[]))
    details, source_hit, road_hit, total = [],0.,0.,0.
    x,y,w,h = capture['analysis_region']
    for item in reference["positive"]:
        points = item['pixels']
        # Only the explicitly analyzed region contributes to the denominator.
        clipped = [p for p,_,_ in samples(points) if x<=p[0]<x+w and y<=p[1]<y+h]
        if len(clipped)<2:
            continue
        sh,length = coverage(clipped,extracted[item['class']],5)
        rh,_ = coverage(clipped,osm[item['class']],12)
        source_hit+=sh;road_hit+=rh;total+=length
        details.append({'id':item['id'],'class':item['class'],'length_px':round(length,2),
                        'source_recall':round(sh/length,4),'osm_transfer_recall':round(rh/length,4)})
    all_source = [segment for group in extracted.values() for segment in group]
    negatives=[]
    for item in reference['negative']:
        hit,length=coverage(item['pixels'],all_source,3,aligned=False)
        negatives.append({'id':item['id'],'covered_fraction':round(hit/length,4) if length else 0})
    return {'road_classes':dict(Counter(f['properties']['traffic_status'] for f in result['features'])),
            'source_line_count':sum(len(v) for v in extracted.values()),
            'positive_reference_count':len(details),'positive_reference_length_px':round(total,2),
            'source_length_recall':round(source_hit/total,4) if total else None,
            'osm_transfer_length_recall':round(road_hit/total,4) if total else None,
            'negative_reference_count':len(negatives),
            'negative_false_positive_cases':sum(n['covered_fraction']>=.2 for n in negatives),
            'references':details,'negative_controls':negatives}


def mapping(values):
    result={}
    for value in values:
        key,separator,path=value.partition('=')
        if not separator or key in result:
            raise ValueError('Use unique ID=PATH mappings')
        result[key]=Path(path)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',action='append',required=True,help='Annotation ID=capture directory')
    parser.add_argument('--baseline',action='append',default=[],help='Optional ID=prior roads.geojson')
    parser.add_argument('--annotations',type=Path,default=ROOT/'tests/fixtures/traffic_annotations.json')
    parser.add_argument('--roads',type=Path,default=ROOT/'monitoring/snow_roads.json.gz')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();captures=mapping(args.capture);baseline=mapping(args.baseline)
    references=json.loads(args.annotations.read_text());raw=args.roads.read_bytes();roads=json.loads(gzip.decompress(raw) if args.roads.suffix=='.gz' else raw)['segments']
    known={r['id'] for r in references['captures']}
    if not set(captures)<=known or not set(baseline)<=set(captures):
        raise ValueError('Capture or baseline ID is absent from the manual references')
    report={'method':'manual_reference_audit_v1','scope':'Selected non-exhaustive references on two saved screenshots; not citywide accuracy or global precision.',
            'annotations_sha256':hashlib.sha256(args.annotations.read_bytes()).hexdigest(),
            'algorithm_sha256':hashlib.sha256((ROOT/'monitoring/traffic_image.py').read_bytes()).hexdigest(),
            'roads_sha256':hashlib.sha256(raw).hexdigest(),'captures':[]}
    for reference in references['captures']:
        key=reference['id']
        if key not in captures:
            continue
        folder=captures[key];capture=json.loads((folder/'capture.json').read_text())
        if set(capture['image_sha256'])!={'traffic-off.png','traffic-on.png'}:
            raise ValueError('Both screenshot hashes are required')
        for name,expected in capture['image_sha256'].items():
            if name not in ('traffic-off.png','traffic-on.png') or hashlib.sha256((folder/name).read_bytes()).hexdigest()!=expected:
                raise ValueError('Capture image hash mismatch')
        if hashlib.sha256((folder/'traffic-on.png').read_bytes()).hexdigest()!=reference['image_sha256']:
            raise ValueError('Manual references belong to another screenshot')
        with Image.open(folder/'traffic-off.png') as before,Image.open(folder/'traffic-on.png') as after:
            result=analyze_roads(before,after,capture,roads)
        current=evaluate(reference,result,capture)
        row={'id':key,'role':'holdout' if key==references['holdout_capture'] else 'development',
             'image_sha256':reference['image_sha256'],'current':current}
        if key in baseline:
            old=evaluate(reference,json.loads(baseline[key].read_text()),capture)
            for field in ('source_length_recall','source_line_count','negative_false_positive_cases','negative_controls'):
                old.pop(field)
            for item in old['references']:
                item.pop('source_recall')
            row['baseline']=old
        report['captures'].append(row)
        print(key,json.dumps({k:v for k,v in current.items() if k not in ('references','negative_controls')}))
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    main()
