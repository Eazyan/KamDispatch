#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка версионированного map package v3 (TASK_integrate_map_v3.md).

Объединяет готовые результаты без перезапуска match/classification:
  mapprep/out/graph_v3.json           (только чтение, хэш до/после)
  mapprep/out/zones_v3.json
  mapprep/out/service_classification.csv
  mapprep/out/edge_weights.csv
  mapdata/data/ksodd_street_match.csv (lanes + road_categories)
  mapdata/data/ksodd_point_match.csv  (интенсивности)
  mapdata/data/traffic_lights_match.csv
  mapdata/data/poi_critical.csv

Выходы (все новые, существование проверяется - не перезаписываем чужое):
  mapprep/out/map_package_v3.json
  mapprep/out/edge_weights_v3_integrated.csv
  mapprep/out/map_package_v3_review.csv
  mapprep/out/check_map_v3_integrated.html
  mapprep/out/REPORT_v3_integrated.md

Правила интеграции:
- исходные поля графа не меняются; сервисные поля - с префиксом svc_.
- КСОДД в основной слой только при match=yes И confidence=high, с
  source/confidence/rationale на каждое значение (префикс ksodd_).
  category не придумывается: пустое значение не переносится.
- medium/low/unmatched/uncertain/ambiguous и конфликты high-строк -> review CSV.
- светофоры - отдельным слоем с исходным статусом; циклы/фазы не заполняются.
- ownership=unknown сохраняется; municipal_service из OSM-класса не становится
  подтверждённым владением.

Запуск из корня проекта:
  python mapprep/prep/integrate_map_v3.py
