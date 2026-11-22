# -*- coding: utf-8 -*-
"""qa_context.py — печать окружения точки для ручной QA (без сети)."""
import csv, json, math, os, sys, collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G = json.load(open(os.path.abspath(os.path.join(BASE, '..', 'mapprep', 'out', 'graph_v3.json')), encoding='utf-8'))
KYV = 111320.0
KXV = 111320.0 * math.cos(math.radians(53.04))

def xy(lon, lat):
    return (lon * KXV, lat * KYV)

def seg_dist(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - x1, py - y1), x1, y1
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    qx, qy = x1 + t * dx, y1 + t * dy
    return math.hypot(px - qx, py - qy), qx, qy

def edge_dist(e, lon, lat):
    px, py = xy(lon, lat)
    best = None
    pts = e['geometry']
    for i in range(len(pts) - 1):
        x1, y1 = xy(*pts[i]); x2, y2 = xy(*pts[i + 1])
        d, qx, qy = seg_dist(px, py, x1, y1, x2, y2)
        if best is None or d < best:
            best = d
    return best

def context(lon, lat, label=''):
    print('=== %s (%.6f, %.6f)' % (label, lon, lat))
    # ближайшие именованные рёбра
    cand = []
    for e in G['edges']:
        if not e.get('name'):
            continue
        d = edge_dist(e, lon, lat)
        if d < 120:
            cand.append((d, e['name'], e['highway']))
    cand.sort()
    seen = set()
    for d, nm, hw in cand[:8]:
        key = nm
        tag = '' if key not in seen else ' (dup)'
        seen.add(key)
        print('   edge %4.0fм  %-34s %s%s' % (d, nm, hw, tag))
    # ближайшие узлы
    nd = []
    for n in G['nodes']:
        d = math.hypot(xy(lon, lat)[0] - xy(n['lon'], n['lat'])[0],
                       xy(lon, lat)[1] - xy(n['lon'], n['lat'])[1])
        if d < 100:
            nd.append((d, n['id'], n['kind']))
    nd.sort()
    for d, i, k in nd[:6]:
        print('   node %4.0fм  id=%s kind=%s' % (d, i, k))

if __name__ == '__main__':
    data = json.load(open(os.path.join(BASE, 'data', sys.argv[1]), encoding='utf-8'))
    for q in data:
        if q.get('lon'):
            context(float(q['lon']), float(q['lat']), '%s id=%s conf=%s' % (q['table'], q['id'], q.get('confidence', '')))
        else:
            print('=== %s id=%s (без точки)' % (q['table'], q['id']))
        print('   rat: %s' % q.get('rationale', '')[:200])
