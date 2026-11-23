"""Run exactly the finite tiles in a saved plan. Stop on first failure."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.capture_traffic import capture
from monitoring.traffic_graph import digest, export_graph
from scripts.traffic_city import footprint_coverage
from monitoring.traffic_image import calibration, unproject, mercator

def main():
    p=argparse.ArgumentParser();p.add_argument('graph',type=Path);p.add_argument('plan',type=Path);p.add_argument('output',type=Path);p.add_argument('--chrome',required=True);a=p.parse_args();plan=json.loads(a.plan.read_text())
    if digest(a.graph.read_bytes())!=plan['graph_sha256']:raise ValueError('Graph changed')
    if not 1<=len(plan['tiles'])<=64:raise ValueError('Expected bounded 1–64 tile plan')
    a.output.mkdir(parents=True,exist_ok=True);reviews=[];boxes=[]
    for tile in plan['tiles']:
        directory=a.output/tile['id'];review=a.output/(tile['id']+'-review');print('START',tile['id'],flush=True)
        if not (directory/'capture.json').exists():
            if directory.exists():
                archive=a.output/'interrupted';archive.mkdir(exist_ok=True)
                directory.rename(archive/(tile['id']+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')))
            capture(directory,a.chrome,tile['center'],plan['zoom'],plan['viewport'])
        metadata=json.loads((directory/'capture.json').read_text())
        origin,transform,error=calibration(metadata);x,y,w,h=metadata['analysis_region']
        boxes.append([*mercator(unproject([x,y+h],origin,transform)),*mercator(unproject([x+w,y],origin,transform))])
        if metadata['center']!=tile['center'] or metadata['zoom']!=plan['zoom'] or metadata['viewport']!=plan['viewport']:raise ValueError('Saved tile differs from plan')
        for name,expected in metadata['image_sha256'].items():
            if digest((directory/name).read_bytes())!=expected:raise ValueError('Saved screenshot hash mismatch')
        repo=Path(__file__).resolve().parents[1]
        codehash=digest(b''.join((repo/name).read_bytes() for name in ['monitoring/traffic_graph.py','monitoring/traffic_image.py','monitoring/traffic_raster.py']))
        cached=None
        if (review/'observations.json').exists():
            try:cached=json.loads((review/'observations.json').read_text())
            except ValueError:pass
        sourcehash=digest((directory/'traffic-off.png').read_bytes()+(directory/'traffic-on.png').read_bytes()+(directory/'capture.json').read_bytes())
        valid=cached and cached.get('analyzer_sha256')==codehash and cached.get('observations') and all(o['source_sha256']==sourcehash and o['graph_sha256']==plan['graph_sha256'] for o in cached['observations']) and (review/'ribbons.png').exists()
        if not valid:
            subprocess.run([sys.executable,str(Path(__file__).with_name('graph_traffic.py')),str(a.graph),str(review),'--capture',str(directory),'--now',metadata['captured_at']],check=True)
        else:print('REUSE ANALYSIS',tile['id'],flush=True)
        reviews.append(review);print('DONE',tile['id'],flush=True)
    coverage=footprint_coverage(export_graph(a.graph.read_bytes()),boxes)
    (a.output/'footprint-coverage.json').write_text(json.dumps(coverage))
    print('FOOTPRINT',coverage['fully_in_capture_footprints'],'/',coverage['total_corridors'],flush=True)
    subprocess.run([sys.executable,str(Path(__file__).with_name('traffic_city.py')),'combine',str(a.graph),str(a.output/'city.json'),*map(str,reviews)],check=True)
if __name__=='__main__':main()