"""
import csv, json, hashlib, os, sys, datetime

csv.field_size_limit(50_000_000)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # snegoplan/
OUT = os.path.join(ROOT, "mapprep", "out")
LOGDIR = os.path.join(ROOT, "logs")

PKG = os.path.join(OUT, "map_package_v3.json")
EW_OUT = os.path.join(OUT, "edge_weights_v3_integrated.csv")
REVIEW_OUT = os.path.join(OUT, "map_package_v3_review.csv")
HTML_OUT = os.path.join(OUT, "check_map_v3_integrated.html")
REPORT_OUT = os.path.join(OUT, "REPORT_v3_integrated.md")

GRAPH = os.path.join(OUT, "graph_v3.json")
ZONES = os.path.join(OUT, "zones_v3.json")
SVC = os.path.join(OUT, "service_classification.csv")
EW = os.path.join(OUT, "edge_weights.csv")
STREET = os.path.join(ROOT, "mapdata", "data", "ksodd_street_match.csv")
POINT = os.path.join(ROOT, "mapdata", "data", "ksodd_point_match.csv")
TL = os.path.join(ROOT, "mapdata", "data", "traffic_lights_match.csv")
POI = os.path.join(ROOT, "mapdata", "data", "poi_critical.csv")

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def load_csv(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

TEMPLATE = r"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Снегоплан — map package v3 (QA)</title>
<style>
html,body{margin:0;height:100%;width:100%;overflow:hidden;font:12px system-ui,Arial,sans-serif}
#wrap{position:fixed;inset:0;background:#f4f4ef}
#c{position:absolute;left:0;top:0;width:100%;height:100%;display:block;cursor:grab}
#p{position:absolute;top:6px;right:6px;background:#fffffff0;padding:5px 8px;border:1px solid #bbb;border-radius:6px;max-width:230px;font-size:11px;z-index:2}
#p.min .body{display:none}#p b{cursor:pointer;user-select:none}#p label{display:block;margin:2px 0;cursor:pointer}
#leg{margin-top:6px;font-size:12px}#leg span{display:inline-block;margin-right:8px;white-space:nowrap}
#leg i{display:inline-block;width:12px;height:12px;vertical-align:middle;margin-right:3px;border:1px solid #888}
#tip{position:absolute;display:none;background:#222;color:#fff;padding:6px 8px;border-radius:5px;pointer-events:none;font-size:12px;line-height:1.45;max-width:320px}
</style></head><body>
<div id="wrap"><canvas id="c"></canvas></div>
<div id="p"><b id="tg">[-] Map package v3</b><div class="body">
<label><input type="radio" name="m" value="zone" checked> Зоны</label>
<label><input type="radio" name="m" value="prio"> Приоритеты P1/P2/P3</label>
<label><input type="radio" name="m" value="stage"> Этап 1 (автобус/крит.доступ)</label>
<label><input type="radio" name="m" value="svc"> Сервисный класс</label>
<label><input type="radio" name="m" value="ksodd"> КСОДД high-confidence</label>
<label><input type="checkbox" id="hull"> Контуры зон</label>
<label><input type="checkbox" id="tlc"> Светофоры</label>
<label><input type="checkbox" id="poic"> Критичные POI</label>
<div id="leg"></div><div style="margin-top:6px;color:#666">Колесо — масштаб, drag — сдвиг</div></div></div>
<div id="tip"></div>
<script>
const D=/*DATA*/null;
const cv=document.getElementById("c"),cx=cv.getContext("2d"),tip=document.getElementById("tip");
let mode="zone",showHull=false,showTl=false,showPoi=false;
const lat0=53.04,kx=Math.cos(lat0*Math.PI/180);
let minx=1e9,maxx=-1e9,miny=1e9,maxy=-1e9;
const E=D.edges.map(a=>{const c=a[12],p=[];for(let i=0;i<c.length;i+=2){const x=c[i]*kx,y=-c[i+1];p.push(x,y);minx=Math.min(minx,x);maxx=Math.max(maxx,x);miny=Math.min(miny,y);maxy=Math.max(maxy,y)}
 return {name:a[0],hw:a[1],lanes:a[2],w:a[3],zone:a[4],pr:a[5],muni:a[6],crit:a[7],st:a[8],rev:a[9],km:a[10],len:a[11],p}});
let s=1,ox=0,oy=0,dpr=1,W0=0,H0=0,touched=false;
function fit(){const W=W0,H=H0;s=Math.min(W/(maxx-minx),H/(maxy-miny))*0.95;ox=(W-(maxx-minx)*s)/2-minx*s;oy=(H-(maxy-miny)*s)/2-miny*s}
function resize(){const wr=document.getElementById("wrap");let w=wr.clientWidth,h=wr.clientHeight;if(!w)w=innerWidth;if(!h)h=innerHeight||700;
 cv.style.width=w+"px";cv.style.height=h+"px";dpr=window.devicePixelRatio||1;cv.width=Math.round(w*dpr);cv.height=Math.round(h*dpr);W0=w;H0=h}
function refit(){resize();if(!touched)fit();draw()}
const PC={1:"#d7191c",2:"#2c7bb6",3:"#999999"},PN={1:"P1 магистрали/автобус/крит.подъезды (10)",2:"P2 именованные местные (3)",3:"P3 дворовые/без имени (1)"};
const SC=["#2166ac","#d73027"],SN=["municipal_service=true (уборка)","nonmunicipal — estimate, не подтверждено"];
const STC=["#d7191c","#fdae61","#dddddd"],STN=["этап 1 (bus/крит.доступ)","municipal, этапы 2+","nonmunicipal (не подтверждено)"];
const KC=["#dddddd","#1a9850","#5aae61","#9970ab"],KN=["без подтверждённых данных КСОДД","lanes (high)","интенсивность (high)","lanes+интенсивность (high)"];
function col(e){return mode=="zone"?D.zc[e.zone-1]:mode=="prio"?PC[e.pr]:mode=="stage"?STC[e.st?0:(e.muni?1:2)]:mode=="svc"?SC[e.muni?0:1]:KC[e.km]}
function lw(e){return (e.pr==1?3.2:e.pr==2?2:1.3)*Math.min(2,Math.max(.6,s/60000))*(mode=="stage"&&e.st?1.3:1)}
function draw(){cx.setTransform(1,0,0,1,0,0);cx.clearRect(0,0,cv.width,cv.height);cx.setTransform(dpr,0,0,dpr,0,0);cx.lineCap="round";cx.lineJoin="round";
 if(mode=="ksodd"){for(const e of E){cx.strokeStyle="#eee";cx.lineWidth=1;path(e);cx.stroke()}
  for(const e of E){if(!e.km)continue;cx.strokeStyle=KC[e.km];cx.lineWidth=4;path(e);cx.stroke()}}
 else{for(const pr of [3,2,1]){for(const e of E){if(e.pr!=pr)continue;cx.strokeStyle=col(e);cx.lineWidth=lw(e);path(e);cx.stroke()}}}
 if(showHull){D.hulls.forEach((h,k)=>{cx.strokeStyle=D.zc[k];cx.lineWidth=2;cx.setLineDash([6,4]);cx.beginPath();
  h.forEach((q,i)=>{const X=q[0]*kx*s+ox,Y=-q[1]*s+oy;i?cx.lineTo(X,Y):cx.moveTo(X,Y)});cx.stroke();cx.setLineDash([]);
  let sx=0,sy=0;h.forEach(q=>{sx+=q[0];sy+=q[1]});cx.fillStyle=D.zc[k];cx.font="bold 16px sans-serif";cx.fillText("Зона "+(k+1),sx/h.length*kx*s+ox-20,-sy/h.length*s+oy)})}
 if(showTl){const TL=["#1a9850","#fee08b","#f46d43","#2c7bb6"],TN=["КСОДД matched+high","КСОДД matched (medium/low)","КСОДД unmatched/ambiguous","OSM-only (без пары в КСОДД)"];
  D.tl.forEach(q=>{const X=q[0]*kx*s+ox,Y=-q[1]*s+oy;cx.fillStyle=TL[q[2]];cx.beginPath();cx.arc(X,Y,q[2]==3?3:4.5,0,7);cx.fill();cx.strokeStyle="#fff";cx.lineWidth=1;cx.stroke()})}
 if(showPoi){D.poi.forEach(q=>{const X=q[0]*kx*s+ox,Y=-q[1]*s+oy;cx.strokeStyle=q[2];cx.lineWidth=2;
  cx.beginPath();cx.moveTo(X-5,Y-5);cx.lineTo(X+5,Y+5);cx.moveTo(X+5,Y-5);cx.lineTo(X-5,Y+5);cx.stroke()})}
 legend()}
function path(e){cx.beginPath();for(let i=0;i<e.p.length;i+=2){const X=e.p[i]*s+ox,Y=e.p[i+1]*s+oy;i?cx.lineTo(X,Y):cx.moveTo(X,Y)}}
function legend(){let h="";
 if(mode=="zone")D.zc.forEach((c,i)=>h+=`<span><i style="background:${c}"></i>Зона ${i+1}</span>`);
 else if(mode=="prio")[1,2,3].forEach(p=>h+=`<span><i style="background:${PC[p]}"></i>${PN[p]}</span>`);
 else if(mode=="stage")STC.forEach((c,i)=>h+=`<span><i style="background:${c}"></i>${STN[i]}</span>`);
 else if(mode=="svc")SC.forEach((c,i)=>h+=`<span><i style="background:${c}"></i>${SN[i]}</span>`);
 else{KC.forEach((c,i)=>h+=`<span><i style="background:${c}"></i>${KN[i]}</span>`);
  h+=`<div style="margin-top:4px;color:#666">сплошное = подтверждено КСОДД high; серое = оценочное/без данных</div>`}
 if(showTl)h+=`<div><span><i style="background:#1a9850"></i>TL high</span><span><i style="background:#fee08b"></i>TL med</span><span><i style="background:#f46d43"></i>TL unmatched</span><span><i style="background:#2c7bb6"></i>TL OSM-only</span></div>`;
 if(showPoi)h+=`<div><span><i style="background:#d73027"></i>больницы/клиники</span><span><i style="background:#5e3c99"></i>школы/детсад</span><span><i style="background:#1a9850"></i>госучреждения</span></div>`;
 document.getElementById("leg").innerHTML=h}
function dseg(px,py,ax,ay,bx,by){const dx=bx-ax,dy=by-ay,l=dx*dx+dy*dy;let t=l?((px-ax)*dx+(py-ay)*dy)/l:0;t=Math.max(0,Math.min(1,t));const qx=ax+t*dx-px,qy=ay+t*dy-py;return qx*qx+qy*qy}
function hit(mx,my){let best=null,bd=64;for(const e of E){for(let i=0;i<e.p.length-2;i+=2){const d=dseg(mx,my,e.p[i]*s+ox,e.p[i+1]*s+oy,e.p[i+2]*s+ox,e.p[i+3]*s+oy);if(d<bd){bd=d;best=e}}}return best}
cv.addEventListener("mousemove",ev=>{if(drag){touched=true;ox+=ev.clientX-lx;oy+=ev.clientY-ly;lx=ev.clientX;ly=ev.clientY;draw();return}
 const e=hit(ev.clientX,ev.clientY);if(!e){tip.style.display="none";return}
 tip.innerHTML=`<b>${e.name||"(без названия)"}</b><br>тип: ${e.hw}; зона: ${e.zone}; длина: ${e.len} м<br>`
  +`lanes: ${e.lanes}, width_dir: ${e.w} м<br>`
  +`P${e.pr} (вес ${e.pr==1?10:e.pr==2?3:1}); municipal_service: ${e.muni?"да":"нет (estimate)"}; crit: ${e.crit?"да":"нет"}; этап 1: ${e.st?"да":"нет"}${e.rev?"; на проверке":""}<br>`
  +`КСОДД: ${e.km?("high-confidence атрибуты (mask "+e.km+"), подтверждено"):"нет подтверждённых данных (оценочное)"}`;
 tip.style.display="block";tip.style.left=Math.min(ev.clientX+14,innerWidth-330)+"px";tip.style.top=(ev.clientY+14)+"px"});
let drag=false,lx,ly;
cv.addEventListener("mousedown",ev=>{drag=true;lx=ev.clientX;ly=ev.clientY;cv.style.cursor="grabbing"});
addEventListener("mouseup",()=>{drag=false;cv.style.cursor="grab"});
cv.addEventListener("wheel",ev=>{ev.preventDefault();touched=true;const f=ev.deltaY<0?1.2:1/1.2;ox=ev.clientX-(ev.clientX-ox)*f;oy=ev.clientY-(ev.clientY-oy)*f;s*=f;draw()},{passive:false});
document.querySelectorAll("input[name=m]").forEach(r=>r.onchange=()=>{mode=r.value;draw()});
document.getElementById("hull").onchange=ev=>{showHull=ev.target.checked;draw()};
document.getElementById("tlc").onchange=ev=>{showTl=ev.target.checked;draw()};
document.getElementById("poic").onchange=ev=>{showPoi=ev.target.checked;draw()};
document.getElementById("tg").onclick=()=>{const p=document.getElementById("p");p.classList.toggle("min");document.getElementById("tg").textContent=(p.classList.contains("min")?"[+]":"[-]")+" Map package v3"};
addEventListener("resize",refit);
refit();setTimeout(refit,100);
</script></body></html>"""

