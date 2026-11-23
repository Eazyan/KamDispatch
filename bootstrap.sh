#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
mode=demo
case "${1:-}" in
  "") ;;
  --preview) mode=preview ;;
  -h|--help)
    printf 'Usage: %s [--preview]\n' "$0"
    exit 0
    ;;
  *) printf 'Usage: %s [--preview]\n' "$0" >&2; exit 2 ;;
esac
if (( $# > 1 )); then
  printf 'Usage: %s [--preview]\n' "$0" >&2
  exit 2
fi

docker compose version >/dev/null
docker info >/dev/null
python3 - "$mode" <<'PY'
import os
from pathlib import Path
import re
import secrets
import sys
from tempfile import NamedTemporaryFile

path = Path(".env")
if path.exists():
    print("Using existing .env")
    config = path.read_text(encoding="utf-8")
    entry = re.compile(r"^(?:export[ \t]+)?CITIZEN_BOT_API_KEY[ \t]*=[ \t]*(.*)$", re.MULTILINE)
    matches = list(entry.finditer(config))
    match = matches[-1] if matches else None
    if match is None or match[1].split("#", 1)[0].strip() in {"", '""', "''"}:
        value = f"CITIZEN_BOT_API_KEY={secrets.token_hex(32)}"
        if match is None:
            updated = config + ("\n" if config and not config.endswith("\n") else "") + value + "\n"
        else:
            updated = config[:match.start()] + value + config[match.end():]
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as target:
            target.write(updated)
        Path(target.name).replace(path)
else:
    config = Path(".env.example").read_text(encoding="utf-8")
    config = config.replace("MONITOR_MODE=demo\n", f"MONITOR_MODE={sys.argv[1]}\n", 1)
    config = config.replace("CITIZEN_BOT_API_KEY=\n", f"CITIZEN_BOT_API_KEY={secrets.token_hex(32)}\n", 1)
    config = config.replace("POSTGRES_PASSWORD=\n", f"POSTGRES_PASSWORD={secrets.token_hex(24)}\n", 1)
    with os.fdopen(os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600), "w", encoding="utf-8") as target:
        target.write(config)
PY

mkdir -p runtime/max
docker compose config --format json | python3 -c '
import json
from pathlib import Path
import ssl
import sys
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

environment = json.load(sys.stdin)["services"]["max-bot"]["environment"]
if not (environment.get("MAX_BOT_TOKEN") or "").strip():
    sys.exit(0)

base_url = "https://gu-st.ru/content/lending/"
bundle = Path("runtime/max/ca.pem")
context = ssl.create_default_context()
try:
    certificates = []
    for kind in ("root", "sub"):
        with urlopen(f"{base_url}russian_trusted_{kind}_ca_pem.crt", timeout=20) as response:
            certificate = response.read(65537).decode("ascii").strip()
        if len(certificate) > 65536:
            raise ValueError("слишком большой файл сертификата")
        context.load_verify_locations(cadata=certificate)
        certificates.append(certificate)
    with NamedTemporaryFile(mode="w", encoding="ascii", dir=bundle.parent, delete=False) as target:
        target.write("\n".join(certificates) + "\n")
    path = Path(target.name)
    try:
        path.chmod(0o644)
        path.replace(bundle)
    finally:
        path.unlink(missing_ok=True)
except (OSError, ValueError) as error:
    sys.exit(f"Не удалось подготовить сертификаты MAX: {error}")
print(f"Сертификаты MAX загружены в {bundle}")
'

if [[ ! -f runtime/demo/manifest.json ]]; then
  python3 fixtures/prepare_demo.py fixtures/sample/1.png fixtures/sample/2.png fixtures/sample/3.png fixtures/sample/4.png
fi

docker compose build api
docker compose up -d --no-build
binding=$(docker compose port api 8080)
python3 - "http://$binding" <<'PY'
import json
import sys
from time import monotonic, sleep
from urllib.request import urlopen

url = sys.argv[1]
deadline = monotonic() + 45
while monotonic() < deadline:
    try:
        with urlopen(url + "/healthz", timeout=3) as response:
            if json.load(response).get("status") == "ready":
                print(f"Dashboard: {url}")
                print(f"Citizen reports: {url}/citizens/")
                break
    except (OSError, ValueError):
        sleep(1)
else:
    sys.exit("The API did not become ready. Check docker compose logs api worker.")
PY
