# sdtools — architecture

A console tool for Spatialdata's stable processing scripts, where every run
reports itself to an API, a dispatcher pushes work from one computer to a fleet
of processing machines, and a cheap model turns the logs into something a
colleague can read.

Five components, deliberately loosely coupled — you can replace any one of them
without touching the others:

| # | Component | Runs where | Owns |
|---|-----------|-----------|------|
| 1 | **`sdtools` CLI** (Python + Typer) | operator machines, CI, Claude terminal | discovering tools, running them, capturing logs, uploading |
| 2 | **Run API** (FastAPI + Postgres) | one small server / container | ingest, storage, query, auth, retention |
| 3 | **Dispatcher** (part of the API) + **`sdtools agent`** | API + every worker machine | queue, leases, DAG workflows, routing (§8) |
| 4 | **Summarizer** (Haiku) | inside the API process, or its own worker | log → human-readable status note |
| 5 | **Dashboard** (yours) + optional **Plane sync** | browser / Plane | reading the API; project-level view (§9) |

The seam between 1 and 2 is the wire contract in `sdtools/models.py`. The seam
between 2 and 4 is the REST API. Both are versioned.

---

## 1. Data flow

```
  operator machine                          server                     browser
  ────────────────                          ──────                     ───────
  sdtools tile-index …
        │
        ├─ discover tools/*/tool.yaml
        ├─ build argv, spawn child process
        │
        ├── stdout/stderr ──► line capture ──► local NDJSON  (source of truth)
        │                          │           ~/.sdtools/runs/<run_id>.ndjson
        │                          │
        │                          ├─ ::sdtools:: lines → metrics/progress/artifacts
        │                          │
        │                          └─ batched, gzipped ──► POST /v1/runs
        │                             (background thread)  POST /v1/runs/{id}/events
        │                                     │            POST …/heartbeat
        │                             on failure           PATCH /v1/runs/{id}
        │                                     ▼                   │
        │                             ~/.sdtools/spool/           ▼
        │                             replayed next run     ┌──────────┐
        │                                                   │ Postgres │
        └─ exit code propagated to the shell                └──────────┘
                                                                 │
                                        run finished ──► summary_jobs
                                                                 │
                                                     reduce log (5000:1)
                                                                 │
                                                     Haiku, forced tool call
                                                                 │
                                                     run_summaries
                                                     keyed by log_digest ──► GET /v1/runs
                                                                             GET /v1/stream
```

### Why the local log is the source of truth

A field laptop on a hotel VPN will lose the API mid-run. If the CLI treated
upload as part of the job, a network blip would fail a two-hour job. So:

1. Every captured line is appended to `~/.sdtools/runs/<run_id>.ndjson` **first**,
   synchronously. That file is complete whether or not anything uploads.
2. Upload happens on a background thread from a bounded queue. If the queue
   fills, payloads spool to disk rather than blocking the tool.
3. Failed payloads go to `~/.sdtools/spool/`. Every subsequent `sdtools`
   invocation drains up to 200 of them; `sdtools flush` drains all.
4. `sdtools runs` and `sdtools logs <id>` read the local files, so the CLI is
   fully usable with the API down or absent (`SDTOOLS_OFFLINE=1`).

Ingest is **idempotent**: run ids are client-generated UUIDs and events carry a
per-run monotonic `seq` with a `UNIQUE (run_id, seq)` constraint. Replaying a
spooled batch is a no-op, so at-least-once delivery is safe.

---

## 2. The CLI

### Plugin discovery

Any directory under `tools/` with a `tool.yaml` becomes a subcommand at import
time. `sdtools --help` therefore always reflects what is on disk — adding a
script requires no CLI code change.

```yaml
# tools/tile_index/tool.yaml
name: tile-index                    # command name
version: 0.3.1                      # recorded on every run
summary: Build a tile index over a point-cloud directory.
group: pointcloud                   # grouping in `sdtools list`
runtime: python                     # python | shell | binary
entry: run.py                       # script in this dir
timeout_s: 600                      # hard kill -> status=timeout
writes: true                        # false = read-only, safe to auto-retry
tags: [production]
env: {PDAL_DRIVER_PATH: /opt/pdal}
params:
  - name: input
    type: path                      # str | int | float | bool | path
    required: true
    multiple: true                  # repeatable flag
    help: Directory of tiles.
  - name: workers
    type: int
    default: 4
  - name: api_token
    type: str
    secret: true                    # redacted from the uploaded cmdline
```

