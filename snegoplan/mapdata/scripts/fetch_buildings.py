#!/usr/bin/env python3
# Скачивает выгрузки Overpass для задачи TASK_buildings.md по очереди из зеркал.
# Порядок зеркал: overpass.kumi.systems (раньше работало), overpass-api.de, overpass.private.coffee.
# При 429/504 (и прочих сбоях) — пауза 30 с, до 2 повторов, затем следующее зеркало.
# TLS не отключаем. Используется curl.exe (Windows), Python с -I.
import subprocess, sys, os, time, json

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
# (query-файл, выходной json, минимум элементов для признания успеха)
JOBS = [
    ("scripts/overpass_residential.ql",    "data/osm_residential.json",    100),
    ("scripts/overpass_attract_poly.ql",   "data/osm_attract_poly.json",   10),
    ("scripts/overpass_attract_center.ql", "data/osm_attract_center.json", 100),
]
LOG = os.path.join(BASE, "data", "overpass_buildings_attempts.log")

log = open(LOG, "a", encoding="utf-8")
def say(msg):
    print(msg, flush=True)
    log.write(msg + "\n")
    log.flush()

def fetch(query_file, out_file, min_elems):
    query = open(os.path.join(BASE, query_file), encoding="utf-8").read()
    tmp = os.path.join(BASE, out_file + ".tmp")
    for mirror in MIRRORS:
        for attempt in (1, 2, 3):  # первая + 2 повтора
            say(f"[{time.strftime('%H:%M:%S')}] {os.path.basename(out_file)} <- {mirror} попытка {attempt}")
            r = subprocess.run(
                ["curl.exe", "-sS", "--max-time", "600", "--retry", "0",
                 "--data-urlencode", f"data={query}",
                 "-o", tmp, "-w", "%{http_code}",
                 mirror],
                capture_output=True, text=True)
            code = r.stdout.strip()
            size = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            say(f"  http={code} size={size} err={r.stderr.strip()[:300]}")
            ok = False
            if code == "200" and size > 200:
                try:
                    with open(tmp, encoding="utf-8") as f:
                        d = json.load(f)
                    if "elements" in d and len(d["elements"]) >= min_elems:
                        ok = True
                except Exception as e:
                    say(f"  json error: {e}")
            if ok:
                os.replace(tmp, os.path.join(BASE, out_file))
                say(f"OK: {mirror}, элементов: {len(d['elements'])}, размер: {size}")
                return True
            if os.path.exists(tmp):
                os.remove(tmp)
            if attempt < 3:
                say("  пауза 30 с перед повтором")
                time.sleep(30)
        say(f"BLOCKED {mirror} для {os.path.basename(out_file)}")
    return False

say(f"=== старт {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
failed = [out for qf, out, mn in JOBS if not fetch(qf, out, mn)]
if failed:
    say("FAIL: не скачаны: " + ", ".join(failed))
    sys.exit(1)
say("Все выгрузки скачаны.")