# --- защита от перезаписи чужих результатов ---
for p in (PKG, EW_OUT, REVIEW_OUT, HTML_OUT, REPORT_OUT):
    if os.path.exists(p):
        sys.exit("FAIL: %s уже существует - не перезаписываем чужие результаты" % p)

print("== map package v3: старт", datetime.datetime.now().isoformat(timespec="seconds"))
sha_before = sha256(GRAPH)

g = json.load(open(GRAPH, encoding="utf-8"))
zj = json.load(open(ZONES, encoding="utf-8"))
nodes, edges = g["nodes"], g["edges"]
edge_ids = [e["id"] for e in edges]
edge_set = set(edge_ids)
by_id = {e["id"]: e for e in edges}
print("граф: %d узлов / %d рёбер" % (len(nodes), len(edges)))

svc_rows = load_csv(SVC)
ew_rows = load_csv(EW)
street_rows = load_csv(STREET)
point_rows = load_csv(POINT)
tl_rows = load_csv(TL)
poi_rows = load_csv(POI)
for name, rows in (("service_classification", svc_rows), ("edge_weights", ew_rows)):
    ids = [r["edge_id"] for r in rows]
    assert len(ids) == len(set(ids)) == len(edges) and set(ids) == edge_set, name + ": покрытие рёбер не 1:1"

