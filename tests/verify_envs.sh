#!/usr/bin/env bash
# Environment-resolution verification. Runs entirely offline (no API) —
# the console must be fully functional with zero infrastructure.
#
#   bash tests/verify_envs.sh
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD

export SDTOOLS_HOME=/tmp/sdt-env-home
export SDTOOLS_OFFLINE=1
rm -rf "$SDTOOLS_HOME" /tmp/sdt-envtest && mkdir -p /tmp/sdt-envtest

PASS=0; FAIL=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi }

echo "== 1. resolution is content-addressed and cached =="
OUT1=$(sdtools env resolve pointcloud)
D1=$(echo "$OUT1" | head -1 | grep -oE '\([0-9a-f]{12}\)' | tr -d '()')
OUT2=$(sdtools env resolve pointcloud)
echo "$OUT2" | grep -q "cache hit" && ok "second resolve is a cache hit" \
                                   || bad "expected cache hit: $OUT2"
D2=$(echo "$OUT2" | head -1 | grep -oE '\([0-9a-f]{12}\)' | tr -d '()')
check "digest stable across resolves" "$D1" "$D2"
[ -x "$SDTOOLS_HOME/envs/pointcloud-$D1/bin/python" ] \
  && ok "prefix contains its own interpreter" || bad "no interpreter in prefix"

echo "== 2. the tool really runs inside the environment =="
# system python must NOT have laspy; the env must.
python3 -c "import laspy" 2>/dev/null && bad "system python unexpectedly has laspy" \
                                      || ok "system python has no laspy (clean baseline)"
# make a real LAS file with the env's own laspy
"$SDTOOLS_HOME/envs/pointcloud-$D1/bin/python" - <<'PY'
import numpy as np, laspy
h = laspy.LasHeader(point_format=3, version="1.2")
las = laspy.LasData(h)
n = 1000
las.x = np.random.uniform(0, 100, n); las.y = np.random.uniform(0, 100, n)
las.z = np.random.uniform(700, 800, n)
las.write("/tmp/sdt-envtest/real.las")
print("wrote real.las")
PY
R=$(sdtools las-info --input /tmp/sdt-envtest/real.las --json 2>/dev/null)
check "las-info reads a real LAS via the env's laspy" \
  "$(echo "$R" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["status"], d["metrics"].get("total_points"))')" \
  "ok 1000"

