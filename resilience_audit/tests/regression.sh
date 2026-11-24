#!/bin/sh
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$root"
compose=resilience_audit/deploy/compose
db="resilience_regression_$(date +%s)_$$"
source="$root/.resilience-local/source"
network=kamdispatch-resilience_sandbox

docker build -t kamdispatch-resilience-tests:local -f resilience_audit/deploy/Dockerfile.tests resilience_audit/deploy
"$compose" exec -T db createdb -U monitor "$db"
trap '"$compose" exec -T db dropdb -U monitor "$db"' EXIT

docker run --rm --network "$network" --env-file .resilience-local/local.env -e "TEST_DB=$db" \
  --mount "type=bind,src=$source,dst=/source,readonly" -w /source kamdispatch-resilience-tests:local \
  sh -c 'TEST_DATABASE_URL="host=db port=5432 dbname=$TEST_DB user=monitor password=$POSTGRES_PASSWORD" python -m pytest -p no:cacheprovider -q'

docker run --rm --network "$network" -e CITY_SIM_DB=/tmp/resilience-test.sqlite3 \
  --mount "type=bind,src=$source,dst=/suite,readonly" -w /suite/city-sim kamdispatch-resilience-city:win-1c3cdd8 \
  python -m unittest discover -s city_sim/tests

docker run --rm --network "$network" -e PYTHONPATH=shared \
  --mount "type=bind,src=$source,dst=/suite,readonly" -w /suite/city-sim kamdispatch-resilience-city:win-1c3cdd8 \
  python -m unittest discover -s shared/universal_search/tests