svc_by = {r["edge_id"]: r for r in svc_rows}
ew_by = {r["edge_id"]: r for r in ew_rows}

review = []          # [{table, entity, status, confidence, reason, source}]
conflicted_keys = set()  # (edge_id, attr), отнесённые в review из-за конфликта high-строк
stats = {"ksodd_main_attrs": 0, "ksodd_main_edges": set(), "review_rows": 0}

def add_review(table, entity, status, confidence, reason, source):
    review.append(dict(table=table, entity=entity, status=status,
                       confidence=confidence, reason=reason, source=source))

# ---------- 1. сервисные поля (как есть, с префиксом svc_) ----------
svc_fields = ["passable", "municipal_service", "required_current_stage",
              "priority_source", "priority_confidence", "critical_access",
              "ownership", "review_flag", "rationale"]
for e in edges:
    r = svc_by[e["id"]]
    e["svc_passable"] = r["passable"] == "True"
    e["svc_municipal_service"] = r["municipal_service"] == "True"
    e["svc_required_current_stage"] = r["required_current_stage"] == "True"
    e["svc_priority"] = int(r["priority"])
    e["svc_priority_weight"] = int(r["priority_weight"])
    e["svc_priority_source"] = r["priority_source"]
    e["svc_priority_confidence"] = float(r["priority_confidence"])
    e["svc_critical_access"] = r["critical_access"] == "True"
    e["svc_ownership"] = r["ownership"]
    e["svc_review_flag"] = r["review_flag"] == "True"
    e["svc_rationale"] = r["rationale"]
    e["priority_diff"] = e["svc_priority"] != e["priority"]
n_diff = sum(1 for e in edges if e["priority_diff"])

# ---------- 2. КСОДД high-confidence в основной слой ----------
# атрибут -> (ключ в values, переносить ли пустые)
STREET_ATTRS = {"lanes": ("lanes", False),
                "max_intensity_veh_per_h": ("max_intensity_veh_per_h", False),
                "max_intensity_per_day": ("max_intensity_per_day", False),
                "load_factor": ("load_factor", False),
                "street_length_km": ("length_km", False),
                "street_category": ("category", False),
                "street_flow": ("flow", False),
                "street_qual": ("qual", False)}
POINT_ATTRS = {"intensity_in_per_day": ("intensity_in_per_day", False),
               "intensity_out_per_day": ("intensity_out_per_day", False),
               "intensity_year": ("year", False),
               "intensity_flow": ("flow", False),
               "intensity_qual": ("qual", False)}

def integrate(rows, table, attrs, entity_of):
    """high-confidence присвоения; конфликты (разные значения) -> review."""
    assigned = {}  # (edge_id, attr) -> (value, row)
    blocked = set()  # (edge_id, attr) с конфликтом: автовыбор запрещён, в review
    n_rows = n_vals = 0
    for r in rows:
        high = r["match"] == "yes" and r["confidence"] == "high"
        eids = json.loads(r["v3_edge_ids"])
        vals = json.loads(r["values"])
        if not high:
            reason = ("не подтверждено: match=%s confidence=%s" % (r["match"], r["confidence"]))
            add_review(table, entity_of(r), r["match"], r["confidence"], reason, r["source"])
            continue
        unknown = [x for x in eids if x not in edge_set]
        if unknown:
            add_review(table, entity_of(r), r["match"], r["confidence"],
                       "edge_id вне graph_v3: %s" % unknown, r["source"])
            continue
        n_rows += 1
        for attr, (vkey, keep_empty) in attrs.items():
            raw = vals.get(vkey, "")
            if raw == "" and not keep_empty:
                continue  # пустое не переносим и не выдумываем (category!)
            val = raw.replace(",", ".") if vkey == "load_factor" else raw
            for eid in eids:
                k = (eid, attr)
                if k in blocked:
                    continue  # конфликт уже отнесён в review, автовыбор запрещён
                if k in assigned:
                    prev_val, prev_row = assigned[k]
                    if prev_val == val:
                        continue  # дубль с тем же значением
                    add_review(table, entity_of(r), "conflict", "high",
                               "конфликт: %s=%s (строка '%s') vs %s (строка '%s'); "
                               "автоматически не выбрано — оба варианта на review"
                               % (attr, val, entity_of(r), prev_val, entity_of(prev_row)),
                               r["source"])
                    # откатываем ранее автовыбранное значение: ребро уходит в review
                    ee = by_id[eid]
                    for kk in ("ksodd_" + attr, "ksodd_" + attr + "_source",
                               "ksodd_" + attr + "_confidence", "ksodd_" + attr + "_rationale"):
                        ee.pop(kk, None)
                    n_vals -= 1
                    assigned.pop(k, None)
                    blocked.add(k)
                    conflicted_keys.add(k)
                    continue
                assigned[k] = (val, r)
                e = by_id[eid]
                e["ksodd_" + attr] = val
                e["ksodd_" + attr + "_source"] = r["source"]
                e["ksodd_" + attr + "_confidence"] = r["confidence"]
                e["ksodd_" + attr + "_rationale"] = r["rationale"]
                n_vals += 1
    return n_rows, n_vals