`type: path` params also feed `input_summary` (file count, total bytes,
extensions) via `stat()` only — no file contents are read, and none are uploaded.

Reserved param names, because the CLI adds them to every command:
`project`, `tag`, `dry_run`, `quiet`, `json_out`. A manifest that collides is
reported in `sdtools doctor` rather than crashing the CLI.

### The tool protocol

Tools are ordinary scripts in any language. A script you never modify still
works — it just reports less. To surface a number on the dashboard, print a
sentinel line:

```
::sdtools:: {"type":"metric","name":"points_out","value":18452331}
::sdtools:: {"type":"progress","value":0.42,"note":"tile 12/28"}
::sdtools:: {"type":"artifact","path":"out/t12.laz","kind":"laz","size_bytes":91234}
::sdtools:: {"type":"error","kind":"MissingCRS","message":"no CRS on input"}
::sdtools:: {"type":"field","key":"crs","value":"EPSG:2956"}
```

Python tools can `from sdtools.protocol import metric, progress, artifact, error, field`.
Everything else is one `echo` away. Sentinel lines are parsed into structured
columns and suppressed from console output; everything else is captured verbatim.

### What the runner guarantees

- Interleaved stdout + stderr capture with a monotonic `seq`, so ordering is
  recoverable server-side even with out-of-order delivery.
- Level inference (`error`/`warning`/`info`) from the line text, so filtering
  works without tool cooperation.
- A **heartbeat every 15s**. A run whose heartbeat stops for
  `HEARTBEAT_GRACE_S` is marked `lost` by the server — this is how you
  distinguish "still running" from "the laptop closed".
- `timeout_s` enforcement → status `timeout`; Ctrl-C → `cancelled` (with SIGTERM
  then SIGKILL, so the child gets a chance to clean up).
- A `log_digest`: sha256 of the log with timestamps, durations, and hex
  addresses normalised out. Identical reruns produce identical digests, which is
  what makes summary caching work.
- The tool's exit code is the CLI's exit code, so `&&` chains and CI still work.

### Driving it from a Claude terminal

`--json` makes every command machine-readable, which is what you want when
Claude is the caller:

```bash
$ sdtools tile-index --input /data/sundre --out idx.gpkg --project sundre-2026 --json
{
  "run_id": "…", "status": "ok", "exit_code": 0, "duration_ms": 142310,
  "metrics": {"tiles_in": 240, "tiles_indexed": 238, "tiles_skipped": 2},
  "counts": {"info": 5104, "warning": 17},
  "artifacts": [{"path": "idx.gpkg", "kind": "gpkg", "rows": 238}]
}
```

`--dry-run` prints the argv it would execute and exits — useful for letting a
model propose a command that a human approves before it runs.

---

## 2½. Environments — the console's determinism layer

"The script works on my machine" is really "my machine happens to have the
right GDAL/laspy/torch". So environments are first-class, named, and shared:
a tool declares `environment: pointcloud` in its manifest, and the console
guarantees the same interpreter and the same package tree on every machine —
or fails **before the tool starts**, with a recorded, attributable run
(`error_kind: EnvResolveFailed`) instead of a stack trace. Nothing in this
path is agentic: resolution is lockfiles and content addressing, end to end.

### Backends (current stack, checked Aug 2026)

