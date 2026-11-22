# -*- coding: utf-8 -*-
"""
match_ksodd.py — привязка таблиц КСОДД / зданий / POI к graph_v3 и OSM.
Только локальные файлы, без сети. Читает mapprep/out/graph_v3.json (read-only).

Вход:
  data/ksodd_tables/{traffic_lights,lanes,road_categories,traffic_intensity}.csv
  data/residential.csv, data/attractors.csv, data/osm_petropavlovsk.json
Выход (data/):
  ksodd_street_match.csv, ksodd_point_match.csv, traffic_lights_match.csv,
  buildings_match.csv, poi_critical.csv,
  match_review_unmatched.csv, qa_sample_tl_high.json
"""
import csv, json, math, os, re, difflib, collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # mapdata/
DATA = os.path.join(BASE, 'data')
GRAPH = os.path.abspath(os.path.join(BASE, '..', 'mapprep', 'out', 'graph_v3.json'))

KYV = 111320.0
LAT0 = 53.04
KXV = 111320.0 * math.cos(math.radians(LAT0))

def xy(lon, lat):
    return (lon * KXV, lat * KYV)

def dist_m(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])

# ---------------- нормализация имен ----------------
ABBR = [
    (r'\bпр-кт\b', 'проспект'), (r'\bпросп\b', 'проспект'),
    (r'\bул\b', 'улица'), (r'\bпер\b', 'переулок'), (r'\bб-р\b', 'бульвар'),
    (r'\bнаб\b', 'набережная'), (r'\bш\b', 'шоссе'), (r'\bпл\b', 'площадь'),
    (r'\bмкрн\b', 'микрорайон'), (r'\bпр\b', 'проспект'),
]
TYPE_WORDS = ['улица', 'проспект', 'переулок', 'бульвар', 'набережная',
              'шоссе', 'площадь', 'проезд', 'аллея', 'микрорайон', 'тупик', 'тракт']

