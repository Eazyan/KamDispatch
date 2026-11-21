"""Explicit inventory builder; offline, streaming hashes, excludes credentials/caches."""
import csv
import json
from memory import ROOT, MEMORY, files, digest

def build():
    rows = []
    for rel in files(ROOT):
        if rel.parts[:2] == ('docs','agent-memory') or rel.parts[:2] == ('scripts','agent_memory') or rel.parts[0] in {'.agents','.codex'}:
            continue
        path = ROOT/rel
        suffix = ''.join(path.suffixes)
        count = 'unknown'
        if suffix in {'.csv','.tsv'}:
            try:
                with path.open(newline='', encoding='utf-8-sig') as stream:
                    count = max(0, sum(1 for _ in csv.reader(stream, delimiter='\t' if suffix == '.tsv' else ','))-1)
            except (UnicodeError, csv.Error):
                pass
        elif suffix in {'.json','.geojson'} and path.stat().st_size < 2_000_000:
            try:
                obj = json.loads(path.read_text())
                if isinstance(obj,list): count = len(obj)
                elif isinstance(obj,dict) and isinstance(obj.get('features'),list): count = len(obj['features'])
            except (ValueError,UnicodeError):
                pass
        section = rel.parts[0]
        purpose = {'out':'generated research output', 'research':'research evidence', 'weather_coverage':'weather atlas/code/data',
                   'winter_audit':'road/contracts/relief evidence', 'city_sim':'city backend/assets', 'data':'input data',
                   'prototype':'synthetic simulation', 'snowops':'routing', 'docs':'documentation'}.get(section,'project source/archive')
        rows.append([str(rel),purpose,path.stat().st_size,suffix or 'text/unknown',count,digest(path),'unknown; consult source registry; not a publication clearance'])
    with (ROOT/MEMORY/'inventory.csv').open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['path','purpose','bytes','format','rows_or_objects','sha256','data_owner']);writer.writerows(rows)
    print(f'Inventory: {len(rows)} files, {sum(r[2] for r in rows)} bytes; secrets/caches/symlinks excluded')

if __name__ == '__main__': build()
