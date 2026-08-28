#!/usr/bin/env bash
# Multi-machine dispatch verification.
#
# Simulates one operator computer and three worker machines (separate
# SDTOOLS_HOME dirs and agent identities) against one API. Covers the
# guarantees that matter: exactly-one claimant, DAG ordering, label/queue
# routing, kill -9 -> lease-expiry retry, upstream-failure cascade,
# and deterministic FIFO order within a queue.
#
#   bash tests/verify_dispatch.sh
set -uo pipefail
cd "$(dirname "$0")/.."

PORT=${PORT:-8092}
DB=/tmp/sdt-dispatch.db
LOG=/tmp/sdt-dispatch-api.log
PASS=0; FAIL=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi }

cleanup() { jobs -p | xargs -r kill -9 2>/dev/null; }
trap cleanup EXIT

rm -rf "$DB" /tmp/sdt-home-* /tmp/sdt-site
mkdir -p /tmp/sdt-site && for i in $(seq 1 12); do head -c 9000 /dev/urandom > /tmp/sdt-site/t$i.laz; done

echo "== boot API (fast lease + sweep for testing) =="
env DATABASE_URL="sqlite:///$DB" LEASE_TTL_S=6 SWEEP_INTERVAL_S=2 HEARTBEAT_GRACE_S=8 \
  uvicorn api.main:app --port "$PORT" --log-level warning > "$LOG" 2>&1 &
for _ in $(seq 1 40); do curl -sf "http://127.0.0.1:$PORT/v1/health" >/dev/null && break; sleep .5; done
ADMIN=$(grep -A1 "shown once" "$LOG" | tail -1 | tr -d ' ')

mint() { curl -sX POST "http://127.0.0.1:$PORT/v1/keys" -H "Authorization: Bearer $ADMIN" \
         -d "{\"name\":\"$1\",\"scope\":\"$2\",\"actor_user\":\"$3\"}" \
         | python3 -c 'import json,sys;print(json.load(sys.stdin)["key"])'; }
K_SUBMIT=$(mint operator-anik submit anik)
K_READ=$(mint dash read dash)
K_W1=$(mint worker-1 ingest w1); K_W2=$(mint worker-2 ingest w2); K_W3=$(mint worker-3 ingest w3)
[ -n "$K_SUBMIT" ] && [ -n "$K_W3" ] && ok "minted operator + 3 worker keys" || bad "key minting"

echo "== scope: a worker (ingest) key cannot submit; operator cannot poll =="
c=$(curl -so /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/v1/jobs" \
    -H "Authorization: Bearer $K_W1" -d '{"tool":"soak"}')
check "ingest key cannot submit jobs" "$c" "403"
c=$(curl -so /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/v1/agents/poll" \
    -H "Authorization: Bearer $K_SUBMIT" -d '{"agent_id":"x"}')
check "submit key cannot poll as agent" "$c" "403"

worker() {  # $1=name $2=key $3=queues(csv->repeated) $4=labels $5=extra-args
  local home=/tmp/sdt-home-$1 qargs=""
  mkdir -p "$home"
  for q in $(echo "$3" | tr ',' ' '); do qargs="$qargs --queue $q"; done
  env SDTOOLS_HOME="$home" SDTOOLS_API_URL="http://127.0.0.1:$PORT" \
      SDTOOLS_API_KEY="$2" SDTOOLS_USER="$1" \
      sdtools agent --name "$1" $qargs --labels "$4" --poll-s 0.5 $5 \
      > /tmp/sdt-agent-$1.log 2>&1 &
  echo $!
}

echo "== start 3 'machines': w1,w2 on default; w3 on heavy with label bigmem =="
worker w1 "$K_W1" default ""        "--idle-exit-s 25" >/dev/null
worker w2 "$K_W2" default ""        "--idle-exit-s 25" >/dev/null
worker w3 "$K_W3" heavy   "bigmem"  "--idle-exit-s 25" >/dev/null
sleep 2
n=$(curl -s "http://127.0.0.1:$PORT/v1/agents" -H "Authorization: Bearer $K_READ" \
    | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["agents"]))')
check "3 agents registered" "$n" "3"

echo "== submit the DAG from the operator key =="
WF=$(curl -sX POST "http://127.0.0.1:$PORT/v1/workflows" -H "Authorization: Bearer $K_SUBMIT" \
  -H 'Content-Type: application/json' -d '{
  "name": "sundre-nightly", "project": "sundre-2026",
  "params": {"site": "/tmp/sdt-site"},
  "steps": [
    {"key": "inventory", "tool": "las-info",
     "params": {"input": "${params.site}"}},
    {"key": "index", "tool": "tile-index", "after": ["inventory"],
     "queue": "heavy", "require_labels": ["bigmem"],
     "params": {"input": "${params.site}", "out": "/tmp/sdt-site/index.json"}},
    {"key": "qc-north", "tool": "las-info", "after": ["index"],
     "params": {"input": "${params.site}"}},
    {"key": "qc-south", "tool": "las-info", "after": ["index"],
     "params": {"input": "${params.site}"}}
  ]}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["workflow_id"])')