sr, sv = integrate(street_rows, "ksodd_street_match",
                   STREET_ATTRS, lambda r: r["ksodd_street"])
pr_, pv = integrate(point_rows, "ksodd_point_match",
                    POINT_ATTRS, lambda r: (r["point"] + " / " + r["direction"])[:200])
stats["ksodd_main_attrs"] = sv + pv
KS_ALL = list(STREET_ATTRS) + list(POINT_ATTRS)
stats["ksodd_main_edges"] = {e["id"] for e in edges
                             if any("ksodd_" + a in e for a in KS_ALL)}
print("КСОДД high: street %d строк -> %d значений; point %d строк -> %d значений; рёбер с high: %d"
      % (sr, sv, pr_, pv, len(stats["ksodd_main_edges"])))

# ---------- 3. светофоры отдельным слоем (без выдуманных циклов) ----------
tl_layer = []
for r in tl_rows:
    assert r["cycle_s"].strip() == "" and r["phases"].strip() == "", "циклы/фазы должны быть пусты"
    lon = float(r["lon"]) if r["lon"].strip() else None
    lat = float(r["lat"]) if r["lat"].strip() else None
    tl_layer.append(dict(
        ksodd_id=r["ksodd_id"], address=r["address"], lon=lon, lat=lat,
        osm_id=r["osm_id"], cluster_id=r["cluster_id"], status=r["status"], match=r["match"],
        confidence=r["confidence"], cycle_s=r["cycle_s"], phases=r["phases"],
        note=r["note"], rationale=r["rationale"], source=r["source"], addr_check=r["addr_check"]))
for r in tl_rows:
    if r["match"] != "yes":
        why = ("КСОДД-объект без привязки к графу/OSM" if r["source"].startswith("ksodd")
               else "OSM-светофор без пары в КСОДД")
        if not r["lon"].strip() or not r["lat"].strip():
            why += "; координаты в источнике отсутствуют (не снапаем на ребро)"
        add_review("traffic_lights_match", (r["address"] or r["osm_id"] or r["ksodd_id"]),
                   r["status"], r["confidence"], why, r["source"])

# ---------- 4. POI критичные ----------
poi_layer = []
for r in poi_rows:
    rec = dict(osm_id=r["osm_id"], lon=float(r["lon"]), lat=float(r["lat"]), kind=r["kind"],
               tag=r["tag"], name=r["name"], zone=r["zone"], nearest_edge_id=r["nearest_edge_id"],
               distance_m=r["distance_m"], critical_access=r["critical_access"],
               bus_route_near=r["bus_route_near"], status=r["status"], rationale=r["rationale"])
    poi_layer.append(rec)
    if r["status"] != "matched":
        add_review("poi_critical", r["osm_id"] or (r["tag"] + " " + r["name"]),
                   r["status"], "", "дубль критичного POI (представитель в основном слое)", "attractors.csv")

# ---------- 5. review CSV ----------
stats["review_rows"] = len(review)
with open(REVIEW_OUT, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["table", "entity", "status", "confidence", "reason", "source"])
    w.writeheader()
    w.writerows(review)
from collections import Counter
rev_by_table = Counter((r["table"], r["status"], r["confidence"]) for r in review)
print("review строк: %d" % len(review))

