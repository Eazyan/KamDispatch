"""WGS84 ellipsoidal Mercator and UI-verified local pixel calibration."""
import math
import numpy as np
E = math.sqrt(6.6943799901413165e-3)

def world(point):
    lon,lat=map(float,point)
    if not math.isfinite(lon+lat) or not -180<=lon<=180 or not -85<lat<85:
        raise ValueError('Invalid WGS84 coordinate')
    phi=math.radians(lat);s=math.sin(phi)
    return np.array([math.radians(lon),math.atanh(s)-E*math.atanh(E*s)])

def geographic(point):
    x,y=point;phi=math.atan(math.sinh(y))
    for _ in range(12):phi=math.atan(math.sinh(y+E*math.atanh(E*math.sin(phi))))
    return [math.degrees(x),math.degrees(phi)]

def fit(controls,zoom=None):
    if len(controls)<4:raise ValueError('At least four independent UI controls required')
    geo=np.array([world(p['lon_lat']) for p in controls]);pixels=np.array([p['pixel'] for p in controls],dtype=float)
    origin=geo[0];design=np.column_stack((geo-origin,np.ones(len(geo))))
    if np.linalg.matrix_rank(design[:3])!=3:raise ValueError('Collinear controls')
    transform=np.linalg.solve(design[:3],pixels[:3])
    if zoom is not None:
        # Exact WGS84 world-pixel scale, verified by the wide five-point probe.
        # Keep the UI controls inside the physical desktop window; large virtual
        # viewports can otherwise place context menus outside native screen bounds.
        scale=2**(float(zoom)+8)/(2*math.pi)
        if max(abs(transform[0,0]/scale-1),abs(transform[1,1]/(-scale)-1))>.002:
            raise ValueError('UI scale differs from declared zoom')
        linear=np.array([[scale,0.],[0.,-scale]])
        anchor=np.median(pixels[:3]-(geo[:3]-origin)@linear,axis=0)
        transform=np.vstack((linear,anchor))
    errors=np.linalg.norm(design[3:]@transform-pixels[3:],axis=1)
    if not np.isfinite(transform).all() or max(errors)>1:raise ValueError('Independent UI calibration error exceeds 1px')
    if transform[0,0]<=0 or transform[1,1]>=0 or abs(transform[0,1]/transform[0,0])>.005 or abs(transform[1,0]/transform[1,1])>.005:
        raise ValueError('Map is rotated or tilted')
    return origin,transform,[float(v) for v in errors]

def scale_for_zoom(zoom):
    """CSS pixels per unit of WGS84 ellipsoidal Mercator at the given zoom."""
    return 2**(float(zoom)+8)/(2*math.pi)


def transform_from_anchor(anchor,zoom):
    """Pixel transform when the map center is projected to `anchor`."""
    s=scale_for_zoom(zoom)
    return np.array([[s,0.],[0.,-s],[float(anchor[0]),float(anchor[1])]])


def transform_from_meta(meta):
    """Prefer the measured anchor; keep the five-point fit for older captures."""
    if meta.get('anchor'):
        return world(meta['center']),transform_from_anchor(meta['anchor'],meta['zoom'])
    origin,transform,_=fit(meta['control_points'],meta.get('zoom'))
    return origin,transform


def project(point,origin,transform):return np.append(world(point)-origin,1)@transform

def unproject(pixel,origin,transform):
    return geographic((np.asarray(pixel)-transform[2])@np.linalg.inv(transform[:2])+origin)

def bounds(meta):
    o,t=transform_from_meta(meta);x,y,w,h=meta['analysis_region']
    return [unproject([x,y+h],o,t),unproject([x+w,y],o,t)]

def plan(graph,probe,overlap=.12):
    """Use measured footprint and exact polyline rectangle intersections."""
    o,t=transform_from_meta(probe);x,y,w,h=probe['analysis_region']
    lo=world(unproject([x,y+h],o,t));hi=world(unproject([x+w,y],o,t));extent=hi-lo
    boxes=[]
    for road in graph['segments']:
        points=np.array([world(p) for p in road['coordinates']]);boxes.append((*points.min(axis=0),*points.max(axis=0)))
    minimum=np.array([min(b[0] for b in boxes),min(b[1] for b in boxes)])
    maximum=np.array([max(b[2] for b in boxes),max(b[3] for b in boxes)])
    step=extent*(1-overlap);count=np.maximum(1,np.ceil((maximum-minimum-extent+extent*.02)/step).astype(int)+1)
    tiles=[]
    for row in range(count[1]):
        for col in range(count[0]):
            lower=minimum+[col*step[0],row*step[1]]-extent*.01;upper=lower+extent
            if not any(a<=upper[0] and c>=lower[0] and b<=upper[1] and d>=lower[1] for a,b,c,d in boxes):continue
            tiles.append({'id':f'tile-{row:02}-{col:02}','center':geographic(lower+world(probe['center'])-lo)})
    if not 1<=len(tiles)<=64:raise ValueError('City plan exceeds finite 64-frame budget')
    return dict(graph_sha256=graph['graph_sha256'],zoom=probe['zoom'],viewport=probe['viewport'],tiles=tiles,overlap=overlap)

def footprint_coverage(graph,frames):
    boxes=[]
    for meta in frames:
        lo,hi=bounds(meta);boxes.append([*world(lo),*world(hi)])
    missing=[]
    for road in graph['segments']:
        points=[world(p) for p in road['coordinates']]
        for a,b in zip(points,points[1:]):
            spans=[]
            for west,south,east,north in boxes:
                lo,hi=0.,1.
                for axis,minimum,maximum in ((0,west,east),(1,south,north)):
                    d=b[axis]-a[axis]
                    if abs(d)<1e-15:
                        if not minimum<=a[axis]<=maximum:hi=-1
                    else:
                        first,last=sorted(((minimum-a[axis])/d,(maximum-a[axis])/d));lo=max(lo,first);hi=min(hi,last)
                if hi>=lo:spans.append((lo,hi))
            end=0.
            for lo,hi in sorted(spans):
                if lo>end+1e-9:break
                end=max(end,hi)
            if end<1-1e-9:missing.append(road['id']);break
    return {'total_corridors':len(graph['segments']),'fully_in_footprints':len(graph['segments'])-len(missing),'missing_corridor_ids':missing}
