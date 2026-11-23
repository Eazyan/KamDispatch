"""Create a finite measured city plan and benchmark saved-frame analysis."""
import argparse,json
from pathlib import Path
from monitoring.traffic_graph import export_graph
from traffic_service.geo import plan,footprint_coverage,world,geographic
from traffic_service.analysis import analyze
from traffic_service.runtime import atomic_json

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('.'));p.add_argument('--probe',type=Path,required=True);p.add_argument('--analyze',action='store_true');a=p.parse_args()
    graph=export_graph((a.root/'data/graph_v3.json').read_bytes());probe=json.loads((a.probe/'capture.json').read_text())
    result=plan(graph,probe);atomic_json(a.root/'data/plan.json',result);atomic_json(a.root/'data/graph.json',graph)
    print('PLAN',json.dumps(result),flush=True)
    if a.analyze:
        out=analyze(graph,a.probe)
        print('ANALYSIS',json.dumps({k:v for k,v in out.items() if k!='intervals'}),flush=True)
if __name__=='__main__':main()
