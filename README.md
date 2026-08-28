# sdtools

A LAStools-style console tool for Spatialdata's stable processing scripts, where
every run reports itself to an API so the team can see what's happening, a
dispatcher pushes work from one computer to a fleet of processing machines, and
a cheap model turns the logs into something readable. Optional one-way sync
puts each workflow's outcome into Plane as a work item.

Design rationale and the full contract: **[ARCHITECTURE.md](ARCHITECTURE.md)**
(dispatcher semantics in §8, Plane in §9).

```
sdtools/
├── sdtools/            the CLI
│   ├── cli.py          Typer app; tool subcommands generated from tools/
│   ├── discovery.py    tool.yaml manifest parsing
│   ├── runner.py       process lifecycle, log capture, heartbeat, digest
│   ├── protocol.py     the ::sdtools:: sentinel-line protocol
│   ├── telemetry.py    local NDJSON + batched upload + spool replay
│   ├── models.py       the wire contract (shared with the API)
│   └── config.py       ~/.sdtools/config.toml, machine identity
├── tools/              drop a directory here, get a subcommand
│   ├── las_info/       {tool.yaml, run.py}
│   └── tile_index/     {tool.yaml, run.py}
├── api/
│   ├── main.py         FastAPI ingest + query
│   ├── db.py           SQLAlchemy models (Postgres or SQLite)
│   ├── schema.sql      production Postgres DDL, indexes, partitions, views
│   ├── logreduce.py    200k log lines -> ~40, losslessly for errors
│   └── summarizer.py   the Haiku call
├── dashboard/index.html   reference dashboard (yours replaces it)
└── docker-compose.yml
```

## Quick start

**Windows** — extract this folder somewhere permanent (e.g. `C:\sdtools`), then:

```powershell
cd C:\sdtools
powershell -ExecutionPolicy Bypass -File .\install.ps1
# open a NEW PowerShell window:
sdtools list
```

The installer sets up uv, installs sdtools as an isolated tool with its own
Python 3.12 (your existing Python is untouched), and writes
`%USERPROFILE%\.sdtools\config.toml` pointing at this folder's `tools/`.
Note: `runtime: shell` tools need bash (Git Bash/WSL); `python` tools —
including everything `sdtools wrap` produces — run natively.

**Linux/macOS**:

```bash
pip install -e .                       # installs the `sdtools` command
sdtools list                           # what's discovered
sdtools doctor                         # config + API reachability
```

Run the API:

```bash
export POSTGRES_PASSWORD=… ANTHROPIC_API_KEY=…
docker compose up -d
docker compose logs api | grep -A1 "shown once"     # bootstrap admin key
```

Mint a per-machine ingest key and point the CLI at it:

```bash
curl -sX POST localhost:8080/v1/keys \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -d '{"name":"anik-laptop","scope":"ingest"}'

cat > ~/.sdtools/config.toml <<'TOML'
user = "anik"
email = "aanik@spatialdata.ai"
project = "sundre-2026"

[api]
url = "https://runs.internal.spatialdata.ai"
key = "sdt_inge_…"

[tools]
dir = "/opt/sdtools/tools"
TOML
```

Then use it:

```bash
sdtools tile-index --input /data/sundre --out idx.gpkg --project sundre-2026
sdtools tile-index --input /data/sundre --out idx.gpkg --json    # for Claude/CI
sdtools runs                                                     # recent, local
sdtools logs 4c927d40 --errors                                   # local log
sdtools flush                                                    # replay spool
```

Every setting has an env override: `SDTOOLS_API_URL`, `SDTOOLS_API_KEY`,
`SDTOOLS_TOOLS_DIR`, `SDTOOLS_USER`, `SDTOOLS_PROJECT`, `SDTOOLS_OFFLINE=1`.

## Environments (deterministic, per-tool)

A tool declares `environment: pointcloud`; the console builds that environment
from a committed lockfile before the tool runs (uv for Python stacks, pixi for
conda-forge/CUDA/PDAL/GDAL), caches it content-addressed, and records the env
digest on every run. Same lockfile → identical environment on every machine.

```bash
sdtools env list                 # locked? cached? digest
sdtools env lock pointcloud      # deps changed -> regen lockfile, commit it
sdtools env resolve pointcloud   # prebuild (first tool run does this anyway)
sdtools workflow nightly.yaml --local   # run a whole DAG on THIS machine, no API
```

Machines need `uv` (and `pixi` if you use conda envs) installed once; after
that everything resolves from lockfiles — a fresh worker self-provisions on
its first claimed job. `bash tests/verify_envs.sh` covers this (18 checks).

## Dispatching to multiple machines

On each processing machine (key scope `ingest`):

```bash
sdtools agent --queue default                      # a general worker
sdtools agent --queue heavy --labels bigmem,gpu    # the big box
```

From your computer (key scope `submit`):

