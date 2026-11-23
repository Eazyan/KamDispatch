"""Offline graph_v3 corridor adapter. Display classes are never speeds."""
import hashlib
import json
import math
from datetime import datetime


def digest(data):
    return hashlib.sha256(data).hexdigest()


def export_graph(raw):
    graph = json.loads(raw)
    edges = {e['id']: e for e in graph['edges']}
    if len(edges) != len(graph['edges']):
        raise ValueError('Duplicate edge ID')
    corridors, used = [], set()
    for eid, edge in sorted(edges.items()):
        if eid in used:
            continue
        geometry, length = edge['geometry'], float(edge['length_m'])
        if len(geometry) < 2 or not math.isfinite(length) or length <= 0:
            raise ValueError('Invalid edge geometry/length')
        members = [{'id': eid, 'reverse': False, 'length_m': length}]
        other = edges.get(edge.get('reverse_edge_id'))
        if other and other['id'] != eid:
            valid = (other.get('reverse_edge_id') == eid and edge['u'] == other['v']
                     and edge['v'] == other['u'] and geometry == other['geometry'][::-1]
                     and length == float(other['length_m']))
            if not valid:
                raise ValueError('Inconsistent reverse edge: ' + eid)
            members.append({'id': other['id'], 'reverse': True, 'length_m': length})
        used.update(m['id'] for m in members)
        corridors.append({'id': eid, 'coordinates': geometry, 'length_m': length,
                          'directed_edges': members, 'name': edge.get('name')})
    return {'graph_sha256': digest(raw), 'segments': corridors}


def aware(value):
    date = datetime.fromisoformat(value)
    if date.tzinfo is None:
        raise ValueError('Time requires timezone')
    return date