echo "== 3. env identity lands in run telemetry =="
LAST=$(ls -t "$SDTOOLS_HOME"/runs/*.ndjson | head -1)
python3 - "$LAST" "$D1" <<'PY'
import json, sys
rec = [json.loads(l) for l in open(sys.argv[1])]
start = next(r for r in rec if r["kind"] == "run.start")["data"]
ok = lambda c, m: print(("  \033[32mPASS\033[0m  " if c else "  \033[31mFAIL\033[0m  ") + m)
ok(start.get("env_name") == "pointcloud", f"env_name recorded ({start.get('env_name')})")
ok(start.get("env_digest") == sys.argv[2], f"env_digest recorded ({start.get('env_digest')})")
PY

echo "== 4. lockfile edits change the digest (immutability) =="
cp envs/pointcloud/requirements.lock /tmp/sdt-envtest/lock.bak
echo "# comment to change content" >> envs/pointcloud/requirements.lock
D3=$(sdtools env resolve pointcloud 2>/dev/null | head -1 | grep -oE '\([0-9a-f]{12}\)' | tr -d '()')
[ "$D3" != "$D1" ] && ok "changed lockfile -> new digest ($D1 -> $D3)" \
                   || bad "digest did not change with lockfile"
mv /tmp/sdt-envtest/lock.bak envs/pointcloud/requirements.lock
PR=$(sdtools env prune)
echo "$PR" | grep -qE "removed [1-9]" && ok "prune removed the orphaned prefix" \
                                      || bad "prune removed nothing: $PR"

echo "== 5. an unlocked env fails the run BEFORE the tool starts =="
mkdir -p envs/_unlocked tools/_envtest
cat > envs/_unlocked/env.yaml <<'EOF'
kind: uv
python: "3.12"
deps: [numpy]
EOF
cat > tools/_envtest/tool.yaml <<'EOF'
name: envtest
version: 0.0.1
summary: test tool bound to an unlocked env
runtime: python
entry: run.py
environment: _unlocked
EOF
printf 'print("should never run")\n' > tools/_envtest/run.py
OUT=$(sdtools envtest --json 2>/dev/null); CODE=$?
check "run exits 1" "$CODE" "1"
echo "$OUT" | python3 -c '
import json,sys; d=json.load(sys.stdin)
ok = lambda c, m: print(("  \033[32mPASS\033[0m  " if c else "  \033[31mFAIL\033[0m  ") + m)
ok(d["status"] == "failed" and d["error_kind"] == "EnvResolveFailed",
   "failed with EnvResolveFailed (%s)" % d["error_kind"])
ok("sdtools env lock" in (d["error_message"] or ""), "error tells you the fix")'
LAST=$(ls -t "$SDTOOLS_HOME"/runs/*.ndjson | head -1)
grep -q "should never run" "$LAST" && bad "tool executed despite env failure" \
                                   || ok "tool never started"

echo "== 6. pixi backend drives the right commands (shim; pixi blocked in sandbox) =="
mkdir -p /tmp/sdt-envtest/bin
cat > /tmp/sdt-envtest/bin/pixi <<'EOF'
#!/usr/bin/env bash
echo "$@" >> /tmp/sdt-envtest/pixi-calls.log
case "$1" in
  --version) echo "pixi 0.40.0";;
  install)   exit 0;;
  run)       shift; while [ "$1" != "--" ]; do shift; done; shift; exec "$@";;
esac
EOF
chmod +x /tmp/sdt-envtest/bin/pixi
touch envs/ml-torch/pixi.lock
PIXI_OUT=$(PATH="/tmp/sdt-envtest/bin:$PATH" sdtools env resolve ml-torch 2>&1); PIXI_CODE=$?
check "pixi env resolves through the shim" "$PIXI_CODE" "0"
grep -q "install --manifest-path .*pixi.toml --frozen" /tmp/sdt-envtest/pixi-calls.log \
  && ok "pixi install called with --frozen (lockfile-strict)" \
  || bad "pixi install not called correctly: $(cat /tmp/sdt-envtest/pixi-calls.log)"
rm -f envs/ml-torch/pixi.lock

echo "== 7. local workflow: whole DAG on this machine, no API =="
cat > /tmp/sdt-envtest/local.yaml <<'EOF'
name: local-test
params: {site: /tmp/sdt-envtest}
steps:
  - key: a-scan
    tool: las-info
    params: {input: "${params.site}/real.las"}
  - key: boom
    tool: soak
    after: [a-scan]
    params: {seconds: 0.2, fail: true}
  - key: child
    tool: soak
    after: [boom]
    params: {seconds: 0.1}
  - key: survivor
    tool: soak
    after: [a-scan]
    params: {seconds: 0.2}
EOF
LOUT=$(sdtools workflow /tmp/sdt-envtest/local.yaml --local 2>&1); LCODE=$?
check "local workflow exits 1 (a step failed)" "$LCODE" "1"
echo "$LOUT" | grep -q "✓ a-scan" && ok "a-scan ok" || bad "a-scan missing"
echo "$LOUT" | grep -q "✕ boom" && ok "boom failed as designed" || bad "boom not failed"
echo "$LOUT" | grep -q "⊘ child: cancelled" && ok "child cancelled (cascade)" \
                                            || bad "child not cancelled"
echo "$LOUT" | grep -q "✓ survivor" && ok "survivor ran (independent branch)" \
                                    || bad "survivor did not run"
# deterministic order: a-scan before boom before child; survivor after boom (sorted Kahn)
python3 - <<PY
out = '''$LOUT'''
idx = {k: out.find(f" {k}") for k in ("a-scan", "boom", "child", "survivor")}
ok = lambda c, m: print(("  \033[32mPASS\033[0m  " if c else "  \033[31mFAIL\033[0m  ") + m)
ok(idx["a-scan"] < idx["boom"] < idx["child"], "topological order held")
PY

echo "== 8. two concurrent resolves of one env: exactly one build =="
rm -rf "$SDTOOLS_HOME/envs"
( sdtools env resolve pointcloud > /tmp/sdt-envtest/r1.txt 2>&1 ) &
( sdtools env resolve pointcloud > /tmp/sdt-envtest/r2.txt 2>&1 ) &
wait
BUILDS=$(cat /tmp/sdt-envtest/r1.txt /tmp/sdt-envtest/r2.txt | grep -c "built in")
HITS=$(cat /tmp/sdt-envtest/r1.txt /tmp/sdt-envtest/r2.txt | grep -c "cache hit")
check "exactly one process built" "$BUILDS" "1"
check "the other waited and reused it" "$HITS" "1"

rm -rf tools/_envtest envs/_unlocked
printf '\n  %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
