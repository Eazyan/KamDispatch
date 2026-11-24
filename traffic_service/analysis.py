"""Yandex display palette extraction, conservative graph association, and rasters."""
from pathlib import Path
import json,math,time
import numpy as np
from PIL import Image
import cv2
from traffic_service.geo import transform_from_meta,project,bounds
from scripts.capture_traffic import digest,write_json
from monitoring.traffic_image import extract_traffic_lines
from monitoring.traffic_graph import assign

def color_mask(before,after):
    """Yandex display palette, measured 23.11.2026 on a real off/on pair.

    Observed ribbon colours: free (55,203,82)/(94,205,76), slow (255,187,51).
    Casing and anti-aliased blends are left out; no colour means no observation.
    """
    old=np.asarray(before.convert('RGB'),dtype=np.int16);new=np.asarray(after.convert('RGB'),dtype=np.uint8)
    changed=np.max(np.abs(new.astype(np.int16)-old),axis=2)>=25
    hsv=cv2.cvtColor(new,cv2.COLOR_RGB2HSV);h,s,v=hsv.transpose(2,0,1)
    labels=np.zeros(changed.shape,dtype=np.uint8)
    labels[changed&(h>=36)&(h<=90)&(s>=100)&(v>=130)]=1
    labels[changed&(h>=10)&(h<36)&(s>=130)&(v>=160)]=2
    labels[changed&((h<10)|(h>=170))&(s>=120)&(v>=90)]=3
    return labels

def analyze(graph,directory):
    directory=Path(directory);start=time.monotonic();meta=json.loads((directory/'capture.json').read_text())
    for name,expected in meta['image_sha256'].items():
        if digest(directory/name)!=expected:raise ValueError('Screenshot hash mismatch')
    origin,transform=transform_from_meta(meta);errors=meta.get('calibration_holdout_error_px') or [meta.get('anchor_residual_px',0.)];region=meta['analysis_region'];x,y,w,h=region
    with Image.open(directory/'traffic-off.png') as before,Image.open(directory/'traffic-on.png') as after:
        if before.size!=after.size or list(before.size)!=meta['viewport']:raise ValueError('Image dimensions changed')
        labels=color_mask(before,after)
        mask=np.zeros(labels.shape,dtype=np.uint8);mask[y:y+h,x:x+w]=labels[y:y+h,x:x+w]
        rgba=np.zeros((*labels.shape,4),dtype=np.uint8);rgba[:,:,:3]=np.asarray(after.convert('RGB'));rgba[:,:,3]=(mask>0)*255
        Image.fromarray(rgba).crop((x,y,x+w,y+h)).save(directory/'ribbons.png')
        lines=extract_traffic_lines(before,after,region,color_mask_fn=color_mask)
    pairs=[]
    for road in graph['segments']:
        points=np.array([project(p,origin,transform) for p in road['coordinates']])
        if points[:,0].max()>=x and points[:,0].min()<=x+w and points[:,1].max()>=y and points[:,1].min()<=y+h:pairs.append((road,points))
    visible=dict(graph,segments=[r for r,_ in pairs])
    result=assign(visible,lines,[p for _,p in pairs],region,graph_sha256=graph['graph_sha256'],
                  source_sha256=digest(directory/'capture.json'),captured_at=meta['captured_at'],now=meta['captured_at'],max_age_s=600,bin_px=3)
    by={}
    for row in result['observations']:
        if row['edge_id']!=row['corridor_id'] or row['reason']=='outside_capture':continue
        items=by.setdefault(row['corridor_id'],[])
        item={'interval_m':row['interval_m'],'status':row['traffic_status'],'reason':row['reason'],'timestamp':meta['captured_at']}
        if items and all(items[-1][k]==item[k] for k in ('status','reason','timestamp')):items[-1]['interval_m'][1]=item['interval_m'][1]
        else:items.append(item)
    out={'source':'yandex_maps_ui','graph_sha256':graph['graph_sha256'],'source_sha256':digest(directory/'capture.json'),
         'analyzer_sha256':digest(Path(__file__)),'captured_at':meta['captured_at'],'bounds':bounds(meta),'calibration_errors_px':errors,
         'intervals':by,'color_pixels':{str(i):int(np.sum(mask==i)) for i in (1,2,3)},'source_lines':len(lines),
         'duration_s':time.monotonic()-start,'validation':'experimental_ui_assignment','direction':'unresolved'}
    write_json(directory/'analysis.json',out);return out

def merge(graph,analyses):
    by={s['id']:[] for s in graph['segments']}
    for data in analyses:
        if data['graph_sha256']!=graph['graph_sha256']:raise ValueError('Graph changed')
        for key,items in data['intervals'].items():by[key].extend(items)
    segments={};lengths={'free':0.,'slow':0.,'congested':0.,'unknown':0.}
    for road in graph['segments']:
        rows=by[road['id']];events={};length=road['length_m'];points={0.,length}
        for i,item in enumerate(rows):
            a,b=item['interval_m'];points.update([a,b]);events.setdefault(a,[]).append((i,True));events.setdefault(b,[]).append((i,False))
        active={};out=[];cuts=sorted(points)
        for a,b in zip(cuts,cuts[1:]):
            for i,add in events.get(a,[]):
                if add:active[i]=rows[i]
                else:active.pop(i,None)
            if b<=a:continue
            # Freshest observation first, then measured class over unknown at same time.
            choices=sorted(active.values(),key=lambda q:(q['timestamp'],q['status']!='unknown'),reverse=True)
            row=choices[0] if choices else {'status':'unknown','reason':'outside_capture','timestamp':None}
            item={'interval_m':[a,b],**{k:row[k] for k in ('status','reason','timestamp')}}
            if out and all(out[-1][k]==item[k] for k in ('status','reason','timestamp')):out[-1]['interval_m'][1]=b
            else:out.append(item)
            lengths[item['status']]+=b-a
        # Geometry is served once separately. No fake whole-road colour aggregation.
        segments[road['id']]=out
    return {'segments':segments,'length_m':lengths}
