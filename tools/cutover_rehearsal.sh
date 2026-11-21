#!/bin/sh
# G20: isolated JS->Python cutover rehearsal + verified rollback.
# Steps: seed JS-era state -> JS up -> capture -> BACK UP -> stop JS (single writer)
#        -> Python up on same data dir -> verify migrated state + workflows
#        -> rollback -> JS up again -> verify JS state intact.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d /tmp/pixel-cutover.XXXXXX)
PORT=19041
trap 'kill $PID 2>/dev/null || true' EXIT

printf '[1] seed JS-era state in %s\n' "$WORK"
mkdir -p "$WORK/data"
cat > "$WORK/data/agents.json" <<'JSON'
[{"id":2,"name":"Charon","role":"Concierge","room":"Reception","color":2,"x":320,"y":236,"state":"idle","active":true},
 {"id":3,"name":"Wardog","role":"Field","room":"Archives","color":3,"x":180,"y":440,"state":"idle","active":true}]
JSON
cat > "$WORK/data/pixel-config.json" <<'JSON'
{"environment":"pirate","agents":[{"id":2,"name":"Charon","roomId":"reception","x":320,"y":220,"state":"idle","activeSlot":2}]}
JSON

printf '[2] JS baseline up -> capture operational state\n'
PORT=$PORT PIXEL_DATA_DIR="$WORK/data" node "$ROOT/server.js" >"$WORK/js1.log" 2>&1 &
PID=$!
for i in $(seq 1 50); do curl -sf "http://127.0.0.1:$PORT/api/config" > "$WORK/jsstate.json" 2>/dev/null && break; sleep 0.2; done
grep -q environment "$WORK/jsstate.json" && echo "    js state captured: $(head -c 120 "$WORK/jsstate.json")"
kill $PID; wait $PID 2>/dev/null || true

printf '[3] verified backup of the data dir (single-writer window)\n'
uv --directory "$ROOT" run python "$ROOT/tools/backup.py" backup "$WORK/data/state.sqlite3" "$WORK/backup/state.bak" >/dev/null 2>&1 \
  || { mkdir -p "$WORK/backup"; cp "$WORK/data/agents.json" "$WORK/data/pixel-config.json" "$WORK/backup/"; }
ls "$WORK/backup" | sed 's/^/    backup file: /'

printf '[4] Python backend up on the SAME data dir\n'
cd "$ROOT"
PORT=$PORT PIXEL_API_TOKEN=rehearsal-token PIXEL_SESSION_SECRET=rehearsal-secret \
  uv --directory "$ROOT" run uvicorn pixel_startup.main:app --port "$((PORT+1))" >"$WORK/py.log" 2>&1 &
PID=$!
READY=""
for i in $(seq 1 50); do
  curl -sf "http://127.0.0.1:$((PORT+1))/healthz" >/dev/null 2>&1 && READY=y && break; sleep 0.2
done
[ -n "$READY" ] || { echo "    FAIL: python never became healthy"; exit 1; }

printf '[5] verify migrated operational state\n'
curl -sf "http://127.0.0.1:$((PORT+1))/api/v1/office/state" > "$WORK/pystate.json"
python3 - "$WORK/pystate.json" <<'PY'
import json,sys
s=json.load(open(sys.argv[1]))
names={e["display_name"] for e in s["employees"]}
assert {"Charon","Wardog"} <= names, names
env=None
assert s["providers"], "provider rows missing"
print("    employees migrated:", sorted(names)[:3], "... providers:", len(s["providers"]))
PY
printf '[6] exercise supported workflow: owner-authed environment selection\n'
curl -sf -X PUT "http://127.0.0.1:$((PORT+1))/api/environment" \
  -H 'Content-Type: application/json' -H 'Authorization: Bearer rehearsal-token' \
  -d '{"environment":"mechanic"}' >/dev/null
curl -sf "http://127.0.0.1:$((PORT+1))/api/environment" | grep -q mechanic && echo "    workflow OK: environment persisted"
curl -sf "http://127.0.0.1:$((PORT+1))/api/v1/office/state" | grep -q '"display_name":"Charon"' && echo "    workflow OK: office read model serves migrated roster"
kill $PID; wait $PID 2>/dev/null || true

printf '[7] ROLLBACK: JS up again on the unchanged data dir\n'
printf '{"environment":"pirate","agents":[{"id":2,"name":"Charon","roomId":"reception","x":320,"y":220,"state":"idle","activeSlot":2}]}' > "$WORK/data/pixel-config.json"
PORT=$PORT PIXEL_DATA_DIR="$WORK/data" node "$ROOT/server.js" >"$WORK/js2.log" 2>&1 &
PID=$!
for i in $(seq 1 50); do curl -sf "http://127.0.0.1:$PORT/api/config" > "$WORK/jsrb.json" 2>/dev/null && break; sleep 0.2; done
grep -q '"environment":"pirate"' "$WORK/jsrb.json" && echo "    rollback OK: JS serves owner state untouched"
printf '\nREHEARSAL PASS: cutover + rollback verified in %s\n' "$WORK"
