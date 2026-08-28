#!/usr/bin/env bash
# End-to-end verification. Boots the API on SQLite, exercises the paths that
# are easy to get wrong, and prints a pass/fail table.
#
#   bash tests/verify_e2e.sh
set -uo pipefail
cd "$(dirname "$0")/.."

PORT=${PORT:-8091}
DB=/tmp/sdtools-e2e.db
export SDTOOLS_HOME=/tmp/sdtools-e2e-home
LOG=/tmp/sdtools-e2e-api.log
PASS=0; FAIL=0

ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi }

cleanup() { pkill -f "port $PORT" 2>/dev/null; [ -n "${API_PID:-}" ] && kill "$API_PID" 2>/dev/null; }
trap cleanup EXIT

rm -rf "$SDTOOLS_HOME" "$DB"; mkdir -p "$SDTOOLS_HOME"
mkdir -p /tmp/e2e-in && for i in 1 2 3 4 5 6; do head -c 20000 /dev/urandom > /tmp/e2e-in/t$i.laz; done

echo "== 1. CLI works with no API configured (offline) =="
unset SDTOOLS_API_URL SDTOOLS_API_KEY
sdtools tile-index --input /tmp/e2e-in --out /tmp/e2e-out.json --quiet >/dev/null 2>&1
check "offline run exits 0" "$?" "0"
n=$(ls "$SDTOOLS_HOME"/runs/*.ndjson 2>/dev/null | wc -l | tr -d ' ')
check "local NDJSON written" "$n" "1"
check "nothing spooled when telemetry is off" "$(ls "$SDTOOLS_HOME"/spool 2>/dev/null | wc -l | tr -d ' ')" "0"

echo "== 2. Runs spool while the API is unreachable =="
export SDTOOLS_API_URL="http://127.0.0.1:$PORT" SDTOOLS_API_KEY="placeholder" SDTOOLS_USER=e2e
sdtools tile-index --input /tmp/e2e-in --out /tmp/e2e-out.json --quiet >/dev/null 2>&1
spooled=$(ls "$SDTOOLS_HOME"/spool 2>/dev/null | wc -l | tr -d ' ')
[ "$spooled" -gt 0 ] && ok "payloads spooled ($spooled) with API down" \
                     || bad "expected spooled payloads, got $spooled"

echo "== 3. Boot the API =="
DATABASE_URL="sqlite:///$DB" HEARTBEAT_GRACE_S=5 \
  uvicorn api.main:app --port "$PORT" --log-level warning > "$LOG" 2>&1 &
API_PID=$!
for _ in $(seq 1 40); do curl -sf "http://127.0.0.1:$PORT/v1/health" >/dev/null && break; sleep .5; done
ADMIN=$(grep -A1 "shown once" "$LOG" | tail -1 | tr -d ' ')
[ -n "$ADMIN" ] && ok "bootstrap admin key printed" || bad "no bootstrap key"

INGEST=$(curl -sX POST "http://127.0.0.1:$PORT/v1/keys" -H "Authorization: Bearer $ADMIN" \
  -d '{"name":"e2e","scope":"ingest"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["key"])')
READ=$(curl -sX POST "http://127.0.0.1:$PORT/v1/keys" -H "Authorization: Bearer $ADMIN" \
  -d '{"name":"e2e-read","scope":"read"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["key"])')
[ -n "$INGEST" ] && ok "minted ingest + read keys" || bad "key minting failed"

code=$(curl -so /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/v1/runs" \
  -H "Authorization: Bearer $INGEST")
check "ingest key cannot read" "$code" "403"
code=$(curl -so /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/v1/runs" \
  -H "Authorization: Bearer nope")
check "bad key rejected" "$code" "401"

echo "== 4. Spool replays, then live runs land =="
export SDTOOLS_API_KEY="$INGEST"
sdtools flush > /tmp/e2e-flush.txt 2>&1
grep -q "uploaded" /tmp/e2e-flush.txt && ok "flush ran" || bad "flush produced no output"

sdtools tile-index --input /tmp/e2e-in --out /tmp/e2e-out.json --project e2e --quiet >/dev/null 2>&1
check "live ok run exits 0" "$?" "0"
sdtools tile-index --input /tmp/e2e-in --out /tmp/e2e-b.json --fail-at 2 --project e2e --quiet >/dev/null 2>&1
check "failing run exits 1" "$?" "1"
sleep 4

python3 - "$PORT" "$READ" <<'PY'
import json, sys, urllib.request
port, key = sys.argv[1], sys.argv[2]
def get(p):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{p}",
                               headers={"Authorization": f"Bearer {key}"})
    return json.load(urllib.request.urlopen(r))
runs = get("/v1/runs?limit=50")["runs"]
ok = lambda c, m: print(("  \033[32mPASS\033[0m  " if c else "  \033[31mFAIL\033[0m  ") + m)
ok(len(runs) >= 2, f"runs visible via read key ({len(runs)})")
ok(any(r["status"] == "failed" for r in runs), "failure recorded")
f = next(r for r in runs if r["status"] == "failed")
ok(f["error_kind"] == "TileReadFailure", f"error_kind captured ({f['error_kind']})")
ok(bool(f["summary"]), "failure summarised eagerly")
s = next(r for r in runs if r["status"] == "ok")
ok(s["summary"] is None, "success NOT summarised eagerly (lazy)")
ok(s["metrics"].get("tiles_indexed") is not None,
   f"metrics captured ({s['metrics']})")
ok(s["event_count"] > 0, f"events stored ({s['event_count']})")
ev = get(f"/v1/runs/{s['run_id']}/events?level=warning")["events"]
ok(len(ev) > 0, f"level filter works ({len(ev)} warnings)")
lazy = get(f"/v1/runs/{s['run_id']}/summary")
ok(lazy["cached"] is False and lazy["summary"], "lazy summary generated on demand")
again = get(f"/v1/runs/{s['run_id']}/summary")
ok(again["cached"] is True, "second read served from cache")
st = get("/v1/stats?hours=24")
ok(st["total_runs"] >= 2 and st["failure_rate"] > 0, f"stats computed ({st['by_status']})")
PY

echo "== 5. Duplicate ingest is idempotent =="
python3 - "$PORT" "$INGEST" <<'PY'
import gzip, json, sys, urllib.request, uuid, datetime
port, key = sys.argv[1], sys.argv[2]
def post(path, payload):
    body = gzip.compress(json.dumps(payload, default=str).encode())
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Content-Encoding": "gzip"})
    return json.load(urllib.request.urlopen(r))
now = datetime.datetime.now(datetime.timezone.utc)
rid = str(uuid.uuid4())
post("/v1/runs", {"run_id": rid, "tool": "dup-test", "tool_version": "1", "cli_version": "1",
                  "cmdline": "x", "cwd": "/", "started_at": now,
                  "actor": {"user": "e2e", "machine_id": "m", "hostname": "h", "platform": "p"}})
batch = {"run_id": rid,
         "events": [{"seq": i, "ts": now, "message": f"line {i}"} for i in range(1, 21)]}
a = post(f"/v1/runs/{rid}/events", batch)
b = post(f"/v1/runs/{rid}/events", batch)
ok = lambda c, m: print(("  \033[32mPASS\033[0m  " if c else "  \033[31mFAIL\033[0m  ") + m)
ok(a["accepted"] == 20, f"first batch accepted ({a})")
ok(b["accepted"] == 0 and b["duplicates"] == 20, f"replay deduplicated ({b})")
dup = post("/v1/runs", {"run_id": rid, "tool": "dup-test", "tool_version": "1",
    "cli_version": "1", "cmdline": "x", "cwd": "/", "started_at": now,
    "actor": {"user": "e2e", "machine_id": "m", "hostname": "h", "platform": "p"}})
ok(dup.get("duplicate") is True, "duplicate run.start recognised")
PY

echo "== 6. Heartbeat sweeper marks abandoned runs lost =="
echo "     (waiting up to 40s for one sweeper cycle)"
lost=no
for _ in $(seq 1 40); do
  sleep 1
  if curl -s "http://127.0.0.1:$PORT/v1/runs?limit=50" -H "Authorization: Bearer $READ" \
     | grep -q '"status": *"lost"'; then lost=yes; break; fi
done
check "abandoned run marked lost" "$lost" "yes"

echo "== 7. SSE stream emits run objects =="
sse=$(timeout 6 curl -sN "http://127.0.0.1:$PORT/v1/stream" -H "Authorization: Bearer $READ" \
      & sleep 1; sdtools las-info --input /tmp/e2e-in --quiet >/dev/null 2>&1; sleep 4; wait)
echo "$sse" | grep -q '^data: ' && ok "SSE delivered a run event" || bad "no SSE data frames"

echo "== 8. No unhandled server errors =="
tb=$(grep -c Traceback "$LOG")
check "zero tracebacks in API log" "$tb" "0"

printf '\n  %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
