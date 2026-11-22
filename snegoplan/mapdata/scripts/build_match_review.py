# -*- coding: utf-8 -*-
"""build_match_review.py — data/match_review.html: автономная canvas-карта QA (без сети).
Слои: traffic lights / intensity / poi / buildings-unmatched; цвет по статусу;
клик — rationale + source."""
import csv, json, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
G = json.load(open(os.path.abspath(os.path.join(BASE, '..', 'mapprep', 'out', 'graph_v3.json')), encoding='utf-8'))

# базовая карта: именованные рёбра + магистрали (тонкий контекст)
edges = []
for e in G['edges']:
    if not e.get('name') and e['highway'] not in ('primary', 'secondary', 'tertiary', 'trunk'):
        continue
    edges.append([round(c, 5) for p in e['geometry'] for c in p])

layers = {}

# id для OSM-строк
tl = []
for r in csv.DictReader(open(os.path.join(DATA, 'traffic_lights_match.csv'), encoding='utf-8')):
    if not r['lon']:
        continue
    rid = r['ksodd_id'] or r['osm_id']
    tl.append({'id': rid, 'lon': float(r['lon']), 'lat': float(r['lat']),
               'status': r['status'], 'confidence': r['confidence'],
               'label': r['address'] or ('OSM ' + r['osm_id']),
               'rat': r['rationale'][:400], 'src': r['source']})
layers['Светофоры (87)'] = tl

pi = []
for r in csv.DictReader(open(os.path.join(DATA, 'ksodd_point_match.csv'), encoding='utf-8')):
    if not r['lon']:
        continue
    pi.append({'id': r['direction'][:40], 'lon': float(r['lon']), 'lat': float(r['lat']),
               'status': 'matched' if r['match'] == 'yes' else 'unmatched',
               'confidence': r['confidence'],
               'label': r['direction'][:60], 'rat': r['rationale'][:400], 'src': r['source']})
layers['Интенсивность (с точками)'] = pi

pp = []
for r in csv.DictReader(open(os.path.join(DATA, 'poi_critical.csv'), encoding='utf-8')):
    pp.append({'id': r['osm_id'], 'lon': float(r['lon']), 'lat': float(r['lat']),
               'status': r['status'], 'confidence': r['critical_access'],
               'label': r['tag'], 'rat': r['rationale'][:300], 'src': 'attractors.csv'})
layers['Критичные POI'] = pp

bu = []
for r in csv.DictReader(open(os.path.join(DATA, 'buildings_match.csv'), encoding='utf-8')):
    if r['status'] != 'unmatched':
        continue
    bu.append({'id': r['osm_id'], 'lon': float(r['lon']), 'lat': float(r['lat']),
               'status': 'unmatched', 'confidence': '',
               'label': r['addr'][:60], 'rat': r['rationale'][:200], 'src': 'residential.csv'})
layers['Здания unmatched'] = bu

data = {'edges': edges, 'layers': layers}
payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))

html = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>QA привязки КСОДД — match_review</title>
<style>
html,body{margin:0;height:100%;overflow:hidden;font:12px system-ui,Arial,sans-serif}
#c{position:fixed;inset:0;cursor:grab}
#p{position:fixed;top:6px;left:6px;background:#fffffff2;padding:6px 10px;border:1px solid #bbb;border-radius:6px;z-index:2;max-width:260px}
#p label{display:block;margin:2px 0;cursor:pointer}
#tip{position:fixed;display:none;background:#222;color:#fff;padding:6px 8px;border-radius:5px;pointer-events:none;font-size:12px;line-height:1.45;max-width:340px;z-index:3;white-space:pre-wrap}
#leg{font-size:11px;color:#444;margin-top:4px}
</style></head><body>
<canvas id="c"></canvas>
<div id="p"><b>Слои:</b><div id="ls"></div><div id="leg">● matched ● ambiguous ● unmatched<br>клик — rationale+source</div></div>
<div id="tip"></div>
<script>
const D=/*DATA*/null;
const SC={matched:"#1a9850",ambiguous:"#f4a582",unmatched:"#d7191c",yes:"#1a9850",no:"#d7191c",uncertain:"#f4a582"};
const cv=document.getElementById("c"),cx=cv.getContext("2d"),tip=document.getElementById("tip");
const lat0=53.04,kx=Math.cos(lat0*Math.PI/180);
let minx=1e9,maxx=-1e9,miny=1e9,maxy=-1e9;
const E=D.edges.map(a=>{const p=[];for(let i=0;i<a.length;i+=2){const x=a[i]*kx,y=-a[i+1];p.push(x,y);minx=Math.min(minx,x);maxx=Math.max(maxx,x);miny=Math.min(miny,y);maxy=Math.max(maxy,y)}return p});
let P=[];
for(const[name,pts]of Object.entries(D.layers)){for(const q of pts){P.push({...q,layer:name,x:q.lon*kx,y:-q.lat})}}
P.forEach(q=>{minx=Math.min(minx,q.x);maxx=Math.max(maxx,q.x);miny=Math.min(miny,q.y);maxy=Math.max(maxy,q.y)});
let s=1,ox=0,oy=0,dpr=1;
function fit(){s=Math.min(innerWidth/(maxx-minx),innerHeight/(maxy-miny))*0.95;ox=(innerWidth-(maxx-minx)*s)/2-minx*s;oy=(innerHeight-(maxy-miny)*s)/2-miny*s}
function resize(){dpr=devicePixelRatio||1;cv.width=innerWidth*dpr;cv.height=innerHeight*dpr;cv.style.width=innerWidth+"px";cv.style.height=innerHeight+"px"}
const vis={};let visCount=0;
const ls=document.getElementById("ls");
for(const name of Object.keys(D.layers)){const id="L"+visCount++;vis[name]=true;const lb=document.createElement("label");
lb.innerHTML='<input type="checkbox" checked data-n="'+name+'"> '+name+' ('+D.layers[name].length+')';
lb.querySelector("input").onchange=ev=>{vis[ev.target.dataset.n]=ev.target.checked;draw()};ls.appendChild(lb)}
function draw(){cx.setTransform(1,0,0,1,0,0);cx.clearRect(0,0,cv.width,cv.height);cx.setTransform(dpr,0,0,dpr,0,0);cx.lineCap="round";
 cx.strokeStyle="#d8d8d0";cx.lineWidth=1;
 for(const p of E){cx.beginPath();for(let i=0;i<p.length;i+=2){const X=p[i]*s+ox,Y=p[i+1]*s+oy;i?cx.lineTo(X,Y):cx.moveTo(X,Y)}cx.stroke()}
 for(const q of P){if(!vis[q.layer])continue;cx.fillStyle=SC[q.status]||"#666";cx.beginPath();cx.arc(q.x*s+ox,q.y*s+oy,4,0,7);cx.fill();cx.strokeStyle="#fff";cx.lineWidth=1;cx.stroke()}}
let drag=null,moved=0;
cv.onmousedown=e=>{drag=[e.clientX,e.clientY,ox,oy];moved=0};
cv.onmousemove=e=>{if(drag){const dx=e.clientX-drag[0],dy=e.clientY-drag[1];moved+=Math.abs(dx)+Math.abs(dy);ox=drag[2]+dx;oy=drag[3]+dy;draw()}else{hover(e)}};
cv.onmouseup=e=>{if(drag&&moved<4){pick(e)}drag=null};
cv.onwheel=e=>{const f=e.deltaY>0?0.85:1.18;s*=f;draw();e.preventDefault()};
function pick(e){const r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;let best=null;
 for(const q of P){if(!vis[q.layer])continue;const d=Math.hypot(q.x*s+ox-mx,q.y*s+oy-my);if(d<12&&(!best||d<best[0]))best=[d,q]}
 if(best){const q=best[1];tip.style.display="block";tip.style.left=(e.clientX+12)+"px";tip.style.top=(e.clientY+12)+"px";
  tip.textContent="["+q.layer+"] "+q.id+"\\n"+q.label+"\\nстатус: "+q.status+"  conf: "+q.confidence+"\\n"+q.rat+"\\nsource: "+q.src}
 else tip.style.display="none"}
function hover(e){tip.style.display="none"}
window.onresize=()=>{resize();draw()};
resize();fit();draw();
</script></body></html>"""
html = html.replace('/*DATA*/null', payload)
out = os.path.join(DATA, 'match_review.html')
open(out, 'w', encoding='utf-8').write(html)
print('match_review.html: %.2f МБ, точек: %d' % (len(html.encode('utf-8')) / 1e6,
      sum(len(v) for v in layers.values())))
