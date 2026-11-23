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
from monitoring.traffic_image import analyze_roads, calibration, project, estimate_traffic, validate_basemap_pair


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
    matched = segments([{**f,"properties":{**f["properties"],"pixels":[project(p,origin,transform).tolist() for p in f["geometry"]["coordinates"]]}}
                        for f in result.get("matched_sections",{}).get("features",[])])
    section_hit = 0.
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
        mh,_ = coverage(clipped,matched[item['class']],12)
        section_hit+=mh
        source_hit+=sh;road_hit+=rh;total+=length
        details.append({'id':item['id'],'class':item['class'],'length_px':round(length,2),
                        'source_recall':round(sh/length,4),'osm_transfer_recall':round(rh/length,4)})
    all_source = [segment for group in extracted.values() for segment in group]
    negatives=[]
    for item in reference['negative']:
        hit,length=coverage(item['pixels'],all_source,3,aligned=False)
        negatives.append({'id':item['id'],'covered_fraction':round(hit/length,4) if length else 0})
    return {'road_classes':dict(Counter(f['properties']['traffic_status'] for f in result['features'])),
            'roads_with_matched_sections':len({f['properties']['road_id'] for f in result.get('matched_sections',{}).get('features',[])}),
            'matched_section_length_recall':round(section_hit/total,4) if total else None,
            'source_line_count':sum(len(v) for v in extracted.values()),
            'positive_reference_count':len(details),'positive_reference_length_px':round(total,2),
            'source_length_recall':round(source_hit/total,4) if total else None,
            'osm_transfer_length_recall':round(road_hit/total,4) if total else None,
            'negative_reference_count':len(negatives),
            'negative_false_positive_cases':sum(n['covered_fraction']>=.2 for n in negatives),
            'references':details,'negative_controls':negatives}


def model_validation(roads, result, spatial=False):
    """Leave one OSM way out, including all its partial and full observations."""
    by_id = {road["id"]: road for road in roads}
    def group(road_id):
        road = by_id[road_id]
        return road.get("osm_way") or road_id
    known = {f["id"]: f["properties"]["traffic_status"] for f in result["features"]
             if f["properties"]["traffic_status"] in ("free","slow","congested")}
    rows = []
    if spatial and known:
        centers = {key:np.mean(road["coordinates"],axis=0) for key,road in by_id.items()}
        middle = np.median([centers[key] for key in known],axis=0)
        def block(key):
            return tuple(bool(v) for v in centers[key] >= middle)
        folds = []
        for held_block in sorted({block(key) for key in known}):
            test_keys = {key for key in known if block(key) == held_block}
            hidden_groups = {group(key) for key in by_id if block(key) == held_block}
            hidden = {key for key in by_id if group(key) in hidden_groups}
            folds.append((str(held_block),hidden,test_keys))
    else:
        folds = [(str(held_group),{key for key in by_id if group(key)==held_group},
                  {key for key in known if group(key)==held_group})
                 for held_group in sorted({group(key) for key in known},key=str)]
    for fold, hidden, test_keys in folds:
        masked = {**result, "features":[{**f,"properties":{**f["properties"],
                  "traffic_status":"unknown"}} if f["id"] in hidden else f for f in result["features"]],
                  "matched_sections":{"type":"FeatureCollection", "features":[f for f in
                      result.get("matched_sections",{}).get("features",[]) if f["properties"]["road_id"] not in hidden]}}
        inferred = estimate_traffic(roads,masked)
        predicted = {f["id"]: f["properties"] for f in inferred["features"]}
        prior = inferred.get("metadata",{}).get("prior_scores",{})
        baseline = max(prior,key=prior.get) if prior else "unknown"
        used_sources = set(inferred.get("metadata",{}).get("prior_source_road_ids",[]))
        if hidden & used_sources:
            raise AssertionError("Held-out OSM way leaked into model evidence")
        for key, actual in known.items():
            if key not in test_keys:
                continue
            prop = predicted.get(key,{})
            rows.append({"road_id":key,"osm_way":group(key),"fold":fold,"withheld_class":actual,
                         "estimated_class":prop.get("estimated_status","unknown"),
                         "basis":prop.get("basis","no_training_evidence"),
                         "sample_prior_class":baseline})
    support = Counter(row["withheld_class"] for row in rows)
    recalls = {status:sum(row["withheld_class"]==status and row["estimated_class"]==status for row in rows)/count
               for status,count in support.items()}
    baseline_recalls = {status:sum(row["withheld_class"]==status and row["sample_prior_class"]==status for row in rows)/count
                        for status,count in support.items()}
    count = len(rows)
    return {"method":"leave_one_spatial_quadrant_out" if spatial else "leave_one_osm_way_out","scope":"Agreement with held-out screenshot-derived whole-road classes; not field accuracy or independent manual labels",
            "count":count,"class_support":dict(support),"held_out_evidence_overlap":0,
            "estimated_count":sum(row["estimated_class"]!="unknown" for row in rows),
            "class_agreement":round(sum(row["estimated_class"]==row["withheld_class"] for row in rows)/count,4) if count else None,
            "balanced_class_agreement":round(sum(recalls.values())/len(recalls),4) if recalls else None,
            "prior_baseline_class_agreement":round(sum(row["sample_prior_class"]==row["withheld_class"] for row in rows)/count,4) if count else None,
            "class_recall":recalls,"prior_baseline_balanced_agreement":round(sum(baseline_recalls.values())/len(baseline_recalls),4) if baseline_recalls else None,
            "rows":rows}


