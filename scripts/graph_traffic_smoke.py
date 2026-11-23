"""Deterministic synthetic capture. Never empirical traffic evidence."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from PIL import Image, ImageDraw
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
def coord(x,y):return [158+x/100000,math.degrees(math.atan(math.sinh(math.asinh(math.tan(math.radians(53)))-y/100000)))]
g=dict(edges=[dict(id='forward',u=1,v=2,length_m=100,geometry=[coord(20,100),coord(280,100)],reverse_edge_id='reverse'),dict(id='reverse',u=2,v=1,length_m=100,geometry=[coord(280,100),coord(20,100)],reverse_edge_id='forward')])
(out/'graph.json').write_text(json.dumps(g));Image.new('RGB',(300,200),'white').save(out/'traffic-off.png');im=Image.new('RGB',(300,200),'white');ImageDraw.Draw(im).line([(70,100),(130,100)],fill=(230,30,30),width=4);im.save(out/'traffic-on.png')
c=dict(viewport=[300,200],analysis_region=[0,0,300,200],captured_at='2026-11-23T10:00:00+12:00',control_points=[dict(pixel=[x,y],lon_lat=coord(x,y)) for x,y in [(10,10),(290,10),(10,190),(290,190)]],image_sha256={n:hashlib.sha256((out/n).read_bytes()).hexdigest() for n in ['traffic-off.png','traffic-on.png']},source='synthetic')
(out/'capture.json').write_text(json.dumps(c))
subprocess.run([sys.executable,str(Path(__file__).with_name('graph_traffic.py')),str(out/'graph.json'),str(out/'review'),'--capture',str(out),'--now','2026-11-23T10:01:00+12:00'],check=True)