# ---------- 6. map_package_v3.json ----------
pkg = dict(
    meta=dict(
        created=datetime.date.today().isoformat(),
        task="mapprep/TASK_integrate_map_v3.md",
        script="mapprep/prep/integrate_map_v3.py",
        graph_sha256_before=sha_before,
        inputs={os.path.relpath(p, ROOT): sha256(p) for p in
                (GRAPH, ZONES, SVC, EW, STREET, POINT, TL, POI)},
        counts=dict(nodes=len(nodes), edges=len(edges), zones=len(zj["zones"]),
                    traffic_lights=len(tl_layer), poi_critical=len(poi_layer),
                    review_rows=len(review), priority_diff_edges=n_diff,
                    ksodd_main_values=stats["ksodd_main_attrs"],
                    ksodd_main_edges=len(stats["ksodd_main_edges"]),
                    stage1_edges=sum(1 for e in edges if e["svc_required_current_stage"]),
                    stage1_dir_km=round(sum(e["length_m"] for e in edges
                                            if e["svc_required_current_stage"]) / 1000.0, 3)),
        rules=["исходные поля графа не изменены; сервисные поля с префиксом svc_",
               "КСОДД в основной слой только match=yes + confidence=high, с source/confidence/rationale",
               "пустая category/циклы/фазы не восстанавливаются",
               "ownership=unknown сохранён; municipal_service != подтверждённое владение",
               "светофоры отдельным слоем, недоказанные точки не снапятся на ребро",
               "этап 1 (svc_required_current_stage) = municipal_service И (bus_route ИЛИ svc_critical_access);"
               " priority==1 сам по себе основанием этапа не является (mapprep/TASK_align_stage1_map_v3.md)"],
        limitations=["ownership не подтверждён ни для одного ребра (нет реестра/КСОДД-владения)",
                     "lanes/width/maxspeed графа ~88% - estimate (см. REPORT_service.md)",
                     "циклы светофоров в КСОДД отсутствуют - в планировании estimate 75 с из build_v3",
                     "category дорог в road_categories.csv пуста - автоматически не назначается"]),
    nodes=nodes, edges=edges, zones=zj["zones"],
    layers=dict(traffic_lights=tl_layer, poi_critical=poi_layer))
json.dump(pkg, open(PKG, "w", encoding="utf-8"), ensure_ascii=False)
print("package: %.1f МБ" % (os.path.getsize(PKG) / 1e6))

# ---------- 7. edge_weights_v3_integrated.csv ----------
KSODD_ATTRS = list(STREET_ATTRS) + list(POINT_ATTRS)
ew_cols = list(ew_rows[0].keys())
extra = ["svc_passable", "svc_municipal_service", "svc_required_current_stage",
         "svc_priority", "svc_priority_weight", "svc_priority_source",
         "svc_priority_confidence", "svc_critical_access", "svc_ownership",
         "svc_review_flag", "priority_diff"]
ks_cols = []
for a in KSODD_ATTRS:
    ks_cols += ["ksodd_" + a, "ksodd_" + a + "_source", "ksodd_" + a + "_confidence"]
with open(EW_OUT, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=ew_cols + extra + ks_cols)
    w.writeheader()
    for e in edges:
        row = dict(ew_by[e["id"]])
        row.update({k: e[k] for k in extra})
        for k in ks_cols:
            row[k] = e.get(k, "")
        w.writerow(row)
print("edge_weights_integrated: %d строк" % len(edges))

# ---------- 8. QA-карта (автономная, canvas, без сети) ----------
ZC = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#008080", "#9a6324", "#808000", "#f032e6"]
hulls = [z["hull_geojson"]["geometry"]["coordinates"][0] for z in zj["zones"]]
# [name, hw, lanes, width_dir_m, zone, svc_pr, muni, crit, stage1, rev, ksodd_mask, len, geo...]
mask_of = lambda e: (1 if "ksodd_lanes" in e else 0) | (2 if "ksodd_intensity_in_per_day" in e else 0) \
                    | (4 if "ksodd_street_length_km" in e else 0)
emap = []
for e in edges:
    emap.append([e["name"] or "", e["highway"], e["lanes"], e["width_dir_m"], e["zone"],
                 e["svc_priority"], 1 if e["svc_municipal_service"] else 0,
                 1 if e["svc_critical_access"] else 0, 1 if e["svc_required_current_stage"] else 0,
                 1 if e["svc_review_flag"] else 0,
                 mask_of(e), e["length_m"],
                 [round(c, 5) for p in e["geometry"] for c in p]])
TLC = {"matched_high": "#1a9850", "matched_mid": "#fee08b", "tl_other": "#f46d43", "osm_only": "#2c7bb6"}
tl_pts = [[round(t["lon"], 6), round(t["lat"], 6),
           0 if (t["status"] == "matched" and t["confidence"] == "high")
           else 1 if t["status"] == "matched" else 2 if t["source"].startswith("ksodd") else 3,
           t["status"], t["confidence"], t["address"] or t["osm_id"] or t["ksodd_id"], t["source"]]
          for t in tl_layer if t["lon"] is not None]
POIC = {"hospital": "#d73027", "clinic": "#d73027", "school": "#5e3c99", "government": "#1a9850",
        "health": "#d73027", "work": "#1a9850"}
poi_pts = [[round(p["lon"], 6), round(p["lat"], 6), POIC.get(p["critical_access"] or p["kind"], "#666"),
            p["kind"], p["status"], p["critical_access"], p["name"] or p["osm_id"]] for p in poi_layer]
data = dict(edges=emap, hulls=hulls, zc=ZC, tl=tl_pts, poi=poi_pts)
html = TEMPLATE.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
open(HTML_OUT, "w", encoding="utf-8").write(html)
print("QA-карта: %.1f МБ" % (os.path.getsize(HTML_OUT) / 1e6))

# ---------- 9. машинные проверки (acceptance) ----------
checks = []
def check(name, ok, detail=""):
    checks.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (" | " + detail if detail else ""))

check("узлы/рёбра сохранены", len(nodes) == 9620 and len(edges) == 23172,
      "%d / %d" % (len(nodes), len(edges)))
