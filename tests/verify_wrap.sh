#!/usr/bin/env bash
# `sdtools wrap` verification: skill folder -> console tool, deterministically.
set -uo pipefail
cd "$(dirname "$0")/.."
export SDTOOLS_HOME=/tmp/sdt-wrap-home SDTOOLS_OFFLINE=1
rm -rf "$SDTOOLS_HOME" /tmp/sdt-wraptest tools/wraptest envs/wraptest
PASS=0; FAIL=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi }

mkdir -p /tmp/sdt-wraptest/scripts
cat > /tmp/sdt-wraptest/SKILL.md <<'S'
agent instructions live here
S
cat > /tmp/sdt-wraptest/requirements.txt <<'S'
# nothing needed
S
cat > /tmp/sdt-wraptest/scripts/main.py <<'S'
import argparse, sys
ap = argparse.ArgumentParser()
ap.add_argument("--input", required=True, help="in dir")
ap.add_argument("--count", type=int, default=3)
ap.add_argument("--tag", action="append", help="labels")
ap.add_argument("--verbose", action="store_true")
a = ap.parse_args()
print("ran with", a.input, a.count, a.tag, a.verbose)
sys.exit(0)
S
OUT=$(sdtools wrap /tmp/sdt-wraptest --name wraptest 2>&1); CODE=$?
check "wrap exits 0" "$CODE" "0"
echo "$OUT" | grep -q "ignored (agentic" && ok "SKILL.md flagged as agentic, excluded" \
                                         || bad "SKILL.md not flagged"
echo "$OUT" | grep -q -- "--tag collides" && ok "reserved --tag auto-renamed with flag mapping" \
                                          || bad "reserved param not handled"
grep -q "flag: --tag" tools/wraptest/tool.yaml && ok "manifest carries flag: --tag" \
                                                || bad "flag mapping missing in manifest"
grep -q "environment: system" tools/wraptest/tool.yaml \
  && ok "empty requirements -> system env (no needless venv)" \
  || grep -q "environment:" tools/wraptest/tool.yaml && ok "env drafted" || bad "no environment key"

R=$(sdtools wraptest --input /tmp/sdt-wraptest --count 5 \
      --tag-arg a --tag-arg b --verbose --json 2>/dev/null)
echo "$R" | python3 -c '
import json,sys; d=json.load(sys.stdin)
ok = lambda c,m: print(("  \033[32mPASS\033[0m  " if c else "  \033[31mFAIL\033[0m  ")+m)
ok(d["status"]=="ok", "wrapped tool runs ok through the console")'
LAST=$(ls -t "$SDTOOLS_HOME"/runs/*.ndjson | head -1)
grep -q "ran with /tmp/sdt-wraptest 5 \['a', 'b'\] True" "$LAST" \
  && ok "child received --tag a --tag b via flag mapping" \
  || bad "child args wrong: $(grep 'ran with' "$LAST")"

echo "== positional args, stdlib filtering, pip-name mapping =="
rm -rf /tmp/sdt-wrap2 tools/wrap2 envs/wrap2 && mkdir -p /tmp/sdt-wrap2
cat > /tmp/sdt-wrap2/main.py <<'S'
from __future__ import annotations
import argparse, glob, shlex, tempfile, multiprocessing
from concurrent.futures import ThreadPoolExecutor
import numpy
import cv2
import yaml
from PIL import Image
ap = argparse.ArgumentParser()
ap.add_argument("data_root", help="root")
ap.add_argument("--crs", default="EPSG:2952")
a = ap.parse_args()
print("GOT", a.data_root, a.crs)
S
OUT2=$(sdtools wrap /tmp/sdt-wrap2 --name wrap2 2>&1)
grep -q "positional: true" tools/wrap2/tool.yaml && ok "positional arg captured in manifest" \
                                                 || bad "positional not captured"
DEPS=$(python3 -c "import yaml;print(','.join(yaml.safe_load(open('envs/wrap2/env.yaml'))['deps']))")
check "stdlib filtered + pip names mapped" "$DEPS" "numpy,opencv-python,pillow,pyyaml"
sed -i 's/^environment: wrap2$/environment: system/' tools/wrap2/tool.yaml
python3 - <<'S'
import pathlib
p = pathlib.Path("tools/wrap2/src/main.py"); t = p.read_text()
for h in ["import numpy","import cv2","import yaml","from PIL import Image"]:
    t = t.replace(h + "\n", "")
p.write_text(t)
S
D=$(sdtools wrap2 --data-root /tmp/x --crs EPSG:3857 --dry-run 2>&1)
echo "$D" | grep -qE '\-\-crs EPSG:3857 /tmp/x$' && ok "positional emitted last, after options" \
                                                   || bad "argv order wrong: $D"
R2=$(sdtools wrap2 --data-root /tmp/x --json 2>/dev/null)
echo "$R2" | python3 -c '
import json,sys; d=json.load(sys.stdin)
ok = lambda c,m: print(("  \033[32mPASS\033[0m  " if c else "  \033[31mFAIL\033[0m  ")+m)
ok(d["status"]=="ok", "tool with a positional arg runs ok")'
OUT3=$(sdtools wrap /tmp/sdt-wrap2 --name wrap2 2>&1); C3=$?
check "re-wrap without --force is refused" "$C3" "2"
sdtools wrap /tmp/sdt-wrap2 --name wrap2 --force >/dev/null 2>&1 && ok "--force redrafts" || bad "--force failed"
grep -q "already exists — left untouched" <(sdtools wrap /tmp/sdt-wrap2 --name wrap2 --force 2>&1) \
  && ok "reviewed env.yaml never clobbered by --force" || bad "env.yaml was clobbered"
rm -rf tools/wrap2 envs/wrap2

rm -rf tools/wraptest envs/wraptest
printf '\n  %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