def assign(export, lines, projected, region, *, graph_sha256, source_sha256,
           captured_at, now, max_age_s=900, provider_updated_at=None, bin_px=2):
    """Projected geometry and extracted source lines share CSS pixel coordinates.

    Sample bins preserve local intervals; ambiguous parallel candidates are rejected
    even when they share an OSM way. Coverage is a length fraction, not confidence.
    """
    import numpy as np
    if graph_sha256 != export['graph_sha256']:
        raise ValueError('Graph hash mismatch')
    age = (aware(now) - aware(captured_at)).total_seconds()
    if max_age_s <= 0 or bin_px <= 0 or len(projected) != len(export['segments']):
        raise ValueError('Invalid configuration/projected geometry')
    stale = age > max_age_s or age < 0
    if provider_updated_at is not None:
        aware(provider_updated_at)
    x, y, w, h = region
    bins = []
    part_grid = {}
    radius = 12.0
    for ci, points in enumerate(projected):
        points = np.asarray(points, dtype=float)
        lengths = [float(np.linalg.norm(b-a)) for a,b in zip(points, points[1:])]
        total = sum(lengths)
        if not math.isfinite(total) or total <= 0:
            raise ValueError('Invalid projected geometry')
        offset = 0
        for a,b,length in zip(points, points[1:], lengths):
            if not length:
                continue
            # Index exact polyline parts; sampled-bin distances cause phase jitter.
            low = np.floor((np.minimum(a,b)-radius)/24).astype(int)
            high = np.floor((np.maximum(a,b)+radius)/24).astype(int)
            part = (ci,a,b,(b-a)/length,length)
            for col in range(low[0],high[0]+1):
                for row in range(low[1],high[1]+1):
                    part_grid.setdefault((col,row),[]).append(part)
            n = max(1, math.ceil(length/bin_px))
            for j in range(n):
                p = a+(b-a)*(j+.5)/n
                bins.append({'ci': ci, 'p': p, 't': (b-a)/length,
                             'start': (offset+length*j/n)/total,
                             'end': (offset+length*(j+1)/n)/total,
                             'visible': x <= p[0] < x+w and y <= p[1] < y+h,
                             'votes': [], 'source_ids': set(), 'ambiguous': False})
            offset += length
    grid = {}
    for item in bins:
        key = tuple(np.floor(item['p']/8).astype(int))
        grid.setdefault(key, []).append(item)
    for line in lines:
        if line['traffic_status'] not in {'free','slow','congested'}:
            raise ValueError('Unknown source color')
        a,b = np.asarray(line['pixels'], dtype=float)
        length = float(np.linalg.norm(b-a))
        if not math.isfinite(length) or length <= 0:
            raise ValueError('Invalid source line')
        tangent = (b-a)/length
        # Every bin competes against other corridors at its closest source sample.
        low = np.floor((np.minimum(a,b)-radius)/8).astype(int)
        high = np.floor((np.maximum(a,b)+radius)/8).astype(int)
        candidates = [q for col in range(low[0],high[0]+1)
                      for row in range(low[1],high[1]+1) for q in grid.get((col,row), [])]
        for item in candidates:
            if not item['visible'] or abs(item['t'] @ tangent) < math.cos(math.radians(20)):
                continue
            along = float((item['p']-a) @ tangent)
            # Lateral tolerance must not extend a ribbon far beyond its endpoints.
            if along < -1 or along > length+1:
                continue
            p = a+tangent*float(np.clip(along,0,length))
            distance = float(np.linalg.norm(item['p']-p))
            if distance > radius:
                continue
            competitors = []
            key=tuple(np.floor(p/24).astype(int))
            for ci,start,end,direction,part_length in part_grid.get(key,[]):
                if ci==item['ci'] or abs(direction @ tangent)<math.cos(math.radians(20)):
                    continue
                q=start+direction*float(np.clip((p-start) @ direction,0,part_length))
                if np.linalg.norm(q-p)<=distance+2:
                    competitors.append(ci)
            item['source_ids'].add(line['id'])
            if competitors:
                item['ambiguous'] = True
            else:
                item['votes'].append((line['traffic_status'], line['id'], distance))
    results = []
    for item in bins:
        distances = {}
        for status,line_id,distance in item['votes']:
            distances[status]=min(distances.get(status,math.inf),distance)
        ranked=sorted(distances,key=distances.get)
        statuses=set(ranked)
        if len(ranked)>1 and distances[ranked[1]]-distances[ranked[0]]>3:
            statuses={ranked[0]}
        status, reason = 'unknown', 'no_source_color'
        if not item['visible']:
            reason = 'outside_capture'
        elif stale:
            reason = 'stale_or_future_capture'
        elif item['ambiguous'] and not statuses:
            reason = 'ambiguous_parallel_corridor'
        elif len(statuses) > 1:
            reason = 'mixed_colors'
        elif statuses:
            status, reason = next(iter(statuses)), 'matched_source_line'
        corridor = export['segments'][item['ci']]
        start, end = item['start']*corridor['length_m'], item['end']*corridor['length_m']
        ids = sorted(item['source_ids'])
        for member in corridor['directed_edges']:
            interval = [member['length_m']-end,member['length_m']-start] if member['reverse'] else [start,end]
            results.append({'corridor_id': corridor['id'], 'edge_id': member['id'],
                            'interval_m': interval, 'traffic_status': status, 'reason': reason,
                            'source_line_ids': ids, 'coverage_fraction': (end-start)/corridor['length_m'] if status != 'unknown' else 0,
                            'direction': 'unresolved', 'scope': 'road_corridor',
                            'timestamp': captured_at, 'provider_updated_at': provider_updated_at,
                            'graph_sha256': graph_sha256, 'source_sha256': source_sha256})
    return {'method': 'corridor_exact_geometry_v3', 'matching_parameters': {'radius_px': radius, 'ambiguity_margin_px': 2, 'class_margin_px': 3, 'endpoint_extension_px': 1, 'bin_px': bin_px}, 'observations': results, 'stale': stale, 'age_s': age,
            'max_age_s': max_age_s, 'validation': 'experimental_unvalidated',
            'not_for_automatic_dispatch': True}
