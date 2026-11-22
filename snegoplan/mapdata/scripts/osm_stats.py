import json, collections
d = json.load(open('/home/claude/mapdata/data/osm_petropavlovsk.json', encoding='utf-8'))
els = d['elements']
by_type = collections.Counter(e['type'] for e in els)
ways = [e for e in els if e['type'] == 'way']
nodes = [e for e in els if e['type'] == 'node']
rels = [e for e in els if e['type'] == 'relation']

hw_ways = [w for w in ways if 'highway' in w.get('tags', {})]
hw_nodes = [n for n in nodes if 'highway' in n.get('tags', {})]

def cnt(items, key):
    return sum(1 for x in items if key in x.get('tags', {}))

stats = []
stats.append(f"elements total: {len(els)}  (ways={by_type['way']}, nodes={by_type['node']}, relations={by_type['relation']})")
stats.append(f"ways with highway=*: {len(hw_ways)}")
stats.append(f"nodes with highway=*: {len(hw_nodes)}")
for t in ['lanes','lanes:forward','lanes:backward','turn:lanes','turn:lanes:forward','turn:lanes:backward','width','maxspeed','surface','oneway','name','ref','bus','lit','incline','bridge','tunnel','parking:lane','parking:lane:both','parking:lane:left','parking:lane:right']:
    stats.append(f"ways tagged {t}: {cnt(ways, t)}")
for t in ['highway','traffic_signals','traffic_signals:direction','crossing','stop','give_way','bus_stop','public_transport','barrier']:
    stats.append(f"nodes tagged {t}: {cnt(nodes, t)}")

ts = collections.Counter()
for n in hw_nodes:
    v = n['tags'].get('traffic_signals:direction')
    if v: ts[v] += 1
stats.append(f"traffic_signals:direction values: {dict(ts)}")

hw_cls = collections.Counter(w['tags']['highway'] for w in hw_ways)
stats.append("highway classes: " + str(dict(hw_cls.most_common())))
routes = collections.Counter(r['tags'].get('route') for r in rels if 'route' in r.get('tags', {}))
stats.append(f"route relations: {dict(routes)} (total relations: {len(rels)})")
amenity_p = sum(1 for w in ways if w.get('tags',{}).get('amenity')=='parking') + sum(1 for n in nodes if n.get('tags',{}).get('amenity')=='parking')
stats.append(f"amenity=parking objects: {amenity_p}")

open('/home/claude/mapdata/data/osm_stats.txt','w').write("\n".join(stats))
print("\n".join(stats))