```bash
sdtools submit tile-index -P input=/data/sundre -P out=/data/sundre/idx.json \
        --queue heavy --require bigmem             # one job
sdtools workflow docs/workflow.example.yaml \
        --set site=/data/sundre --watch            # a DAG of jobs
sdtools jobs                                       # queue state
sdtools agents                                     # fleet state
```

Semantics in one breath: ready jobs dispatch in `(priority, created_at, job_id)`
order per queue; a claim is a renewable lease, so a machine that dies mid-job
loses the lease and the job retries elsewhere (up to `max_attempts`); a
workflow step runs only after its `after:` dependencies are ok, and a failed
step cancels everything downstream of it while parallel branches continue.

## Wrapping an existing script or agent-skill folder

`sdtools wrap <folder>` scaffolds the whole integration from an existing
folder — including skill folders from agent workflows:

```bash
# on a machine that mounts the share:
sdtools wrap "\\\\SDAI-FS1\\Production\\Projects\\CLAUDE\\SDAI-PROD-AGENT-01\\Workflows\\Aecon_Project\\skills\\aecon_process"
sdtools env lock aecon_process        # pin the drafted deps, commit the lock
sdtools aecon-process --dry-run ...   # review, then run for real
```

It copies the folder into `tools/<name>/src/` (or `--in-place` to reference a
share), finds the entry script, statically reads its argparse flags into
`params:` (types, defaults, help — every guess marked for review), drafts
`envs/<name>/env.yaml` from requirements, auto-renames params that collide
with console options (keeping the child's flag via `flag:`), and explicitly
ignores SKILL.md/prompt files — the console runs code, not prompts. It never
executes the wrapped code.

## Adding one of your scripts

1. `mkdir tools/my_thing`, move the script in as `run.py` (any language works —
   set `runtime: shell` or `binary`).
2. Write `tool.yaml` — name, version, summary, and one `params` entry per flag.
3. `sdtools list` to confirm, `sdtools my-thing --dry-run …` to check the argv.

That's the whole integration. The script needs no knowledge of run ids, the API,
or uploads. To put a number on the dashboard, add one line:

```python
from sdtools.protocol import metric
metric("points_out", 18_452_331)
```

or from anything non-Python:

```bash
echo '::sdtools:: {"type":"metric","name":"points_out","value":18452331}'
```

## Configuration reference (API side)

| Env var | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./sdtools.db` | use `postgresql+psycopg://…` in production |
| `ANTHROPIC_API_KEY` | — | unset → deterministic template summaries |
| `SDTOOLS_SUMMARY_MODEL` | `claude-haiku-4-5` | set to the cheapest current Haiku id for your account |
| `SUMMARIZE_ALL` | `0` | `1` = summarize successes too, not just problems |
| `HEARTBEAT_GRACE_S` | `120` | silence before a run is marked `lost` |
| `EVENT_RETENTION_DAYS` | `45` | log-line retention |
| `CORS_ORIGINS` | `*` | set to your dashboard origin |

## Verification

```bash
bash tests/verify_e2e.sh              # 14 checks, boots its own API on SQLite
PYTHONPATH=. python3 tests/test_summarizer_shape.py   # model call shape, no tokens spent
ruff check sdtools api tools tests
```

`verify_e2e.sh` covers the paths that are easy to get wrong: running with no API
configured, spooling while the API is down and replaying afterwards, scope
separation (an ingest key must not be able to read), idempotent duplicate
ingest, eager-on-failure vs lazy-on-success summarisation, summary caching,
heartbeat → `lost`, and the SSE stream.

## Status of this repo

Verified working end to end on SQLite: tool discovery, dynamic subcommands, run
capture, gzip batched ingest, idempotent replay, spool-and-replay after an API
outage, heartbeat/`lost` detection, stats, SSE, lazy and eager summaries, and the
reference dashboard in both themes.

Not yet exercised: a real Haiku call (no API key was available in the build
environment — the call shape is covered by `tests/test_summarizer_shape.py`
against a stubbed SDK), and Postgres specifically (`schema.sql` is written but
the app was run against SQLite).

Dispatcher/Plane env (API side): `LEASE_TTL_S` (default 120), `SWEEP_INTERVAL_S`
(30), and for Plane: `PLANE_API_KEY`, `PLANE_WORKSPACE`, `PLANE_PROJECT_MAP`
(JSON of sdtools project → Plane project UUID, `"*"` = fallback),
`PLANE_BASE_URL` (self-hosted domain), `PLANE_RESOURCE` (`work-items`, or
`issues` on older self-hosted), `DASHBOARD_URL` (click-through link). The Plane
call shape is written against the published API reference but was not exercised
against a live Plane in this environment.

Dispatch verification: `bash tests/verify_dispatch.sh` — 26 checks: scope
separation, label/queue routing, DAG ordering, FIFO+priority determinism,
kill -9 → lease-expiry retry on another machine, upstream-failure cascade.