[ -n "$WF" ] && ok "workflow accepted ($WF)" || bad "workflow rejected"

echo "== reject a cyclic DAG =="
c=$(curl -so /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/v1/workflows" \
  -H "Authorization: Bearer $K_SUBMIT" -d '{"name":"cycle","steps":[
    {"key":"a","tool":"soak","after":["b"]},{"key":"b","tool":"soak","after":["a"]}]}')
check "cycle rejected with 422" "$c" "422"

for _ in $(seq 1 60); do
  st=$(curl -s "http://127.0.0.1:$PORT/v1/workflows/$WF" -H "Authorization: Bearer $K_READ" \
       | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"])')
  [ "$st" != "running" ] && break; sleep 1
done
check "workflow finished ok" "$st" "ok"

curl -s "http://127.0.0.1:$PORT/v1/workflows/$WF" -H "Authorization: Bearer $K_READ" \
  > /tmp/sdt-wf.json
python3 - <<'PY'
import json
wf = json.load(open("/tmp/sdt-wf.json"))
jobs = {j["step_key"]: j for j in wf["jobs"]}
ok = lambda c, m: print(("  \033[32mPASS\033[0m  " if c else "  \033[31mFAIL\033[0m  ") + m)
ok(all(j["state"] == "ok" for j in jobs.values()), "all 4 steps ok")
ok(jobs["index"]["leased_by"].endswith(":w3"),
   f"heavy+bigmem step routed to w3 (ran on {jobs['index']['leased_by']})")
inv_done = jobs["inventory"]["finished_at"]; idx_start = jobs["index"]["started_at"]
ok(idx_start >= inv_done, f"DAG order held (index started {idx_start} >= inventory finished {inv_done})")
qc = {jobs["qc-north"]["leased_by"], jobs["qc-south"]["leased_by"]}
ok(qc <= {"", None} or all(x and (x.endswith(":w1") or x.endswith(":w2")) for x in qc),
   f"qc steps ran on the default pool ({sorted(x or '?' for x in qc)})")
ok(all(j["run_id"] for j in jobs.values()), "every job linked to a telemetry run_id")
PY

echo "== deterministic FIFO within a queue =="
python3 - "$PORT" "$K_SUBMIT" "$K_READ" <<'PY'
import json, sys, time, urllib.request
port, ksub, kread = sys.argv[1:4]
def call(method, path, key, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r))
ids = [call("POST", "/v1/jobs", ksub,
            {"tool": "soak", "queue": "serial", "priority": 100 if i else 5,
             "params": {"seconds": 0.4}})["job_id"] for i in range(4)]
# job 0 has priority 5 -> must run first even though all were submitted together
time.sleep(0.2)
PY
worker serial "$K_W1" serial "" "--max-jobs 4 --idle-exit-s 20" >/dev/null
for _ in $(seq 1 40); do
  left=$(curl -s "http://127.0.0.1:$PORT/v1/jobs?queue=serial&state=queued,leased,running" \
    -H "Authorization: Bearer $K_READ" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["jobs"]))')
  [ "$left" = "0" ] && break; sleep 1
done
python3 - "$PORT" "$K_READ" <<'PY'
import json, sys, urllib.request
port, key = sys.argv[1:3]
r = urllib.request.Request(f"http://127.0.0.1:{port}/v1/jobs?queue=serial&limit=10",
                           headers={"Authorization": f"Bearer {key}"})
jobs = json.load(urllib.request.urlopen(r))["jobs"]
jobs = [j for j in jobs if j["state"] == "ok"]
ordered = sorted(jobs, key=lambda j: j["started_at"])
ok = lambda c, m: print(("  \033[32mPASS\033[0m  " if c else "  \033[31mFAIL\033[0m  ") + m)
ok(len(jobs) == 4, f"all 4 serial jobs completed ({len(jobs)})")
ok(ordered[0]["priority"] == 5, "priority 5 ran before the priority-100 batch")
rest = [j for j in ordered[1:]]
ok(rest == sorted(rest, key=lambda j: (j["created_at"], j["job_id"])),
   "equal-priority jobs ran in submission order")
