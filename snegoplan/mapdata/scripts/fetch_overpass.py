#!/usr/bin/env python3
# Скачивает данные Overpass по очереди из зеркал, с 1 повтором при сбое.
import subprocess, sys, os, time

BASE = "/home/claude/mapdata"
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
QUERY_FILE = os.path.join(BASE, "scripts", "overpass_query.ql")
OUT = os.path.join(BASE, "data", "osm_petropavlovsk.json")
LOG = os.path.join(BASE, "data", "overpass_attempts.log")

query = open(QUERY_FILE, encoding="utf-8").read()
open(LOG, "w").write("")

for mirror in MIRRORS:
    for attempt in (1, 2):
        print(f"[{time.strftime('%H:%M:%S')}] {mirror} попытка {attempt}", flush=True)
        with open(LOG, "a") as lg:
            lg.write(f"{mirror} attempt {attempt}\n")
        r = subprocess.run(
            ["curl", "-sS", "--max-time", "600", "--retry", "0",
             "--data-urlencode", f"data={query}",
             "-o", OUT + ".tmp", "-w", "%{http_code}",
             mirror],
            capture_output=True, text=True)
        code = r.stdout.strip()
        size = os.path.getsize(OUT + ".tmp") if os.path.exists(OUT + ".tmp") else 0
        with open(LOG, "a") as lg:
            lg.write(f"  http={code} size={size} err={r.stderr.strip()[:300]}\n")
        # Проверим, что это валидный JSON и внутри есть "elements"
        ok = False
        if code == "200" and size > 1000:
            head = open(OUT + ".tmp", "rb").read(200)
            if b'"elements"' in open(OUT + ".tmp", "rb").read()[:500000] or b'"elements"' in head:
                try:
                    import json
                    with open(OUT + ".tmp", encoding="utf-8") as f:
                        d = json.load(f)
                    if "elements" in d and len(d["elements"]) > 100:
                        ok = True
                except Exception as e:
                    with open(LOG, "a") as lg:
                        lg.write(f"  json error: {e}\n")
        if ok:
            os.replace(OUT + ".tmp", OUT)
            print(f"OK: {mirror}, элементов: {len(d['elements'])}, размер: {size}", flush=True)
            sys.exit(0)
        if os.path.exists(OUT + ".tmp"):
            os.remove(OUT + ".tmp")
        if attempt == 2:
            with open(LOG, "a") as lg:
                lg.write(f"BLOCKED {mirror}\n")
        time.sleep(5)

print("FAIL: все зеркала недоступны", flush=True)
sys.exit(1)
