"""Export graph_v3 or analyze a saved capture without touching graph bytes."""
import argparse
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from monitoring.traffic_graph import export_graph, assign, digest


def main():
    p=argparse.ArgumentParser()
    p.add_argument('graph',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--capture',type=Path);p.add_argument('--now');p.add_argument('--max-age-s',type=float,default=900)
    a=p.parse_args(); raw=a.graph.read_bytes();export=export_graph(raw)
    a.output.mkdir(parents=True,exist_ok=True)
    (a.output/'segments.json').write_text(json.dumps(export,ensure_ascii=False))
    if not a.capture:return
    from PIL import Image
    from monitoring.traffic_image import calibration, project, extract_traffic_lines, unproject
    c=json.loads((a.capture/'capture.json').read_text());origin,transform,error=calibration(c)
    off=(a.capture/'traffic-off.png').read_bytes();on=(a.capture/'traffic-on.png').read_bytes()
    for name,data in [('traffic-off.png',off),('traffic-on.png',on)]:
        if digest(data)!=c['image_sha256'][name]:raise ValueError('Source hash mismatch')
    before=Image.open(a.capture/'traffic-off.png');after=Image.open(a.capture/'traffic-on.png')
    if before.size!=after.size or tuple(c['viewport'])!=before.size:raise ValueError('Viewport mismatch')
    region=c['analysis_region'];x,y,w,h=region
    if not (0<=x<x+w<=before.width and 0<=y<y+h<=before.height):raise ValueError('Invalid region')
    # Restrict work to corridors whose projected bounding boxes intersect capture.
    pairs=[]
    for road in export['segments']:
        points=[project(point,origin,transform).tolist() for point in road['coordinates']]
        if max(q[0] for q in points)>=x and min(q[0] for q in points)<=x+w and max(q[1] for q in points)>=y and min(q[1] for q in points)<=y+h:pairs.append((road,points))
    visible=dict(export,segments=[q[0] for q in pairs]);lines=extract_traffic_lines(before,after,region)
    out=assign(visible,lines,[q[1] for q in pairs],region,graph_sha256=export['graph_sha256'],source_sha256=digest(off+on+(a.capture/'capture.json').read_bytes()),captured_at=c['captured_at'],now=a.now or datetime.now(timezone.utc).isoformat(),max_age_s=a.max_age_s,provider_updated_at=c.get('provider_updated_at'))
    out['analyzer_sha256']=digest(b''.join((Path(__file__).resolve().parents[1]/name).read_bytes() for name in ['monitoring/traffic_graph.py','monitoring/traffic_image.py','monitoring/traffic_raster.py']))
    out.update(source_lines=lines,segments=visible['segments'],projected=[q[1] for q in pairs],region=region,calibration_holdout_error_px=error)
    (a.output/'observations.json').write_text(json.dumps(out,ensure_ascii=False))
    (a.output/'reference.png').write_bytes(off)
    from monitoring.traffic_raster import ribbon_overlay
    overlay=ribbon_overlay(before,after,region)
    overlay.save(a.output/'ribbons.png')
    overlay.resize((max(1,overlay.width//4),max(1,overlay.height//4)),Image.Resampling.LANCZOS).save(a.output/'ribbons-overview.png')
    out['raster_bounds']=[unproject([x,y+h],origin,transform),unproject([x+w,y],origin,transform)]
    out['capture_metadata']=c
    (a.output/'observations.json').write_text(json.dumps(out,ensure_ascii=False))
    template=Path(__file__).with_name('graph_traffic_review.html').read_text()
    (a.output/'review.html').write_text(template.replace('__DATA__',json.dumps(out,ensure_ascii=False).replace('<','\\u003c')))
    if a.graph.read_bytes()!=raw:raise RuntimeError('Graph changed during analysis')
if __name__=='__main__':main()