check("edge_id уникальны", len(set(edge_ids)) == len(edge_ids), str(len(edge_ids)))
check("classification покрытие ровно 1 раз", len(svc_rows) == 23172 and len(svc_by) == 23172)
rev_ok = all(e["reverse_edge_id"] is None or e["reverse_edge_id"] in edge_set for e in edges)
check("обратные рёбра сохранены", rev_ok)
check("зоны сохранены", len(zj["zones"]) == 9)
req_impl = all((not e["svc_required_current_stage"]) or e["svc_municipal_service"] for e in edges)
check("required_current_stage => municipal_service", req_impl)
stage1_bad = sum(1 for e in edges
                 if e["svc_required_current_stage"] and
                 not (e["svc_municipal_service"] and (bool(e.get("bus_route")) or e["svc_critical_access"])))
check("этап 1 = municipal И (bus_route ИЛИ crit), только такие", stage1_bad == 0,
      "нарушений: %d" % stage1_bad)
miss_stage1 = sum(1 for e in edges
                  if e["svc_municipal_service"] and (bool(e.get("bus_route")) or e["svc_critical_access"])
                  and not e["svc_required_current_stage"])
check("все municipal bus/crit включены в этап 1", miss_stage1 == 0,
      "пропущено: %d" % miss_stage1)
n_st1 = sum(1 for e in edges if e["svc_required_current_stage"])
km_st1 = sum(e["length_m"] for e in edges if e["svc_required_current_stage"]) / 1000.0
p1_nost1 = sum(1 for e in edges if e["svc_priority"] == 1 and not e["svc_required_current_stage"])
p1_nost1_km = sum(e["length_m"] for e in edges
                  if e["svc_priority"] == 1 and not e["svc_required_current_stage"]) / 1000.0
print("этап 1: %d рёбер / %.1f км направленных; P1 без этапа 1: %d рёбер / %.1f км"
      % (n_st1, km_st1, p1_nost1, p1_nost1_km))
pair_bad = 0
for e in edges:
    rid = e["reverse_edge_id"]
    if not rid:
        continue
    r = by_id[rid]
    for f in ("svc_passable", "svc_municipal_service", "svc_required_current_stage", "svc_priority",
              "svc_priority_weight", "svc_priority_source", "svc_critical_access", "svc_ownership"):
        if e[f] != r[f]:
            pair_bad += 1
            break
check("парные рёбра: согласованные сервисные поля", pair_bad == 0, "расхождений: %d" % pair_bad)
W = {1: 10, 2: 3, 3: 1}
check("веса 1/2/3 = 10/3/1", all(e["svc_priority_weight"] == W[e["svc_priority"]] for e in edges))
check("ownership=unknown у всех", all(e["svc_ownership"] == "unknown" for e in edges))
check("циклы/фазы светофоров пусты", all(t["cycle_s"] == "" and t["phases"] == "" for t in tl_layer))
check("sha256 graph_v3 до=после", sha_before == sha256(GRAPH), sha_before[:16] + "…")
# KSODD: source/confidence на каждом значении основного слоя
miss_src = sum(1 for e in edges for a in KSODD_ATTRS
               if ("ksodd_" + a) in e and not e["ksodd_" + a + "_source"])
check("source/confidence у каждого KSODD-значения", miss_src == 0)
# конфликтующие high-пары не выбраны автоматически, ушли в review
leak = sum(1 for (eid, a) in conflicted_keys if "ksodd_" + a in by_id[eid])
check("конфликтные high-значения не в основном слое", leak == 0,
      "конфликтных пар: %d" % len(conflicted_keys))
# счётчики high/review согласованы с исходными CSV
exp_street_high = sum(1 for r in street_rows if r["match"] == "yes" and r["confidence"] == "high")
exp_point_high = sum(1 for r in point_rows if r["match"] == "yes" and r["confidence"] == "high")
got_street_high = sr; got_point_high = pr_
check("high-счётчики = исходным CSV",
      got_street_high == exp_street_high and got_point_high == exp_point_high,
      "street %d/%d, point %d/%d" % (got_street_high, exp_street_high, got_point_high, exp_point_high))
# review покрывает все medium/low/unmatched
exp_review = sum(1 for r in street_rows + point_rows if not (r["match"] == "yes" and r["confidence"] == "high")) \
    + sum(1 for r in tl_rows if r["match"] != "yes") \
    + sum(1 for r in poi_rows if r["status"] != "matched")
n_conflict = sum(1 for r in review if r["status"] == "conflict")
check("review-счётчики согласованы (строки+конфликты)",
      len(review) >= exp_review and stats["review_rows"] == len(review),
      "review %d, ожидалось >= %d (независимых конфликтов: %d)"
      % (len(review), exp_review, n_conflict))
fails = [c for c in checks if not c[1]]
check("package валидный JSON", json.load(open(PKG, encoding="utf-8")) is not None)

# ---------- 10. REPORT ----------
def row(t, s, c, n):
    return "| %s | %s | %s | %d |" % (t, s, c, n)
rev_tbl = {}
for (t, s, c), n in sorted(rev_by_table.items()):
    rev_tbl.setdefault(t, []).append(row(t, s, c or "—", n))