def target_coverage(target_ids, result):
    """Count original target IDs; local-only observations and prior-only models do not suffice."""
    targets = set(target_ids)
    observations = {f["id"]: f["properties"] for f in result["features"] if f["id"] in targets}
    observed = {key for key,p in observations.items() if p["traffic_status"] in ("free","slow","congested")}
    model = {f["id"]:f["properties"] for f in result.get("model_estimates",{}).get("features",[]) if f["id"] in targets}
    estimated = {key for key,p in model.items() if p["estimated_status"] in ("free","slow","congested")
                 and p["basis"] != "sample_prior_only"} - observed
    return {"target_count":len(targets),"present_count":len(observations),"whole_road_observations":len(observed),
            "supported_model_estimates":len(estimated),"total_assessed":len(observed|estimated),
            "unassessed_ids":sorted(targets-(observed|estimated)),"missing_ids":sorted(targets-set(observations)),
            "model_basis_counts":dict(Counter(model[key]["basis"] for key in estimated)),
            "model_abstention_counts":dict(Counter(p.get("abstention_reason","unspecified") for p in model.values()
                                                    if p["estimated_status"] == "unknown")),
            "observed_ids":sorted(observed),"supported_model_ids":sorted(estimated),
            "assessed_ids":sorted(observed|estimated)}


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
    parser.add_argument('--minimum-assessed',type=int,help='Require this many assessed roads for every capture with fixed target IDs')
    args=parser.parse_args();captures=mapping(args.capture);baseline=mapping(args.baseline)
    if args.minimum_assessed is not None and args.minimum_assessed<1:
        raise ValueError('Minimum assessed count must be positive')
    target_checks=0
    references=json.loads(args.annotations.read_text());raw=args.roads.read_bytes();roads=json.loads(gzip.decompress(raw) if args.roads.suffix=='.gz' else raw)['segments']
    known={r['id'] for r in references['captures']}
    if not set(captures)<=known or not set(baseline)<=set(captures):
        raise ValueError('Capture or baseline ID is absent from the manual references')
    report={'method':'manual_reference_and_graph_model_audit_v2','scope':'Selected non-exhaustive references and graph model reuse of two saved screenshots; parameter selection used pilot-4, not independent validation or citywide accuracy.',
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
            validate_basemap_pair(before,after,capture["analysis_region"])
            result=analyze_roads(before,after,capture,roads)
        result['metadata'].update(captured_at=capture['captured_at'],image_sha256=capture['image_sha256'],roads_sha256=hashlib.sha256(raw).hexdigest())
        result['model_estimates']=estimate_traffic(roads,result)
        current=evaluate(reference,result,capture)
        current['model_validation']=model_validation(roads,result)
        current['spatial_block_validation']=model_validation(roads,result,spatial=True)
        if reference.get('target_road_ids'):
            current['target_coverage']=target_coverage(reference['target_road_ids'],result)
            target_checks+=1
            check=current['target_coverage']
            if args.minimum_assessed is not None and (check['missing_ids'] or check['total_assessed']<args.minimum_assessed):
                raise ValueError(f"Target coverage failed: {check['total_assessed']}/{check['target_count']}")
        current['model_specification']=result['model_estimates'].get('metadata',{})
        row={'id':key,'role':'previous_holdout_reused_for_development' if key==references['holdout_capture'] else 'development',
             'image_sha256':reference['image_sha256'],'current':current}
        if key in baseline:
            old=evaluate(reference,json.loads(baseline[key].read_text()),capture)
            for field in ('source_length_recall','source_line_count','negative_false_positive_cases','negative_controls'):
                old.pop(field)
            for item in old['references']:
                item.pop('source_recall')
            row['baseline']=old
        report['captures'].append(row)
        print(key,json.dumps({k:v for k,v in current.items() if k not in ('references','negative_controls','model_validation','spatial_block_validation','target_coverage','model_specification')}))
    if args.minimum_assessed is not None and not target_checks:
        raise ValueError('Minimum assessed requires a capture with fixed target IDs')
    report['minimum_assessed_requirement']=args.minimum_assessed
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    main()