PY

echo "== kill -9 mid-job: lease expires, another machine retries =="
JOB=$(curl -sX POST "http://127.0.0.1:$PORT/v1/jobs" -H "Authorization: Bearer $K_SUBMIT" \
  -d '{"tool":"soak","queue":"flaky","params":{"seconds":30},"max_attempts":2}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["job_id"])')
VICTIM=$(worker victim "$K_W2" flaky "" "--max-jobs 1")
sleep 4   # let it claim and start
kill -9 "$VICTIM" 2>/dev/null && ok "victim agent killed -9 mid-job" || bad "victim not running"
worker rescuer "$K_W3" flaky "" "--max-jobs 1 --idle-exit-s 60" >/dev/null
# job soaks 30s on the rescuer after ~6s lease expiry; wait generously
st="?"
for _ in $(seq 1 70); do
  j=$(curl -s "http://127.0.0.1:$PORT/v1/jobs?queue=flaky" -H "Authorization: Bearer $K_READ")
  st=$(echo "$j" | python3 -c 'import json,sys;print(json.load(sys.stdin)["jobs"][0]["state"])')
  [ "$st" = "ok" ] && break; sleep 1
done
check "killed job re-queued and completed elsewhere" "$st" "ok"
echo "$j" | python3 -c '
import json,sys
j=json.load(sys.stdin)["jobs"][0]
ok = lambda c, m: print(("  \033[32mPASS\033[0m  " if c else "  \033[31mFAIL\033[0m  ") + m)
ok(j["attempts"] == 2, "took exactly 2 attempts (%d)" % j["attempts"])
ok(j["leased_by"] and j["leased_by"].endswith(":rescuer"),
   "completed on the rescuer machine (%s)" % j["leased_by"])'

echo "== failing step cancels its downstream cone, parallel branch survives =="
WF2=$(curl -sX POST "http://127.0.0.1:$PORT/v1/workflows" -H "Authorization: Bearer $K_SUBMIT" \
  -d '{"name":"cascade","steps":[
    {"key":"boom","tool":"soak","params":{"seconds":0.3,"fail":true},"max_attempts":1},
    {"key":"child","tool":"soak","after":["boom"],"params":{"seconds":0.2}},
    {"key":"grandchild","tool":"soak","after":["child"],"params":{"seconds":0.2}},
    {"key":"survivor","tool":"soak","params":{"seconds":0.3}}
  ]}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["workflow_id"])')
worker cascade "$K_W1" default "" "--max-jobs 2 --idle-exit-s 20" >/dev/null
for _ in $(seq 1 40); do
  st=$(curl -s "http://127.0.0.1:$PORT/v1/workflows/$WF2" -H "Authorization: Bearer $K_READ" \
       | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"])')
  [ "$st" != "running" ] && break; sleep 1
done
check "cascade workflow terminal state is failed" "$st" "failed"
curl -s "http://127.0.0.1:$PORT/v1/workflows/$WF2" -H "Authorization: Bearer $K_READ" | python3 -c '
import json,sys
wf=json.load(sys.stdin); jobs={j["step_key"]: j for j in wf["jobs"]}
ok = lambda c, m: print(("  \033[32mPASS\033[0m  " if c else "  \033[31mFAIL\033[0m  ") + m)
ok(jobs["boom"]["state"] == "failed", "boom failed")
ok(jobs["child"]["state"] == "cancelled" and jobs["child"]["error_kind"] == "UpstreamFailed",
   "child cancelled with UpstreamFailed")
ok(jobs["grandchild"]["state"] == "cancelled", "grandchild cancelled (transitive)")
ok(jobs["survivor"]["state"] == "ok", "independent branch still ran to ok")'

echo "== telemetry joined up: the dispatched runs are in /v1/runs =="
n=$(curl -s "http://127.0.0.1:$PORT/v1/runs?limit=100" -H "Authorization: Bearer $K_READ" \
    | python3 -c 'import json,sys;print(sum(1 for r in json.load(sys.stdin)["runs"] if any(t.startswith("job:") for t in r["tags"])))')
[ "$n" -ge 10 ] && ok "dispatched runs carry job: tags in telemetry ($n)" \
               || bad "expected >=10 job-tagged runs, got $n"

tb=$(grep -c Traceback "$LOG")
check "zero API tracebacks" "$tb" "0"

printf '\n  %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