def norm_full(s):
    """Аббревиатуры раскрыть, пунктуацию убрать, ё->е."""
    if not s:
        return ''
    s = s.lower().replace('ё', 'е')
    s = s.replace('\u2013', ' - ').replace('\u2014', ' - ')
    for pat, rep in ABBR:
        s = re.sub(pat, rep, s)
    s = re.sub(r'[^0-9a-zа-я\- ]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def bare_name(s):
    """Типовые слова убрать. Если имя целиком из типовых слов — вернуть ''.
    bare_keys() даёт токены-фолбэки для таких имён ('Набережная улица' -> ['набережная'])."""
    nf = norm_full(s)
    s = nf
    for w in TYPE_WORDS:
        s = re.sub(r'\b' + w + r'\b', ' ', s)
    return re.sub(r'\s+', ' ', s).strip(' -')

def bare_keys(s):
    """Кандидаты bare-ключа: обычный bare либо токены полной формы."""
    b = bare_name(s)
    if b:
        return [b]
    return [t for t in norm_full(s).split() if len(t) >= 3]

def flex(s):
    """Пробел -> [\s\-]+, дефис -> [\s\-]* (устойчиво к 'Северо- Восточное')."""
    out = []
    for ch in s:
        if ch == ' ':
            out.append(r'[\s\-]+')
        elif ch == '-':
            out.append(r'[\s\-]*')
        else:
            out.append(ch)
    return ''.join(out)

# ручные синонимы КСОДД -> v3 (зафиксированы в отчёте и rationale)
MANUAL_ALIAS = {
    'академика заварицкого': 'переулок имени Заварицкого А. Н.',
}

# ---------------- загрузка графа ----------------
print('loading graph_v3 ...', flush=True)
G = json.load(open(GRAPH, encoding='utf-8'))
NODES = {n['id']: n for n in G['nodes']}
EDGES = G['edges']

FULL_MAP = collections.defaultdict(set)      # norm_full(name) -> {canonical}
BARE_MAP = collections.defaultdict(set)      # bare -> {canonical}
canon_edges = collections.defaultdict(list)  # canonical -> [edge]
for e in EDGES:
    nm = e.get('name')
    if not nm:
        continue
    canon_edges[nm].append(e)
    FULL_MAP[norm_full(nm)].add(nm)
    for bk in bare_keys(nm):
        BARE_MAP[bk].add(nm)
COLLIDED_BARE = {k for k, v in BARE_MAP.items() if len(v) > 1}
for k in COLLIDED_BARE:
    del BARE_MAP[k]

# regex-алиасы: полные формы + неоднозначные bare (все canonical-коллизии даём отдельно)
ALIASES = []   # (compiled, canonical, label)
seen_pat = set()
def add_alias(pat, canonical, label):
    if pat in seen_pat:
        return
    seen_pat.add(pat)
    ALIASES.append((re.compile(r'(?<![a-zа-я0-9])' + pat + r'(?![a-zа-я0-9])'), canonical, label))

for nf, cans in FULL_MAP.items():
    if len(nf) < 3:
        continue
    for c in cans:
        add_alias(flex(nf), c, 'full:' + nf)
for bare, cans in BARE_MAP.items():
    if len(bare) < 3:
        continue
    for c in cans:
        add_alias(flex(bare), c, 'bare:' + bare)
ALIASES.sort(key=lambda t: -len(t[0].pattern))

def extract_streets(text):
    """Все улицы v3 в тексте -> (found[(canonical,label)], rest_text)."""
    found = []
    spans = []
    low = norm_full(text)
    for rx, canon, label in ALIASES:
        for m in rx.finditer(low):
            s, e = m.span()
            if any(not (e <= s2 or s >= e2) for s2, e2 in spans):
                continue
            spans.append((s, e))
            found.append((canon, label))
    rest = []
    prev = 0
    for s, e in sorted(spans):
        rest.append(low[prev:s])
        prev = e
    rest.append(low[prev:])
    rest = re.sub(r'\s+', ' ', ' '.join(rest)).strip()
    return found, rest

def resolve_street(name):
    """КСОДД-имя -> (canonical|None, {canon set}|None, kind, score, rationale)."""
    nf = norm_full(name)
    if nf in FULL_MAP:
        cans = FULL_MAP[nf]
        k = 'exact-full' if len(cans) == 1 else 'exact-full-merge'
        return (sorted(cans)[0], cans, k, 1.0, 'имя exact (полная форма): "%s"' % nf)
    b = bare_name(name)
    if not b:
        for t in nf.split():
            if len(t) >= 3 and t in BARE_MAP:
                b = t
                break
    if b in BARE_MAP:
        cans = BARE_MAP[b]
        k = 'exact-bare' if len(cans) == 1 else 'exact-bare-merge'
        return (sorted(cans)[0], cans, k, 0.98, 'имя exact (без типа): "%s"' % b)
    if b in MANUAL_ALIAS:
        c = MANUAL_ALIAS[b]
        return (c, {c}, 'manual', 0.9, 'ручной синоним КСОДД->v3: "%s" -> "%s" (зафиксирован в отчёте)' % (b, c))
    if b in COLLIDED_BARE:
        # нельзя разрешить без типового слова
        return (None, None, 'collided', 0.0, 'неоднозначное имя (коллизия в v3): "%s"' % b)
    best, bs = None, 0.0
    for cand in BARE_MAP:
        r = difflib.SequenceMatcher(None, b, cand).ratio()
        if r > bs:
            bs, best = r, cand
    if bs >= 0.8:
        cans = BARE_MAP[best]
        return (sorted(cans)[0], cans, 'fuzzy', round(bs, 3),
                'имя fuzzy %.2f: "%s" -> "%s"' % (bs, b, best))
    return (None, None, 'none', round(bs, 3), 'улица не найдена: "%s" (best fuzzy %.2f)' % (b, bs))

# ---------------- сегментный индекс ----------------
CELL = 75.0
GRID = collections.defaultdict(list)
SEG_BY_ID = {}
for e in EDGES:
    pts = e['geometry']
    for i in range(len(pts) - 1):
        x1, y1 = xy(*pts[i]); x2, y2 = xy(*pts[i + 1])
        sid = (e['id'], i)
        SEG_BY_ID[sid] = (x1, y1, x2, y2)
        for cx in range(int(min(x1, x2) // CELL), int(max(x1, x2) // CELL) + 1):
            for cy in range(int(min(y1, y2) // CELL), int(max(y1, y2) // CELL) + 1):
                GRID[(cx, cy)].append(sid)

def seg_dist(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - x1, py - y1), x1, y1
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    qx, qy = x1 + t * dx, y1 + t * dy
    return math.hypot(px - qx, py - qy), qx, qy

def nearest_edge(lon, lat, maxdist):
    px, py = xy(lon, lat)
    R = int(maxdist // CELL) + 1
    cxs, cys = int(px // CELL), int(py // CELL)
    best = None
    seen = set()
    for cx in range(cxs - R, cxs + R + 1):
        for cy in range(cys - R, cys + R + 1):
            for sid in GRID.get((cx, cy), ()):
                if sid in seen:
                    continue
                seen.add(sid)
                x1, y1, x2, y2 = SEG_BY_ID[sid]
                d, qx, qy = seg_dist(px, py, x1, y1, x2, y2)
                if best is None or d < best[0]:
                    best = (d, sid, (qx, qy))
    if best is None or best[0] > maxdist:
        return None
    eid = best[1][0]
    e = next(e for e in EDGES if e['id'] == eid)
    return e, best[0], best[2]

NODE_LIST = G['nodes']
def nearest_node(lon, lat, maxdist, kinds=None):
    px, py = xy(lon, lat)
    best = None
    for n in NODE_LIST:
        if kinds and n['kind'] not in kinds:
            continue
        d = dist_m((px, py), xy(n['lon'], n['lat']))
        if best is None or d < best[0]:
            best = (d, n)
    if best and best[0] <= maxdist:
        return best
    return None

# ---------------- пересечение улиц ----------------
def street_segs(canonical):
    out = []
    for e in canon_edges.get(canonical, []):
        pts = e['geometry']
        for i in range(len(pts) - 1):
            out.append((xy(*pts[i]), xy(*pts[i + 1])))
    return out

def streets_cross(streets, maxd=80.0):
    """streets: [canonical,...] (с дублями ок). Точка = середина ближайших точек."""
    uniq = []
    for s in streets:
        if s not in uniq:
            uniq.append(s)
    best = None
    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            A = street_segs(uniq[i])
            B = street_segs(uniq[j])
            if not A or not B:
                continue
            for a in A:
                am = ((a[0][0] + a[1][0]) / 2, (a[0][1] + a[1][1]) / 2)
                for b in B:
                    d, qx, qy = seg_dist(am[0], am[1], b[0][0], b[0][1], b[1][0], b[1][1])
                    d2, qx2, qy2 = seg_dist(qx, qy, a[0][0], a[0][1], a[1][0], a[1][1])
                    dd = (d + d2) / 2
                    if best is None or dd < best[0]:
                        best = (dd, (qx + qx2) / 2, (qy + qy2) / 2)
    if best and best[0] <= maxd:
        return (best[1] / KXV, best[2] / KYV, best[0])
    return None

BEARINGS = {'север': 0.0, 'юг': 180.0, 'восток': 90.0, 'запад': 270.0}

def arm_with_bearing(canonical, ilon, ilat, qual):
    """Пара рёбер (edge+reverse) улицы, плечо у перекрёстка с квалификатором."""
    px, py = xy(ilon, ilat)
    qwant = BEARINGS.get(qual)
    best = None
    for e in canon_edges.get(canonical, []):
        pts = e['geometry']
        dmin, qbest = None, None
        for i in range(len(pts) - 1):
            x1, y1 = xy(*pts[i]); x2, y2 = xy(*pts[i + 1])
            d, qx, qy = seg_dist(px, py, x1, y1, x2, y2)
            if dmin is None or d < dmin:
                dmin, qbest = d, (qx, qy)
        if dmin is None or dmin > 150:
            continue
        if qwant is not None:
            az = math.degrees(math.atan2(qbest[0] - px, qbest[1] - py)) % 360
            score = abs((az - qwant + 180) % 360 - 180)
        else:
            score = dmin
        key = (score, dmin)
        if best is None or key < best[0]:
            best = (key, e, dmin)
    if not best:
        return None
    e = best[1]
    ids = [e['id']]
    rid = e.get('reverse_edge_id')
    if rid:
        ids.append(rid)
    return ids, best[2]

def all_pairs_of(canonicals):
    """Все пары рёбер (edge+reverse) для списка canonical."""
    ids = []
    for c in canonicals:
        for e in canon_edges.get(c, []):
            ids.append(e['id'])
            rid = e.get('reverse_edge_id')
            if rid:
                ids.append(rid)
    return sorted(set(ids))

# ---------------- чтение/запись CSV ----------------
def read_csv(name):
    return list(csv.DictReader(open(os.path.join(DATA, name), encoding='utf-8')))

def write_csv(name, rows, fields):
    with open(os.path.join(DATA, name), 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fields, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow(r)

def jdump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))

print('aliases:', len(ALIASES), 'collided bare:', len(COLLIDED_BARE), flush=True)

# ============================================================
# 1. road_categories.csv + lanes.csv -> ksodd_street_match.csv
# ============================================================
out_street = []
rc = read_csv(os.path.join('ksodd_tables', 'road_categories.csv'))
for r in rc:
    street = r['street']
    canon, cans, kind, score, rat = resolve_street(street)
    match = 'yes'
    conf = 'high' if kind.startswith('exact') else ('medium' if kind in ('manual', 'fuzzy') else 'low')
    if canon is None:
        # fallback: извлечь улицы из текста (пояснения в скобках, сегменты А - Б)
        found, rest = extract_streets(street)
        ucanons = []
        for c, _ in found:
            if c not in ucanons:
                ucanons.append(c)
        if ucanons:
            cans = set(ucanons)
            canon = ucanons[0]
            kind = 'extract'
            match = 'yes'
            conf = 'medium'
            score = 0.7
            rat = 'имя извлечено из описания: %s; хвост: "%s"' % ('+'.join(ucanons), rest[:50])
        else:
            match = 'no'
    edges_ids = all_pairs_of(cans) if cans else []
    if match == 'yes' and not edges_ids:
        match = 'no'; conf = 'low'; rat += '; рёбер нет'
    if kind and kind.endswith('merge'):
        rat += '; объединены регистровые дубли v3: %s' % sorted(cans)
    out_street.append({
        'ksodd_file': 'road_categories.csv', 'ksodd_street': street,
        'v3_edge_ids': jdump(edges_ids), 'score': score, 'match': match,
        'values': jdump({'length_km': r['length_km'], 'category': r['category']}),
        'confidence': conf, 'rationale': rat, 'source': r['source']})

ln = read_csv(os.path.join('ksodd_tables', 'lanes.csv'))
for r in ln:
    point = r['point']; direction = r['direction']
    dl = norm_full(direction)
    qual = next((w for w in BEARINGS if re.search(r'\b' + w + r'\b', dl)), None)
    flow = 'вход' if 'вход' in dl else ('выход' if 'выход' in dl else '')
    found_d, rest_d = extract_streets(direction)
    canon_d = None
    rat_parts = []
    if found_d:
        canon_d = found_d[0][0]
        score = 1.0
        rat_parts.append('направление exact: "%s"' % found_d[0][1])
    else:
        d_clean = re.sub(r'\b(вход|выход)\b', ' ', dl)
        d_clean = re.sub(r'\bна\s+(север|юг|восток|запад)\b', ' ', d_clean)
        canon_d, cans_d, kind_d, score, rat0 = resolve_street(d_clean)
        rat_parts.append(rat0)
    match = 'yes' if canon_d else 'no'
    pstreets, prest = extract_streets(point)
    upoint = []
    for c, _ in pstreets:
        if c not in upoint:
            upoint.append(c)
    conf = 'high'
    ilon = ilat = None
    ids = []
    if len(upoint) >= 2:
        cs = streets_cross(upoint)
        if cs:
            ilon, ilat, cd = cs
            rat_parts.append('перекрёсток геом: %s (d=%.0fм)' % ('+'.join(upoint[:2]), cd))
        else:
            conf = 'medium'
            rat_parts.append('геометрия перекрёстка не найдена: %s' % '+'.join(upoint))
    else:
        conf = 'medium'
        rat_parts.append('в точке <2 улиц v3: "%s"' % prest[:60])
    if canon_d and ilon is not None:
        ab = arm_with_bearing(canon_d, ilon, ilat, qual)
        if ab:
            ids, arm_d = ab
            rat_parts.append('плечо d=%.0fм az=%s flow=%s' % (arm_d, qual or '-', flow))
            if arm_d > 50:
                conf = 'medium'
        else:
            conf = 'medium'
            rat_parts.append('плечо не найдено')
    elif canon_d:
        ids = all_pairs_of([canon_d])
        conf = 'medium'
        rat_parts.append('все рёбра улицы (%d)' % len(ids))
    if match == 'no':
        conf = 'low'
    out_street.append({
        'ksodd_file': 'lanes.csv',
        'ksodd_street': '%s | %s' % (point[:60], direction),
        'v3_edge_ids': jdump(ids), 'score': score, 'match': match,
        'values': jdump({'lanes': r['lanes'], 'max_intensity_veh_per_h': r['max_intensity_veh_per_h'],
                          'max_intensity_per_day': r['max_intensity_per_day'],
                          'load_factor': r['load_factor'], 'flow': flow, 'qual': qual or ''}),
        'confidence': conf, 'rationale': '; '.join(rat_parts), 'source': r['source']})

write_csv('ksodd_street_match.csv', out_street,
          ['ksodd_file', 'ksodd_street', 'v3_edge_ids', 'score', 'match', 'values',
           'confidence', 'rationale', 'source'])
print('ksodd_street_match.csv:', len(out_street), flush=True)

# ============================================================
# 2. traffic_intensity.csv -> ksodd_point_match.csv
# ============================================================
ti = read_csv(os.path.join('ksodd_tables', 'traffic_intensity.csv'))
out_point = []
for r in ti:
    point = r['point']; direction = r['direction']
    dl = norm_full(direction)
    qual = next((w for w in BEARINGS if re.search(r'\b' + w + r'\b', dl)), None)
    flow = 'вход' if 'вход' in dl else ('выход' if 'выход' in dl else '')
    found_d, rest_d = extract_streets(direction)
    rat = []
    ids = []
    if found_d:
        canon_d = found_d[0][0]
        score = 1.0
        rat.append('направление exact: "%s"' % found_d[0][1])
    else:
        d_clean = re.sub(r'\b(вход|выход)\b', ' ', dl)
        d_clean = re.sub(r'\bна\s+(север|юг|восток|запад)\b', ' ', d_clean)
        canon_d, cans_d, kind_d, score, rat0 = resolve_street(d_clean)
        rat.append(rat0)
    match = 'yes' if canon_d else 'no'
    pstreets, prest = extract_streets(point)
    upoint = []
    for c, _ in pstreets:
        if c not in upoint:
            upoint.append(c)
    conf = 'high'
    ilon = ilat = None
    if len(upoint) >= 2:
        cs = streets_cross(upoint)
        if cs:
            ilon, ilat, cd = cs
            rat.append('перекрёсток геом d=%.0fм (%s)' % (cd, '+'.join(upoint[:2])))
        else:
            conf = 'medium'
            rat.append('геометрия перекрёстка не найдена: %s' % '+'.join(upoint))
    else:
        conf = 'medium'
        rat.append('в точке <2 улиц v3: "%s"' % prest[:60])
    if canon_d and ilon is not None:
        ab = arm_with_bearing(canon_d, ilon, ilat, qual)
        if ab:
            ids, arm_d = ab
            rat.append('плечо d=%.0fм az=%s flow=%s' % (arm_d, qual or '-', flow))
            if arm_d > 40:
                conf = 'medium'
        else:
            conf = 'medium'
            rat.append('плечо не найдено')
    elif canon_d:
        ids = all_pairs_of([canon_d])
        conf = 'medium'
        rat.append('все рёбра улицы (%d)' % len(ids))
    if match == 'no':
        conf = 'low'
    out_point.append({
        'point': point, 'direction': direction,
        'v3_edge_ids': jdump(ids), 'lon': ('%.7f' % ilon) if ilon is not None else '',
        'lat': ('%.7f' % ilat) if ilat is not None else '',
        'match': match, 'confidence': conf, 'rationale': '; '.join(rat),
        'source': r['source'],
        'values': jdump({'intensity_in_per_day': r['intensity_in_per_day'],
                          'intensity_out_per_day': r['intensity_out_per_day'],
                          'year': r['year'], 'flow': flow, 'qual': qual or ''})})
write_csv('ksodd_point_match.csv', out_point,
          ['point', 'direction', 'v3_edge_ids', 'lon', 'lat', 'match', 'confidence',
           'rationale', 'source', 'values'])
print('ksodd_point_match.csv:', len(out_point), flush=True)

# ============================================================
# 3. геокодинг домов по residential.csv
# ============================================================
print('geocoding index ...', flush=True)
res = read_csv('residential.csv')
house_idx = collections.defaultdict(list)
HOUSE_RE = re.compile(r',\s*([0-9]+[a-zа-я]?(?:/[0-9]+)?)')
for r in res:
    addr = r['addr']
    if not addr:
        continue
    m = HOUSE_RE.search(addr)
    if not m:
        continue
    b = bare_name(addr[:m.start()])
    if len(b) < 3:
        continue
    house = m.group(1).lower().replace('ё', 'е')
    house_idx[b].append((house, float(r['lon']), float(r['lat']), r['osm_id']))

def geocode_house(street_canon, house_raw):
    cands = None
    for bk in bare_keys(street_canon):
        if bk in house_idx:
            cands = house_idx[bk]
            break
    if not cands:
        return None
    h = house_raw.lower().replace('ё', 'е')
    for house, lon, lat, oid in cands:
        if house == h:
            return (lon, lat, oid, 'exact')
    hn = re.match(r'[0-9]+', h)
    if hn:
        for house, lon, lat, oid in cands:
            if house.startswith(hn.group(0)):
                return (lon, lat, oid, 'num')
    return None

# ============================================================
# 4. traffic_lights.csv + OSM -> traffic_lights_match.csv
# ============================================================
tl = read_csv(os.path.join('ksodd_tables', 'traffic_lights.csv'))
osm = json.load(open(os.path.join(DATA, 'osm_petropavlovsk.json'), encoding='utf-8'))
# пул кандидатов для сверки: ВСЕ узлы highway=traffic_signals (95);
# строки в выходе: 26 узлов с явным ключом traffic_signals (согласно TASK/инварианту)
osm_all_sig = [e for e in osm['elements']
               if e['type'] == 'node' and e.get('tags', {}).get('highway') == 'traffic_signals']
osm_out = [e for e in osm['elements']
           if e['type'] == 'node' and 'traffic_signals' in e.get('tags', {})]
osm_out.sort(key=lambda e: e['id'])
print('OSM signal nodes: pool=%d, rows=%d' % (len(osm_all_sig), len(osm_out)), flush=True)

def parse_tl_address(addr):
    found, rest = extract_streets(addr)
    ucanons = []
    for c, _ in found:
        if c not in ucanons:
            ucanons.append(c)
    low = norm_full(addr)
    house_m = re.search(r'(?:д\.?\s*)?([0-9]+[a-zа-я]?(?:/[0-9]+)?)', rest)
    house = house_m.group(1) if house_m else None
    house_street = None
    if house:
        cm = re.match(r'^(.+?),\s*' + re.escape(house), low)
        if cm:
            cb = bare_name(cm.group(1))
            for cand_canon in canon_edges:
                if bare_name(cand_canon) == cb:
                    house_street = cand_canon
                    break
        if house_street is None and ucanons:
            # дом, привязанный к второй улице в адресе ("Проспект Таранца (Фролова, 5)")
            if len(ucanons) > 1:
                house_street = ucanons[-1]
            else:
                house_street = ucanons[0]
    gc = geocode_house(house_street, house) if (house and house_street) else None
    if gc:
        return {'type': 'house', 'streets': ucanons, 'house': house,
                'house_street': house_street, 'gc': gc}
    if len(ucanons) >= 2:
        return {'type': 'intersection', 'streets': ucanons, 'house': None,
                'house_street': None, 'gc': None}
    if ucanons:
        return {'type': 'street_only', 'streets': ucanons, 'house': house,
                'house_street': house_street, 'gc': None}
    return {'type': 'none', 'streets': [], 'house': house, 'house_street': house_street, 'gc': None}

located = []
for i, r in enumerate(tl):
    addr = r['address']
    p = parse_tl_address(addr)
    lon = lat = None
    how = ''
    rat = []
    if p['type'] == 'house':
        glon, glat, boid, howh = p['gc']
        lon, lat = glon, glat
        how = 'house:' + howh
        rat.append('дом "%s, %s" по residential.csv (%s, %s)' % (bare_name(p['house_street']), p['house'], howh, boid))
    elif p['type'] == 'intersection':
        cs = streets_cross(p['streets'])
        if cs:
            lon, lat, cd = cs
            how = 'cross'
            rat.append('пересечение "%s" геом d=%.0fм' % ('+'.join(p['streets'][:2]), cd))
        else:
            rat.append('пересечение не вычислено: %s' % '+'.join(p['streets']))
    elif p['type'] == 'street_only':
        rat.append('только улица, геопривязки нет: "%s"' % p['streets'][0])
    else:
        rat.append('адрес не разобран: "%s"' % addr)
    located.append({'i': i, 'lon': lon, 'lat': lat, 'how': how, 'rat': rat, 'p': p})

# кластеры КСОДД <= 25 м (union-find)
N = len(located)
parent = list(range(N))
def find(a):
    while parent[a] != a:
        parent[a] = parent[parent[a]]
        a = parent[a]
    return a
for a in range(N):
    if located[a]['lon'] is None:
        continue
    for b in range(a + 1, N):
        if located[b]['lon'] is None:
            continue
        if dist_m(xy(located[a]['lon'], located[a]['lat']), xy(located[b]['lon'], located[b]['lat'])) <= 25:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
clusters = collections.defaultdict(list)
for k, L in enumerate(located):
    if L['lon'] is not None:
        clusters[find(k)].append(k)
cluster_of = {}
for root, members in clusters.items():
    cid = 'C%02d' % (len(cluster_of) + 1)
    for m in members:
        cluster_of[m] = (cid, root)
print('tl clusters:', len(clusters), 'located:', sum(1 for L in located if L['lon'] is not None), flush=True)

def rep_key(k):
    L = located[k]
    return ({'cross': 0, 'house:exact': 1, 'house:num': 2}.get(L['how'], 3), L['i'])
cluster_rep = {root: sorted(m, key=rep_key)[0] for root, m in clusters.items()}

# one-to-one привязка представителей к OSM-пулу (95), жадно по дистанции
pool_nodes = {e['id']: e for e in osm_all_sig}
for e in osm_out:
    pool_nodes.setdefault(e['id'], e)
OSM_POOL = [(e['lon'], e['lat'], e['id'], ('traffic_signals' in e.get('tags', {})))
            for e in pool_nodes.values()]
pairs = []
for root, rep in cluster_rep.items():
    L = located[rep]
    for lon, lat, oid, explicit in OSM_POOL:
        d = dist_m(xy(L['lon'], L['lat']), xy(lon, lat))
        if d <= 40:
            pairs.append((d, root, oid, explicit))
pairs.sort()
rep_osm = {}     # root -> (d, oid, explicit)
used_osm = set()
for d, root, oid, explicit in pairs:
    if root in rep_osm or oid in used_osm:
        continue
    rep_osm[root] = (d, oid, explicit)
    used_osm.add(oid)

out_tl = []
for i, r in enumerate(tl):
    L = located[i]
    row = {'ksodd_id': r['id'], 'address': r['address'], 'cycle_s': r['cycle_s'],
           'phases': r['phases'], 'note': r['note'],
           'lon': ('%.7f' % L['lon']) if L['lon'] is not None else '',
           'lat': ('%.7f' % L['lat']) if L['lat'] is not None else '',
           'osm_id': '', 'cluster_id': '', 'status': '', 'match': '',
           'confidence': '', 'rationale': '', 'source': r['source'], 'addr_check': ''}
    rat = list(L['rat'])
    if L['lon'] is None:
        row.update(status='unmatched', match='no', confidence='low', rationale='; '.join(rat))
    else:
        cid, root = cluster_of[i]
        row['cluster_id'] = cid
        rep = cluster_rep[root]
        nj = nearest_node(L['lon'], L['lat'], 1e9, kinds=['junction', 'traffic_signals'])
        ac = ''
        if nj:
            ac = 'node@%dм(%s)' % (round(nj[0]), nj[1]['kind'])
        if rep != i:
            row.update(status='ambiguous', match='uncertain', confidence='low',
                       rationale='; '.join(rat + ['дубль кластера %s, представитель ksodd_id=%s' % (cid, tl[rep]['id'])]))
        elif root in rep_osm:
            d, oid, explicit = rep_osm[root]
            row['osm_id'] = 'n%d' % oid
            conf = 'high' if (L['how'] == 'cross' and d <= 25 and nj and nj[0] <= 40) else 'medium'
            if L['how'].startswith('house'):
                conf = 'medium'
            extra = 'OSM-светофор n%d d=%.0fм%s' % (oid, d, '' if explicit else ' (узел без явного traffic_signals-tag)')
            row.update(status='matched', match='yes', confidence=conf,
                       rationale='; '.join(rat + [extra]))
        else:
            if L['how'] == 'cross':
                conf = 'medium'
                if nj and nj[0] > 40:
                    conf = 'low'
                    rat.append('addr_check: до узла v3 %dм >40' % round(nj[0]))
                row.update(status='matched', match='yes', confidence=conf,
                           rationale='; '.join(rat + ['OSM-светофор в ≤40м не найден']))
            elif L['how'] == 'house:exact':
                row.update(status='matched', match='yes', confidence='medium',
                           rationale='; '.join(rat + ['OSM-пары нет; дом точный из residential.csv']))
            elif L['how'] == 'house:num':
                row.update(status='ambiguous', match='uncertain', confidence='low',
                           rationale='; '.join(rat + ['номер по числовой части; OSM-пары нет']))
            else:
                row.update(status='unmatched', match='no', confidence='low',
                           rationale='; '.join(rat + ['геопривязка недостаточна, в выдачу не идёт']))
        row['addr_check'] = ac
    out_tl.append(row)

# строки OSM: 26 узлов с явным тегом, по одному разу
for e in osm_out:
    oid = e['id']
    n = NODES.get(oid)
    paired = oid in used_osm
    out_tl.append({'ksodd_id': '', 'address': '', 'cycle_s': '', 'phases': '',
                   'note': 'OSM node, без пары в КСОДД',
                   'lon': e['lon'], 'lat': e['lat'], 'osm_id': 'n%d' % oid,
                   'cluster_id': '', 'status': 'unmatched', 'match': 'no',
                   'confidence': 'high',
                   'rationale': ('пара с КСОДД-строкой найдена (см. ksodd_id с osm_id=n%d)' % oid) if paired else
                                 'OSM traffic_signals node; пары в КСОДД-таблице (61 адрес) нет',
                   'source': 'osm_petropavlovsk.json', 'addr_check': ''})

write_csv('traffic_lights_match.csv', out_tl,
          ['ksodd_id', 'address', 'cycle_s', 'phases', 'note', 'lon', 'lat',
           'osm_id', 'cluster_id', 'status', 'match', 'confidence', 'rationale',
           'source', 'addr_check'])
print('traffic_lights_match.csv:', len(out_tl), flush=True)

# ============================================================
# 5. residential.csv -> buildings_match.csv
# ============================================================
out_b = []
for r in res:
    lon, lat = float(r['lon']), float(r['lat'])
    ne = nearest_edge(lon, lat, 50)
    if ne:
        e, d, q = ne
        out_b.append({'osm_id': r['osm_id'], 'lon': r['lon'], 'lat': r['lat'],
                      'levels': r['levels'], 'area_m2': r['area_m2'], 'addr': r['addr'],
                      'nearest_edge_id': e['id'], 'zone': e.get('zone') or '',
                      'distance_m': round(d, 1), 'status': 'matched',
                      'rationale': 'снап ≤50м: ребро %s (%s), d=%.1fм' % (e['id'], e.get('name') or e['highway'], d)})
    else:
        out_b.append({'osm_id': r['osm_id'], 'lon': r['lon'], 'lat': r['lat'],
                      'levels': r['levels'], 'area_m2': r['area_m2'], 'addr': r['addr'],
                      'nearest_edge_id': '', 'zone': '', 'distance_m': '',
                      'status': 'unmatched', 'rationale': 'нет ребра в ≤50м (isolated здание)'})
write_csv('buildings_match.csv', out_b,
          ['osm_id', 'lon', 'lat', 'levels', 'area_m2', 'addr', 'nearest_edge_id',
           'zone', 'distance_m', 'status', 'rationale'])
print('buildings_match.csv:', len(out_b), flush=True)

# ============================================================
# 6. attractors.csv -> poi_critical.csv (критичные + dedup <=75м по уровню)
# ============================================================
att = read_csv('attractors.csv')
CRIT_TAG = ('amenity=hospital', 'amenity=clinic', 'amenity=school',
            'amenity=kindergarten', 'office=government', 'amenity=college',
            'amenity=university')
crit_rows = []
stats_kind = collections.Counter()
for r in att:
    stats_kind[r['kind']] += 1
    tag = r['tag']
    if tag not in CRIT_TAG:
        continue
    level = ('hospital' if tag in ('amenity=hospital', 'amenity=clinic')
             else 'government' if tag == 'office=government' else 'school')
    crit_rows.append((r, level))

n = len(crit_rows)
par = list(range(n))
def fnd(a):
    while par[a] != a:
        par[a] = par[par[a]]; a = par[a]
    return a
pts = [xy(float(r['lon']), float(r['lat'])) for r, _ in crit_rows]
for a in range(n):
    for b in range(a + 1, n):
        if crit_rows[a][1] != crit_rows[b][1]:
            continue
        if dist_m(pts[a], pts[b]) <= 75:
            ra, rb = fnd(a), fnd(b)
            if ra != rb:
                par[ra] = rb
groups = collections.defaultdict(list)
for i in range(n):
    groups[fnd(i)].append(i)
keep = {}
for root, members in groups.items():
    best = None
    for i in members:
        r = crit_rows[i][0]
        ne = nearest_edge(float(r['lon']), float(r['lat']), 200)
        d = ne[1] if ne else 999
        if best is None or d < best[0]:
            best = (d, i)
    keep[root] = best[1]
out_poi = []
for i, (r, level) in enumerate(crit_rows):
    lon, lat = float(r['lon']), float(r['lat'])
    ne = nearest_edge(lon, lat, 50)
    eid = ne[0]['id'] if ne else ''
    d = round(ne[1], 1) if ne else ''
    zone = (ne[0].get('zone') if ne and ne[0].get('zone') is not None else '')
    bus_near = bool(ne and ne[0].get('bus_route'))
    root = fnd(i)
    if keep[root] != i:
        status = 'ambiguous'
        rat = 'дубль POI %s, представитель osm_id=%s' % (level, crit_rows[keep[root]][0]['osm_id'])
    else:
        status = 'matched'
        rat = 'критичный POI %s; снап %s' % (level, ('%.1fм' % ne[1]) if ne else '>50м')
    out_poi.append({'osm_id': r['osm_id'], 'lon': r['lon'], 'lat': r['lat'],
                    'kind': r['kind'], 'tag': r['tag'], 'name': '',
                    'zone': zone, 'nearest_edge_id': eid, 'distance_m': d,
                    'critical_access': level, 'bus_route_near': 'yes' if bus_near else 'no',
                    'status': status, 'rationale': rat})
write_csv('poi_critical.csv', out_poi,
          ['osm_id', 'lon', 'lat', 'kind', 'tag', 'name', 'zone', 'nearest_edge_id',
           'distance_m', 'critical_access', 'bus_route_near', 'status', 'rationale'])
print('poi_critical.csv:', len(out_poi), '(attractor kinds:', dict(stats_kind), ')', flush=True)

# ============================================================
# 7. review-CSV + QA-сэмпл
# ============================================================
review = []
for r in out_street:
    if r['match'] != 'yes':
        review.append({'table': 'ksodd_street_match', 'id': r['ksodd_file'] + '|' + r['ksodd_street'],
                       'status': 'unmatched' if r['match'] == 'no' else 'ambiguous',
                       'confidence': r['confidence'], 'rationale': r['rationale'], 'source': r['source']})
for r in out_point:
    if r['match'] != 'yes':
        review.append({'table': 'ksodd_point_match', 'id': r['point'][:80] + '|' + r['direction'],
                       'status': 'unmatched' if r['match'] == 'no' else 'ambiguous',
                       'confidence': r['confidence'], 'rationale': r['rationale'], 'source': r['source']})
for r in out_tl:
    if r['status'] != 'matched':
        review.append({'table': 'traffic_lights_match', 'id': r['ksodd_id'] or r['osm_id'],
                       'status': r['status'], 'confidence': r['confidence'],
                       'rationale': r['rationale'], 'source': r['source'],
                       'lon': r['lon'], 'lat': r['lat']})
for r in out_poi:
    if r['status'] != 'matched':
        review.append({'table': 'poi_critical', 'id': r['osm_id'], 'status': r['status'],
                       'confidence': '', 'rationale': r['rationale'], 'source': 'attractors.csv',
                       'lon': r['lon'], 'lat': r['lat']})
write_csv('match_review_unmatched.csv', review,
          ['table', 'id', 'status', 'confidence', 'rationale', 'source', 'lon', 'lat'])
print('match_review_unmatched.csv:', len(review), flush=True)

qa = [{'table': 'traffic_lights', 'id': r['ksodd_id'], 'lon': r['lon'], 'lat': r['lat'],
       'confidence': r['confidence'], 'rationale': r['rationale']}
      for r in out_tl if r['ksodd_id'] and r['status'] == 'matched' and r['confidence'] == 'high']
json.dump(qa, open(os.path.join(DATA, 'qa_sample_tl_high.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print('QA high-confidence traffic lights:', len(qa), flush=True)

for name, rows, key in [('street', out_street, 'match'), ('point', out_point, 'match'),
                        ('tl', out_tl, 'status'), ('b', out_b, 'status'), ('poi', out_poi, 'status')]:
    print(name, dict(collections.Counter(r[key] for r in rows)))