rep = []
rep.append("# Map package v3 — интегрированный отчёт (%s)\n" % datetime.date.today().isoformat())
rep.append("Задача: `mapprep/TASK_integrate_map_v3.md`. Скрипт: `mapprep/prep/integrate_map_v3.py` "
           "(одна команда из корня проекта, без сети).\n")
rep.append("## Команда воспроизведения\n\n```\npython mapprep/prep/integrate_map_v3.py\n```\n")
rep.append("## Входы (sha256)\n")
for p, h in pkg["meta"]["inputs"].items():
    rep.append("- `%s`: `%s`" % (p, h))
rep.append("\n## Что вошло в основной слой\n")
rep.append("- Все %d узлов и %d рёбер graph_v3 (исходные поля не изменены), 9 зон, "
           "обратные рёбра и one-way (206) сохранены." % (len(nodes), len(edges)))
rep.append("- Сервисная классификация: svc_passable / svc_municipal_service / svc_required_current_stage / "
           "svc_priority (+weight/source/confidence) / svc_critical_access / ownership / review_flag на каждом ребре.")
rep.append("- КСОДД high-confidence (match=yes + high, с source/confidence/rationale на значение): "
           "**%d значений на %d рёбер** (street: %d строк/ %d значений; points: %d строк/ %d значений). "
           "Пустая `category` не переносилась — автоматически приоритет/категория не назначались."
           % (stats["ksodd_main_attrs"], len(stats["ksodd_main_edges"]), sr, sv, pr_, pv))
rep.append("- Веса приоритетов 1/2/3 = 10/3/1; автобусные маршруты и критические подъезды к больницам/клиникам "
           "в P1 сохранены (источник — classification, не КСОДД-средние матчи).")
rep.append("- **Этап 1 (`svc_required_current_stage`)**: %d рёбер / %.1f км (направленные) — municipal_service "
           "И (bus_route ИЛИ critical_access). P1 без допуска в этап 1 (магистрали без автобуса/крит.доступа): "
           "%d рёбер / %.1f км — следующие этапы. Зафиксировано `mapprep/TASK_align_stage1_map_v3.md`."
           % (n_st1, km_st1, p1_nost1, p1_nost1_km))
rep.append("- Расхождение svc_priority vs исходный priority графа: %d рёбер (все — upgrade крит.подъезд/автобус "
           "по classification; поле priority графа сохранено, флаг priority_diff на ребре)." % n_diff)
rep.append("\n## Отдельные слои\n")
rep.append("- Светофоры: %d объектов (61 КСОДД + 26 OSM) с исходным status/match/confidence; "
           "cycle_s/phases пустые, не заполнялись; недоказанные точки на рёбра не снапались." % len(tl_layer))
rep.append("- Критичные POI: %d строк (162 matched представителя + 46 ambiguous-дублей), "
           "critical_access/bus_route_near отдельно от категорий дорог." % len(poi_layer))
rep.append("\n## Review (`map_package_v3_review.csv`) — %d строк\n" % len(review))
rep.append("| Таблица | Статус | Confidence | Строк |")
rep.append("|---|---|---|---|")
for t in sorted(rev_tbl):
    rep.extend(rev_tbl[t])
rep.append("\nВсе medium/low/unmatched/uncertain исходных CSV учтены; независимые конфликты high-строк: %d." % n_conflict)
rep.append("\n## Машинные проверки (acceptance)\n")
rep.append("| Проверка | Результат |")
rep.append("|---|---|")
for name, ok, detail in checks:
    rep.append("| %s | %s%s |" % (name, "PASS" if ok else "FAIL",
                                 (" (" + detail + ")") if detail else ""))
rep.append("\n## Ограничения\n")
for l in pkg["meta"]["limitations"]:
    rep.append("- " + l)
rep.append("\n## Backout\n")
rep.append("- `mapprep/out/graph_v3.json` не изменён (sha256 до = после, см. выше).")
rep.append("- Удаление выходов возвращает состояние «как было»:\n"
           "  `Remove-Item mapprep/out/map_package_v3.json, mapprep/out/edge_weights_v3_integrated.csv,"
           " mapprep/out/map_package_v3_review.csv, mapprep/out/check_map_v3_integrated.html,"
           " mapprep/out/REPORT_v3_integrated.md`")
rep.append("\n## QA-карта\n")
rep.append("`check_map_v3_integrated.html` — автономный canvas-просмотрщик (без сети): режимы "
           "зоны/P1-P3/**Этап 1 (красный = bus/крит.доступ, оранжевый = municipal этапов 2+, серый = nonmunicipal)**/"
           "сервисный класс/КСОДД-enrichment; слои светофоров (цвет = статус матча) и критичных POI; "
           "легенда различает подтверждённое (КСОДД high, сплошное) и оценочное (estimate).")
open(REPORT_OUT, "w", encoding="utf-8").write("\n".join(rep) + "\n")

print("== проверки: %d PASS / %d FAIL" % (len(checks) - len(fails), len(fails)))
if fails:
    sys.exit(1)
print("== готово", datetime.datetime.now().isoformat(timespec="seconds"))
