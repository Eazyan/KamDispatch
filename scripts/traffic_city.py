"""Finite city capture plan and conservative interval mosaic, no scheduled collection."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from monitoring.traffic_graph import export_graph
from monitoring.traffic_image import mercator, calibration, unproject, project

def plan(graph, probe):
    import numpy as np
    c=json.loads((probe/'capture.json').read_text());origin,t,_=calibration(c)
    x,y,w,h=c['analysis_region'];center=np.asarray(project(c['center'],origin,t))
    # Use measured UI footprint, not a guessed zoom scale. Grid has 10% overlap.
    lo=mercator(unproject([x,y+h],origin,t));hi=mercator(unproject([x+w,y],origin,t))
    points=np.array([mercator(p) for s in graph['segments'] for p in s['coordinates']])
    minimum=points.min(axis=0);maximum=points.max(axis=0);extent=hi-lo;step=extent*.9
    count=np.maximum(1,np.ceil((maximum-minimum-extent+extent*.04)/step).astype(int)+1)
    tiles=[]
    for row in range(int(count[1])):
        for col in range(int(count[0])):
            lower=minimum+np.array([col,row])*step-extent*.02
            # Requested center can be displaced from screenshot center by side panel.
            shift=mercator(c['center'])-lo
            requested=lower+shift
            lon=math.degrees(requested[0]);lat=math.degrees(math.atan(math.sinh(requested[1])))
            tiles.append(dict(id=f'tile-{row:02}-{col:02}',center=[lon,lat],bbox_mercator=[*lower,*(lower+extent)]))
    return dict(graph_sha256=graph['graph_sha256'],zoom=c['zoom'],viewport=c['viewport'],tiles=tiles)

def footprint_coverage(graph, boxes):
    """Verify entire polylines against the union of actual capture rectangles."""
    missing=[]
    for road in graph['segments']:
        points=[mercator(p) for p in road['coordinates']]
        for a,b in zip(points,points[1:]):
            spans=[]
            for west,south,east,north in boxes:
                lo,hi=0.,1.
                for axis,minimum,maximum in [(0,west,east),(1,south,north)]:
                    delta=b[axis]-a[axis]
                    if abs(delta)<1e-15:
                        if not minimum<=a[axis]<=maximum:hi=-1
                    else:
                        first,last=sorted(((minimum-a[axis])/delta,(maximum-a[axis])/delta))
                        lo=max(lo,first);hi=min(hi,last)
                if hi>=lo:spans.append((lo,hi))
            end=0.
            for lo,hi in sorted(spans):
                if lo>end+1e-9:break
                end=max(end,hi)
            if end<1-1e-9:
                missing.append(road['id']);break
    return dict(total_corridors=len(graph['segments']),fully_in_capture_footprints=len(graph['segments'])-len(missing),missing_corridor_ids=missing)

def combine(graph, inputs):
    by={s['id']:[] for s in graph['segments']};captures=[]
    for directory in inputs:
        d=json.loads((directory/'observations.json').read_text());captures.append(dict(path=str(directory),timestamp=d['observations'][0]['timestamp'] if d['observations'] else None))
        for o in d['observations']:
            if o['graph_sha256']!=graph['graph_sha256']:raise ValueError('Graph hash mismatch')
            if o['edge_id']!=o['corridor_id'] or o['reason']=='outside_capture':continue
            by[o['corridor_id']].append(o)
    output=[];lengths={};observed=0
    for s in graph['segments']:
        rows=by[s['id']];bounds=sorted({0.,s['length_m'],*(v for o in rows for v in o['interval_m'])});merged=[]
        # Sweep sorted interval endpoints instead of searching all rows for every bin.
        events={}
        for i,o in enumerate(rows):
            a,b=o['interval_m'];events.setdefault(a,[]).append((i,True));events.setdefault(b,[]).append((i,False))
        active={}
        for a,b in zip(bounds,bounds[1:]):
            for i,add in events.get(a,[]):
                if add:active[i]=rows[i]
                else:active.pop(i,None)
            if b<=a:continue
            o=max(active.values(),key=lambda q:datetime.fromisoformat(q['timestamp'])) if active else None
            status=o['traffic_status'] if o else 'unknown';reason=o['reason'] if o else 'not_captured'
            item=dict(interval_m=[a,b],traffic_status=status,reason=reason,timestamp=o['timestamp'] if o else None,source_sha256=o['source_sha256'] if o else None,source_line_ids=o['source_line_ids'] if o else [],direction='unresolved')
            if merged and all(merged[-1][k]==item[k] for k in ['traffic_status','reason','timestamp','source_sha256']):
                merged[-1]['interval_m'][1]=b;merged[-1]['source_line_ids']=sorted(set(merged[-1]['source_line_ids']+item['source_line_ids']))
            else:merged.append(item)
            lengths[status]=lengths.get(status,0)+b-a
        if any(o['timestamp'] for o in merged):observed+=1
        output.append(dict(s,intervals=merged))
    return dict(graph_sha256=graph['graph_sha256'],segments=output,captures=captures,coverage=dict(total_corridors=len(output),captured_corridors=observed,length_m=lengths),validation='experimental_unvalidated',direction='unresolved',scope='road_corridor',not_for_automatic_dispatch=True,created_at=datetime.now(timezone.utc).isoformat())

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['plan','combine']);p.add_argument('graph',type=Path);p.add_argument('output',type=Path);p.add_argument('inputs',nargs='+',type=Path);a=p.parse_args();graph=export_graph(a.graph.read_bytes())
    if a.command=='plan':result=plan(graph,a.inputs[0])
    else:result=combine(graph,a.inputs)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,ensure_ascii=False))
    if a.command=='combine':a.output.with_suffix('.html').write_text(Path(__file__).with_name('traffic_city_review.html').read_text().replace('__DATA__',json.dumps(result,ensure_ascii=False).replace('<','\\u003c')))
    print(json.dumps(result.get('coverage',{'tiles':len(result['tiles'])}) if a.command=='plan' else result['coverage']))
if __name__=='__main__':main()
