"""Parse KSODD PDF tables into CSV files in data/ksodd_tables/.
Tables:
  - 2.5.1 (p42-44): traffic lights address list          -> traffic_lights.csv
  - 2.4.2.1 (p35-38): street length table                -> road_categories.csv (no categories in source)
  - 2.9.1 (p65-70): daily intensity by direction         -> traffic_intensity.csv
  - 2.9.3 (p73-77): intersections, lanes, capacity       -> lanes.csv
"""
import csv, os, re, sys
import pdfplumber

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
PDF = os.path.join(BASE, 'data', 'raw', 'ksodd.pdf')
OUT = os.path.join(BASE, 'data', 'ksodd_tables')
os.makedirs(OUT, exist_ok=True)
SRC = 'ksodd.pdf'


def clean(s):
    return re.sub(r'\s+', ' ', (s or '').replace('\n', ' ')).strip()


def write_csv(name, header, rows):
    path = os.path.join(OUT, name)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(header + ['source'])
        w.writerows(rows)
    print(name, len(rows), 'rows')


def tables_on(pdf, pageno):
    return pdf.pages[pageno - 1].extract_tables()


with pdfplumber.open(PDF) as pdf:

    # ---- traffic lights: table 2.5.1, pages 42-44 ----
    rows = []
    for p in (42, 43, 44):
        for tbl in tables_on(pdf, p):
            for r in tbl:
                num, addr = clean(r[0]), clean(r[1] if len(r) > 1 else '')
                if not re.fullmatch(r'\d+', num) or not addr:
                    continue
                fed = '*' in addr
                rows.append([num, addr.replace('*', '').strip(), '', '',
                             'на федеральной трассе А-401' if fed else '',
                             f'{SRC} p{p}'])
    write_csv('traffic_lights.csv',
              ['id', 'address', 'cycle_s', 'phases', 'note'], rows)

    # ---- road lengths: table 2.4.2.1, pages 35-38 ----
    rows = []
    num_re = re.compile(r'^\d+$')
    for p in (35, 36, 37, 38):
        for tbl in tables_on(pdf, p):
            for r in tbl:
                num, name = clean(r[0]), clean(r[1] if len(r) > 1 else '')
                if not num_re.match(num) or not name or name.upper().startswith('ИТОГО'):
                    continue
                length = clean(r[2] if len(r) > 2 else '')
                nonnorm = clean(r[3] if len(r) > 3 else '')
                rows.append([name, '', length, nonnorm, f'{SRC} p{p}'])
    write_csv('road_categories.csv',
              ['street', 'category', 'length_km', 'nonnorm_length_km'], rows)

    # ---- daily intensity: table 2.9.1, pages 65-70 (skip table 2.8.1 on p65) ----
    rows = []
    for p in (65, 66, 67, 68, 69, 70):
        for tbl in tables_on(pdf, p):
            hdr = ' '.join(clean(c) for c in (tbl[0] if tbl else []))
            if 'Точка замера' not in hdr:
                continue
            point = ''
            for r in tbl[1:]:
                c = [clean(x) for x in r]
                if len(c) < 3:
                    continue
                if c[0]:
                    point = c[0]
                direc, val = c[1], c[2]
                if not re.search(r'\d', val):
                    continue
                m = re.match(r'^(\d+)\s*/\s*(\d+)$', val)
                if m:
                    vin, vout = m.group(1), m.group(2)
                else:
                    vin, vout = val, ''
                rows.append([point, direc, vin, vout, '', f'{SRC} p{p}'])
    write_csv('traffic_intensity.csv',
              ['point', 'direction', 'intensity_in_per_day',
               'intensity_out_per_day', 'year'], rows)

    # map point number -> full point name (table 2.9.3 truncates names)
    pt_by_num = {}
    for r in rows:
        m = re.match(r'^(\d+)\)', r[0])
        if m and m.group(1) not in pt_by_num:
            pt_by_num[m.group(1)] = r[0]

    # ---- lanes/capacity: table 2.9.3, pages 73-77 ----
    rows = []
    point = ''
    for p in (73, 74, 75, 76, 77):
        for tbl in tables_on(pdf, p):
            hdr = ' '.join(clean(c) for c in (tbl[0] if tbl else []))
            if 'Кол-вополос' not in hdr.replace(' ', ''):
                continue
            for r in tbl[1:]:
                c = [clean(x) for x in r] + [''] * 11
                if re.match(r'^\d+\)', c[1]):
                    point = c[1]
                elif c[1] and point:
                    point = point + ' ' + c[1]
                m = re.match(r'^(\d+)\)', point)
                if m and m.group(1) in pt_by_num:
                    point = pt_by_num[m.group(1)]
                direc, lanes = c[2], c[3]
                if not lanes:
                    continue
                rows.append([point, direc, lanes, c[7], c[8], c[10],
                             f'{SRC} p{p}'])
    write_csv('lanes.csv',
              ['point', 'direction', 'lanes', 'max_intensity_veh_per_h',
               'max_intensity_per_day', 'load_factor'], rows)
