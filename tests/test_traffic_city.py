import json
from pathlib import Path
import tempfile
import unittest
from scripts.traffic_city import combine,plan
from monitoring.traffic_graph import export_graph

class CityTests(unittest.TestCase):
 def setUp(self):
  self.graph=export_graph(json.dumps({'edges':[dict(id='a',u=1,v=2,geometry=[[158,53],[158.01,53]],length_m=100)]}).encode())
 def test_all_graph_unknown_without_capture(self):
  out=combine(self.graph,[]);self.assertEqual(out['coverage']['total_corridors'],1);self.assertEqual(out['coverage']['length_m'],{'unknown':100})
 def test_overlap_latest_and_unknown_gaps(self):
  with tempfile.TemporaryDirectory() as tmp:
   dirs=[]
   for i,(interval,status,time) in enumerate([([20,60],'slow','10:00:00'),([40,80],'congested','10:01:00')]):
    p=Path(tmp)/str(i);p.mkdir();dirs.append(p)
    o=dict(corridor_id='a',edge_id='a',interval_m=interval,traffic_status=status,reason='matched',timestamp='2026-11-23T'+time+'+12:00',source_sha256=str(i),source_line_ids=['line'],graph_sha256=self.graph['graph_sha256'])
    (p/'observations.json').write_text(json.dumps(dict(observations=[o])))
   out=combine(self.graph,dirs);self.assertEqual(out['coverage']['length_m'],{'unknown':40,'slow':20,'congested':40});self.assertEqual(sum(out['coverage']['length_m'].values()),100)
   self.assertEqual(out,dict(out))
 def test_hash_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'observations.json').write_text(json.dumps(dict(observations=[dict(timestamp='2026-11-23T10:00:00+12:00',graph_sha256='bad')])))
   with self.assertRaises(ValueError):combine(self.graph,[p])

 def test_plan_covers_every_geometry_vertex(self):
  import math
  from monitoring.traffic_image import mercator
  def coordinate(x,y):return [158+x/100000,math.degrees(math.atan(math.sinh(math.asinh(math.tan(math.radians(53)))-y/100000)))]
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);capture=dict(control_points=[dict(pixel=[x,y],lon_lat=coordinate(x,y)) for x,y in [(10,10),(290,10),(10,190),(290,190)]],analysis_region=[0,0,300,200],center=coordinate(150,100),zoom=14,viewport=[300,200])
   (p/'capture.json').write_text(json.dumps(capture));result=plan(self.graph,p)
   for segment in self.graph['segments']:
    for point in segment['coordinates']:
     x,y=mercator(point);self.assertTrue(any(t['bbox_mercator'][0]<=x<=t['bbox_mercator'][2] and t['bbox_mercator'][1]<=y<=t['bbox_mercator'][3] for t in result['tiles']))
 def test_footprint_checks_segment_gaps_not_only_endpoints(self):
  from scripts.traffic_city import footprint_coverage
  from monitoring.traffic_image import mercator
  a,b=map(mercator,self.graph['segments'][0]['coordinates']);mid=(a[0]+b[0])/2
  boxes=[[a[0]-1,a[1]-1,mid-.00001,a[1]+1],[mid+.00001,a[1]-1,b[0]+1,a[1]+1]]
  self.assertEqual(footprint_coverage(self.graph,boxes)['fully_in_capture_footprints'],0)
  self.assertEqual(footprint_coverage(self.graph,[[a[0]-1,a[1]-1,b[0]+1,a[1]+1]])['fully_in_capture_footprints'],1)