| kind | engine | use for | lockfile |
|---|---|---|---|
| `uv` | [uv](https://docs.astral.sh/uv/) (Rust, replaces pip/venv/pyenv) | pure-Python stacks — laspy, numpy, shapely | `requirements.lock`, hash-pinned via `uv pip compile --generate-hashes` |
| `pixi` | [pixi](https://pixi.sh) (conda-forge ecosystem) | ML + native stacks — PyTorch/CUDA, PDAL, GDAL binaries | `pixi.lock`, installed with `--frozen` |
| `system` | none | tools with no deps beyond stdlib | — (default; the pre-environments behaviour) |

uv also *fetches the Python version itself* (`python: "3.12"` in the spec), so
worker machines don't even need the right Python preinstalled — just uv. pixi
covers the part pip can't do deterministically: CUDA toolkits and the C/C++
geospatial libraries, all from conda-forge.

### Layout — committed beside the tools, versioned together

```
envs/
  pointcloud/
    env.yaml            # kind: uv, python: "3.12", deps: [laspy[lazrs]>=2.5, …]
    requirements.lock   # generated once, reviewed, committed
  ml-torch/
    env.yaml            # kind: pixi
    pixi.toml           # conda-forge: pytorch, cuda, pdal, gdal…
    pixi.lock           # committed
tools/
  las_info/tool.yaml    # environment: pointcloud
```

### The two-layer determinism model

1. **The lockfile pins the world.** `sdtools env lock <name>` is the only step
   that ever sees unpinned versions; its output is reviewed and committed like
   code. Two machines with the same lockfile install byte-identical trees
   (uv verifies hashes; pixi installs `--frozen`).
2. **The prefix cache is content-addressed.** Cache key =
   `sha256(spec + lockfile + backend major)`. Prefixes live at
   `~/.sdtools/envs/<name>-<digest>` and are **immutable** — any change to the
   spec or lock produces a new digest and a fresh build; `sdtools env prune`
   collects orphans. Builds are atomic (build to `.partial`, rename) and
   guarded by a file lock, so two jobs landing on a cold machine trigger
   exactly one build (verified in `tests/verify_envs.sh`).

Every run records `(env_name, env_digest)` in telemetry, so the dashboard can
answer "which environment produced this output" — and a mystery regression can
be traced to the lockfile change that caused it.

Measured in this repo: cold build of the pointcloud env (laspy+numpy, hash-
verified) **1.0s** with uv; every later run resolves in ~10ms. Fleet effect:
worker machines need uv (and pixi, if used) installed once — after that,
dispatching a job to a fresh machine self-provisions the environment from the
lockfile on first claim. The dispatch test suite exercises exactly that.

### Console commands

```bash
sdtools env list                # name, kind, locked?, cached?, digest
sdtools env lock pointcloud     # deps changed -> regenerate lock, commit diff
sdtools env resolve pointcloud  # prebuild now instead of on first run
sdtools env prune               # drop prefixes no spec points at
```

### Running whole workflows in the console — no infrastructure

`sdtools workflow nightly.yaml --local` executes the same DAG file the
dispatcher accepts, entirely on the current machine: deterministic topological
order (Kahn's algorithm, ready steps sorted by key — same file, same sequence,
every time), each step through the normal runner with its environment
resolved, failed steps cancelling their downstream cone while independent
branches continue. A field laptop can run the full nightly pipeline offline;
the local NDJSON telemetry uploads whenever the API is next reachable.

One boundary honestly stated: the pixi backend is implemented and its command
sequence is test-verified through a shim, but pixi's installer was unreachable
from the build sandbox, so it has not run against a real conda-forge install
here. Validate `sdtools env resolve ml-torch` on one of your machines.

---

## 3. The API

### Endpoints

| Method | Path | Scope | Purpose |
|---|---|---|---|
| `GET` | `/v1/health` | any | liveness + running count |
| `POST` | `/v1/runs` | ingest | run started (202, idempotent) |
| `PATCH` | `/v1/runs/{id}` | ingest | run finished; enqueues summary |
| `POST` | `/v1/runs/{id}/events` | ingest | batched log lines (gzip) |
| `POST` | `/v1/runs/{id}/heartbeat` | ingest | liveness + progress |
| `POST` | `/v1/runs/{id}/artifacts` | ingest | output metadata |
| `GET` | `/v1/runs` | read | filtered list, summaries joined in |
| `GET` | `/v1/runs/{id}` | read | one run + artifacts + summary |
| `GET` | `/v1/runs/{id}/events` | read | paged log (`after_seq`, `level`) |
| `GET` | `/v1/runs/{id}/summary` | read | cached, or generate on demand |
| `GET` | `/v1/stats` | read | dashboard tiles |
| `GET` | `/v1/stream` | read | SSE feed of run state changes |
| `POST` | `/v1/keys` | admin | mint a key |

### Auth

API keys, `sha256`-hashed at rest, three scopes:

- **`ingest`** — one key per machine (`anik-laptop`, `proc-vm-01`, `ci`). Write
  only. Leaking one lets someone write junk runs, not read your data.
- **`read`** — the dashboard. Read only.
- **`admin`** — minting keys.

The API prints a bootstrap admin key on first boot. There is no user login: the
`actor` on a run is asserted by the CLI, not verified. That is the right trade
for an internal tool — this is visibility, not an audit log. If you later need
non-repudiation, put the API behind your SSO proxy and take `actor` from the
forwarded identity header instead of the payload.

### Storage

Full DDL in `api/schema.sql`. Five tables:

- **`runs`** — one row per invocation. Wide and denormalised on purpose: the
  dashboard's main query must be one index scan with no joins.
- **`run_events`** — the log lines. The only unbounded table, so it is
  **partitioned monthly by `ts`**; retention is `DROP PARTITION`, which is
  instant and doesn't bloat.
- **`run_artifacts`** — output metadata. Indexed on `sha256` so you can find
  runs that produced byte-identical outputs.
- **`run_summaries`** — keyed `UNIQUE (log_digest, prompt_version)`, not by run.
- **`summary_jobs`** — the work queue.

Indexes are chosen from the queries the dashboard actually issues: `started_at
DESC` alone and composed with `project`, `tool`, `actor_user`, `status`; a GIN
index on `tags` and `metrics`; a trigram index on `cmdline` for search; and a
**partial** index on `last_heartbeat_at WHERE status = 'running'` so the sweeper
never scans history.

Two views ship with the schema: `v_recent_runs` (what the dashboard table reads)
and `v_tool_health` (per-tool p95 duration and top error kind — the "what keeps
breaking" panel).

### Background loops

- **Sweeper** (30s): marks stale `running` rows as `lost`, enqueues their
  summaries, and drops events past `EVENT_RETENTION_DAYS`.
- **Summary worker**: drains `summary_jobs` with 3 attempts.

Both live in the API process today. At the point where that hurts, the worker
moves out to its own container reading the same table — no schema change.

---

## 4. The cheap-AI layer

This is where a naive implementation gets expensive and unhelpful at the same
time. A 40-minute PDAL job emits 200k lines that are 95% the same line with a
different number. Sending that raw is wasteful *and* produces a worse summary,
because the signal drowns.

### Log reduction, before any model call

`api/logreduce.py`, in order:

1. Drop protocol lines — already captured as structured metrics.
2. Keep **every** error and warning line, up to 120, then sample evenly.
3. Keep a 40-line head and 60-line tail for context.
4. Collapse the rest by *shape* (digits, paths, and hex normalised out), keeping
   one exemplar per shape annotated `(x1842 similar)`.
5. Collapse consecutive same-shape lines: `(x39 consecutive)`.
6. Mark every gap explicitly — `... 6253 lines elided ...` — so the model knows
   it is looking at a summary and does not infer completeness.
7. Hard character budget (default 20k chars ≈ 5k tokens).

Measured on a synthetic 240-tile PDAL log: **215,780 lines → 42 lines, ~440
tokens, with all 17 warnings and the fatal error preserved.**

### The model call

- Static system prompt marked `cache_control: ephemeral`, so it is served from
  prompt cache after the first call of the window.
- A **forced tool call** (`tool_choice: {type: tool, name: report}`) against a
  JSON schema — no prose preamble, no parsing, no "sure, here's the summary".
- `max_tokens: 700`.
- Facts (status, exit code, metrics, counts) are passed separately from the log
  and marked authoritative, so the model reports numbers rather than estimating
  them from log text.
- **No identity in the prompt.** Actor, hostname, and project are deliberately
  withheld: summaries are cached by log content, so a name in the prompt would
  leak one person onto another person's byte-identical run. The dashboard renders
  who/where from its own columns.

Output shape (`Summary` in `models.py`):

```json
{
  "headline": "Indexed 238 of 240 tiles; 2 skipped for missing CRS",
  "what_happened": "…2-4 sentences…",
  "outcome": "success_with_warnings",
  "severity": "low",
  "key_numbers": ["238 of 240 tiles indexed", "18.4M points"],
  "problems": ["2 tiles have no spatial reference and were skipped"],
  "next_steps": ["Set a CRS on tile_113 and tile_207, then rerun"],
  "confidence": "high"
}
```

`outcome` distinguishes `success` from `success_with_warnings` — exit code 0
with 17 warnings is not a clean run, and that distinction is most of the value.

### Cost control, in order of impact

1. **Cache on `(log_digest, prompt_version)`.** Reruns of the same job are free.
   Bumping `PROMPT_VERSION` invalidates everything intentionally.
2. **Reduce first** (~1000× fewer tokens).
3. **Prompt-cache the system block.**
4. **Lazy on success, eager on failure.** Failures, timeouts, and lost runs are
   summarized immediately; successful runs only when someone clicks. Set
   `SUMMARIZE_ALL=1` to change that.
5. **Graceful fallback.** With no `ANTHROPIC_API_KEY` or on a model error, a
   deterministic template summary is stored instead, so the dashboard never has
   an empty cell. `model` records which path produced it.

Estimate your own bill from the current published Haiku rates rather than any
number quoted here:

```
monthly ≈ runs_summarised
          × ( (~1.5k input tokens  × input_rate_per_Mtok  / 1e6)
            + (~0.3k output tokens × output_rate_per_Mtok / 1e6) )
```

With reduction plus digest caching, the token count per summary is roughly
constant regardless of how long the job ran — which is the point. Check the
current rates on Anthropic's pricing page and set `SDTOOLS_SUMMARY_MODEL` to the
cheapest current Haiku model id for your account.

---

## 5. The dashboard contract

`dashboard/index.html` is a working reference, not the product — it exists to
pin down the API contract and show what the summary field looks like rendered.
Build yours against the same three calls:

- `GET /v1/stats?hours=…` → the tiles and the two bar panels.
- `GET /v1/runs?since_hours=…&status=…&project=…` → the table, with `summary`
  already joined in. No N+1.
- `GET /v1/stream` (SSE) → live updates; each message is a full run object, so
  the client upserts by `run_id`.

For a live log tail, poll `GET /v1/runs/{id}/events?after_seq=N`.

Notes worth carrying into your build: run status uses the reserved status
palette with an icon and a label, never colour alone; the bar panels are
direct-labelled so no legend is needed; `timeout` and `lost` never sit adjacent,
because those two hues are close.

---

## 6. Operational choices, stated plainly

**What is uploaded:** command line (secrets redacted), parameters, log lines,
metrics, timings, output *paths* and sizes, and the operator's configured name.
**What is not:** file contents, credentials, or anything from outside the
captured streams. If a tool prints a secret, that lands in the log — so redact
in the tool, or mark the param `secret: true`.

**Failure modes and what happens:**

| Failure | Behaviour |
|---|---|
| API down | run proceeds; payloads spool; next run replays them |
| Laptop closes mid-run | server marks the run `lost` after the heartbeat grace |
| Duplicate delivery | dropped by `UNIQUE (run_id, seq)` |
| Broken `tool.yaml` | that one tool is skipped; `sdtools doctor` reports it |
| No `ANTHROPIC_API_KEY` | template summaries; everything else unaffected |
| Model call fails | 3 attempts, then a template summary |
| Event table growth | monthly partitions dropped past retention |

**Scaling.** One Postgres and one API container handle this workload for a long
time — the write path is a few hundred rows per run. The first thing to hurt is
`run_events` on very chatty tools; the levers, in order: raise
`batch_lines`, drop `debug` events client-side, shorten
`EVENT_RETENTION_DAYS`, then move event storage to object storage with only
error/warning lines in Postgres. The summary worker moves out of the API process
before any of that becomes necessary.

---

## 7. Build sequence

Each step is independently useful — you get value at step 2, not step 5.

1. **CLI + local logging only.** No server. Wrap two or three stable scripts,
   get the team using `sdtools` instead of calling scripts directly. This is
   also the step that shakes out your manifest params.
2. **API + Postgres, ingest only.** Point one machine at it. Confirm runs
   appear, kill the API mid-run and confirm the spool replays. Now you have
   team visibility via `GET /v1/runs` and `curl`.
3. **Dashboard.** Table + tiles from `/v1/stats` and `/v1/runs`. Still no AI.
4. **Add the summarizer.** Failures only (`SUMMARIZE_ALL=0`). Read a week of
   real summaries, then tune the system prompt and bump `PROMPT_VERSION`.
5. **Add `::sdtools::` metric lines** to your highest-value scripts. This is
   where the dashboard stops being a log viewer and starts answering "how much
   did we process this week".
6. **Then**: SSE live view, `v_tool_health` panel, alerting off `error_kind`.

The one thing worth getting right early is the **manifest and protocol format**,
because it is what every script depends on. Everything downstream — storage,
model, dashboard — is replaceable.

---

## 8. The dispatcher — one computer submits, many machines execute

Telemetry (§1–3) answers "what ran". The dispatcher answers "run this
somewhere" — you queue work from your computer, and agents on the processing
machines pull and execute it. It is **pull-based on purpose**: the server never
needs to reach into a worker (no SSH, no open inbound ports on field machines,
works from behind any NAT/VPN), a dead machine simply stops polling, and adding
capacity is "start one more agent".

### Why a Postgres queue and not Celery/Prefect/Temporal

At this scale (one team, tens of machines, thousands of jobs a day) a queue in
the same Postgres as the telemetry is the deterministic option, not the naive
one:

- **One source of truth.** A job, its run, its log lines, and its AI summary
  join on ids in one database. No broker state to drift out of sync with run
  state; nothing to reconcile after an outage.
- **Determinism is a stated total order**, not an emergent property of broker
  internals: ready jobs dispatch in `(priority, created_at, job_id)` order
  within a queue. Two identical submissions dispatch identically. You can read
  the entire scheduling policy in one function (`api/dispatch.py::poll`).
- **Exactly-one claimant** comes from a guarded UPDATE (`WHERE state='queued'`)
  — the database's own atomicity, no distributed locking. Under heavy
  contention on Postgres this upgrades to `FOR UPDATE SKIP LOCKED` without any
  API change.
- Celery adds a broker and gives you less visibility than you're building;
  Prefect/Dagster orchestrate Python functions, but your units of work are
  versioned CLI tools on specific machines; Temporal is the right call at 100×
  this complexity. If you outgrow this queue, the wire contract (submit / poll
  / lease / finish) maps 1:1 onto any of them — the CLI and agents survive.

### Execution semantics (the guarantees, exactly)

1. **A claim is a lease, not ownership.** `LEASE_TTL_S` (default 120s). The
   agent renews it while the tool runs. Kill the machine — power cord, OOM,
   `kill -9` — and the lease expires, the sweeper re-queues the job, and
   another machine takes it. Verified in `tests/verify_dispatch.sh`: kill -9
   mid-job → completed on a second machine, exactly 2 attempts recorded.
2. **At-least-once, exactly-one-live-executor.** A presumed-dead agent that
   comes back and tries to touch its old job gets 409 (lease is held by
   someone else) and stands down. Consequence: tools should be idempotent per
   output path (yours overwrite outputs, which qualifies); for ones that
   aren't, set `max_attempts: 1`.
3. **Attempts are capped** (`max_attempts`, default 2). The failed attempts'
   runs remain in telemetry — a retried job has two runs, both visible, both
   summarizable.
4. **Routing is queue + labels.** An agent polls named queues
   (`--queue heavy --queue default`) and advertises labels
   (`--labels bigmem,gpu`) plus its installed tools; a job states its queue
   and `require_labels`. First label-satisfying job in the total order wins.
5. **DAG workflows.** A YAML file of steps with `after:` dependencies becomes
   jobs at submission (cycles rejected with 422). A step is offered to agents
   only when every dependency is `ok`. A permanently failed step cancels its
   downstream cone (`UpstreamFailed`) — parallel branches keep running.
   `${params.x}` in step params substitutes from workflow-level params, so one
   file serves every site: `sdtools workflow nightly.yaml --set site=/data/x`.

### The moving parts

```
operator computer                     API                        worker machines
─────────────────                     ───                        ───────────────
sdtools workflow nightly.yaml   POST /v1/workflows          sdtools agent --queue heavy
  --set site=/data/sundre         └─ expand DAG -> jobs         --labels bigmem
  --watch                            (blocked until deps ok)         │
sdtools submit tile-index …     POST /v1/jobs                        │ poll (2s)
sdtools jobs / agents           GET  /v1/jobs|agents|workflows       ▼
                                POST /v1/agents/poll  ◄──── claim: guarded UPDATE,
                                                             (priority, created_at, id)
                                POST /v1/jobs/{id}/start ◄── run_id links job -> run
                                POST /v1/jobs/{id}/lease ◄── renewal thread
                                POST /v1/jobs/{id}/finish ◄─ ok | retry | fail
                                  └─ promote dependents / cancel cone /
                                     finalize workflow -> Plane
sweeper (every SWEEP_INTERVAL_S): expire leases -> requeue or fail
                                  mark silent agents offline
```

The agent executes through the **same runner** as an interactive run, so every
dispatched job gets the full telemetry treatment — local NDJSON first, batched
upload, spool on API outage, heartbeat, digest, summary. Dispatched runs carry
`job:<id>` and `agent:<id>` tags, so the dashboard's run view and the queue
view are two lenses on the same data.

Key scopes extend accordingly: **`submit`** (operators — can also read),
**`ingest`** (worker machines — can also poll/claim; still cannot read
history), `read`, `admin`.

### What this deliberately does not do (yet)

Cross-step data passing (step B reading step A's metrics) — steps share state
through the filesystem paths you give them, which is how your tools already
work. Fan-out ("one job per tile") — submit N jobs from a loop today; a
`foreach:` step is the natural extension. Cron — trigger `sdtools workflow`
from cron/CI on the operator machine, or add a `schedules` table later.

## 9. Plane on the dashboard — the project view

Plane (github.com/makeplane/plane, self-hostable) is where the team already
tracks *projects*; sdtools tracks *runs*. The integration (`api/plane.py`)
keeps that separation instead of mirroring every job into Plane:

- **Workflow submitted** → a work item `[run] sundre-nightly` is created in the
  mapped Plane project (`PLANE_PROJECT_MAP` maps sdtools project names →
  Plane project UUIDs), with a click-through link to the sdtools dashboard.
- **Workflow finished** → the item is renamed `[run ✓ ok]` / `[run ✕ failed]`
  (failed runs get priority `high`) and a comment lands with the job tally and
  the first failing steps' error kinds.

Auth is Plane's `X-API-Key` token; endpoints verified against the published
API reference (Aug 2026): `POST/PATCH /api/v1/workspaces/{ws}/projects/{id}/
work-items/` and `.../comments/`. Older self-hosted releases used `issues` in
the path — set `PLANE_RESOURCE=issues` for those. Plane being down never
affects dispatch: every call is fire-and-forget with a logged warning.

One judgment call worth stating: don't put run-by-run noise into Plane. PMs
get one item per workflow with its outcome; engineers click through to the
sdtools dashboard for logs, metrics, and machine detail. If you later want
Plane items to *drive* work ("rerun failed workflow" from a Plane comment),
that's a webhook receiver on the API — the schema already links
`workflows.plane_issue_id` both ways.

### Build sequence, continued

7. **Dispatcher**: start one agent on one processing machine, `sdtools submit`
   from yours. No workflow files yet.
8. **Workflows**: move your nightly multi-step sequences into YAML DAGs;
   `--watch` in CI.
9. **Plane sync**: set the four `PLANE_*` env vars once workflows are the unit
   of work your team talks about.
